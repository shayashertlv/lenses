"""S10 ``s10_gate``: score any model-frame mesh or GLB against the photos through the frozen S3 cameras.

Library: ``evaluate(product, models, run)`` with ``models`` = {name: (V_mm, F, part_labels) | GLB path |
Model}; stage: ``run(product, run='m1', force=False)`` scores the available candidates (the BSA model when
S9/S6 exist, the previous route's ``candidate.glb`` aligned into the model frame, the tilted-card control,
the canonical generator as an in-sample reference, and fault-injected copies of the previous candidate),
writes ``s10_gate/result.json`` + ``arrays.npz`` (card mesh) + ``sheet.png`` and returns the result.

Cameras. The S3 cameras are frozen; each (model, view) gets the same bounded refine: yaw/pitch/roll
+/-3 deg, magnification at the S3 pivot +/-2 %, pivot image position +/-2 % of the matte width,
perspective fixed; Powell on the S3 loss (soft IoU + boundary, ``cameras.Level``) at ~512 px glasses
width, ``REFINE_MAXFEV`` evaluations for every model, on the FULL silhouette (a temple-masked refine made the
same mesh score 0.68 vs 0.25 %W on vb; see ``TEMPLE_MASKED_REFINE_VIEWS``), so a temple error can reach the
front-piece score through the bounded refine. The angled view gets a camera refine like the others and is
otherwise only judged: nothing here is chosen or tuned on it.

Metrics per view (native pixels, converted with the local px/mm at the S1 front-piece centre):
- ``front_piece``: symmetric contour distance of the hole-filled silhouettes (render vs S0 matte) with the
  model's temples masked (mean mm, p95 mm, mean in % of the front width W) and the masked IoU. The
  temple mask is computed per model from its own geometry behind the front-piece slab
  (z < z_max - S1 front_depth_mm): the part of that projection lying outside the front-piece projection,
  dilated ``TEMPLE_DILATE_MM``, plus every pixel nearer to it than to the front-piece projection (the
  S3 ``front_piece_scale`` rule, so a photo temple at a different hinge angle cannot leak into the
  front-piece score). ``front_piece_common`` also masks the generator's temple region (same for every
  model; isolates the front shape of a model without temples, e.g. the card).
- ``temple``: the same contour distance restricted to the union of the two temple regions (side views).
- ``iou_full``: plain silhouette IoU (diagnostic).
Lens metrics (on the S2 outline-source photo; GT on every traced photo): the visible lens region is the
set of pixels whose first hit is a lens face, rendered at 2x; its outer boundary is compared with the S2
outline (all point types) and with the M0 ground truth (frame-bounded, non-occluded segments), both
directions, mm through the local px/mm at the lens (``s2_frozen_camera``: the same through the unrefined
camera). Seam: sliver holes (<= 1 mm wide) in the front render at 10 px/mm that touch the lens silhouette
where the front photo shows material (see ``seam_metrics``). Head phantom: area share of faces with
|x| < 0.3 W more than 0.15 W behind the front. Contract: ``bsa.contract.check`` when a GLB is given.

Decision (``finalize``, re-runnable on a saved result with ``--refinalize``): M1 criteria c1-c4 for the BSA
model, READY / RETRY / REVIEW (``decide``), the plan's initial gates, and the gate's own validation (card
control and fault injection, ``validation``).
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw
from scipy import ndimage, optimize

from reconstruction.camera import Camera, project

from . import cameras as C
from . import contract, generator, raster
from .core import (PRODUCTS, VIEWS, HELD_OUT_VIEWS, NormFrame, load_photo, run_dir, stage_dir)

STAGE = "s10_gate"
FRAME, TEMPLE, LENS = 0, 1, 2
LABEL_NAMES = {"frame": FRAME, "temple": TEMPLE, "lens": LENS}

REFINE_BOUNDS = {"scale_pct": 2.0, "rotation_deg": 3.0, "translation_pct_width": 2.0}
REFINE_LEVEL_PX = 512.0
REFINE_MAXFEV = 150
ROI_MARGIN = 0.2                 # per-view ROI: matte bbox U generator projection, + 20 % of its width
TEMPLE_DILATE_MM = 1.5
LENS_SS = 2                      # lens edge renders at 2x native
LENS_MIN_AREA_MM2 = 2.0          # visible-lens components below this are ignored (specks through cracks)
SEAM_PX_PER_MM = 10.0
SEAM_TOUCH_PX = 2
SEAM_MIN_COMPONENT_PX = 2        # 1-px holes are float32 ray cracks on shared edges (S1 report)
SEAM_MAX_WIDTH_MM = 1.0          # a seam gap is a sliver: hole components wider than this are other holes
PHANTOM_X_FRAC, PHANTOM_Z_FRAC = 0.30, 0.15
CARD_THICKNESS_MM = 3.0
ALIGN_SAMPLES = (20000, 60000)
ALIGN_MAX_ROT_DEG = 3.0
CRIT2_MM = 0.3
CRIT3_PCT_W = 0.1
PLAN_GATES = {"angled_front_piece_pct_w_max": 1.2, "lens_edge_mm_max": 0.4, "phantom_pct_max": 1.0}
# Flags that send a product to REVIEW: a human must look because the result cannot be verified automatically or
# is risky, and an automatic retry would not change that (``decide``). Each rule, by flag:
REVIEW_RULES = {
    "bsa_model_missing": "no BSA model to score (S6/S9 missing or unreadable)",
    "rimless_low_confidence": "S2: rimless lens whose faint edge ridge supports < 50 % of the rimless points; the outline "
                              "is barely measured and has no frame-bounded segment that c2 could verify",
    "low_contrast_high": "S2: > 60 % of the frame-bounded points were neither measured nor registered to the rim; the "
                         "outline there is the detector's plus the median bevel, i.e. unmeasured",
    "lens_count_disagree": "S2: the front and back detectors see a different number of lenses; the pair/single layout "
                           "is uncertain",
    "back_registration_failed": "S2: the back photo could not be registered, so the outline comes from a < 600 px front "
                                "photo",
    "lens_components": "S2: neither one nor two lens outlines survived symmetrisation (flag lens_components_<n>)",
    "lens_views_inconsistent": "S10: the S2 outline lifted onto the S4 lens surface and projected through the frozen S3 "
                               "camera of the other source view disagrees with that view's lens by more than "
                               "VIEW_CONSISTENCY_K x the product's measured noise (``lens_view_consistency``)",
    "gate_validation_failed": "S10: the gate's own validation (card control / primary fault checks) failed on this "
                              "product, so its scores cannot be trusted here",
    "previous_alignment_residual_high": "S10: the previous candidate could not be aligned to the generator (median "
                                        "residual > 0.5 mm), so the c3 comparison has no reliable baseline",
    "ar_fit_failed": "S7: the AR harness produced no material-fit renders; the frame keeps the pre-M1 ORM rule (the pale "
                     "specular wash), which is known to look wrong",
    "mirror_calibration_failed": "S8: the AR harness produced no mirror-calibration renders; the coat reflectance is the "
                                 "unscaled photo measurement, not verified in the runtime",
    "donor_projection_failed": "S5: a donor projection crossed a camera's near plane and its rule fell back (no lens-plate "
                               "removal, no footprint, no grazing test or no hull carve): the donor geometry is unverified",
    "lens_colour_mismatch": "S8: the delivered lens rendered in the actual AR runtime (front view, the photo's backdrop "
                            "colour) differs from the front photo's lens by more than LENS_COLOUR_DE00_MAX (mean dE00 "
                            "over the lens-local height bands): a wrong lens colour is never READY",
    "lens_colour_check_failed": "S8: the AR harness produced no lens-check render, so the lens colour is unverified",
    "lens_colour_unchecked": "S8 carries no rendered lens-colour check (a result written before the check existed)",
    "no_lens_edge_criterion": "S10: c2 is not applicable (the ground truth has no frame-bounded segment, e.g. a rimless "
                              "lens), so no criterion verifies the lens outline; READY would rest on c3 alone",
    "floating_part": "S10 integrity: a part of the delivered model floats free of the rest (a connected component > "
                     "FLOAT_GAP_MM from every other component, >= FLOAT_MIN_AREA_MM2): detached hardware or a sliver",
    "rough_silhouette": "S10 integrity: the front piece's silhouette in a synthetic off-axis view (yaw 35 / roll 25, no "
                        "photo) is rougher than the generator's by more than ROUGHNESS_MAX_RATIO (crumpled rims, fins)",
}
# the stage flags (S5, S7, S8) the decision reads besides S2's and the gate's own; S8's rendered lens colour is read by
# ``lens_colour`` (``LENS_COLOUR_DE00_MAX``)
STAGE_REVIEW_FLAGS = {"s5_temples": ("donor_projection_failed",), "s7_texture": ("ar_fit_failed",),
                      "s8_lens": ("mirror_calibration_failed", "lens_colour_check_failed")}
# The lens-colour check (``lens_colour``): S8's rendered lens vs the front photo, mean dE00 over the lens-local height
# bands. 8 dE00 is the difference between neighbouring colour names: INVU's pale-teal render of its sky-blue lens (the
# reviews' "wrong lens") scored 9.0, oakley's teal render of its violet/jade shield 14.2; lenses that read right score
# 0.7 (miu clear), 0.8 (vb gradient), 1.1 (rayban tint) and ~5 (INVU after the coat fit). Above it a shopper sees
# another lens (oakley after the fit, ~10: violet sides right, its jade top band a vertical gradient the angle-only
# coat cannot carry).
LENS_COLOUR_DE00_MAX = 8.0
# model-only integrity checks (no photo, never the held-out view): ``integrity``
FLOAT_GAP_MM = 0.5
FLOAT_MIN_AREA_MM2 = 1.0
ROUGHNESS_VIEWS = (("yaw35", 35.0, 0.0, 0.0), ("yaw-35", -35.0, 0.0, 0.0), ("roll25", 0.0, 0.0, 25.0))
ROUGHNESS_SIGMA_MM = 1.0
ROUGHNESS_MAX_RATIO = 1.5
# Flags that are recorded but never force REVIEW (they describe the product or a bounded, handled condition):
INFORMATIONAL_FLAGS = ("rimless_review", "refinement_clipped_high", "lens_mirror_iou_low", "lens_share_out_of_range",
                       "outline_from_back_mirrored", "front_low_resolution", "bevel_offset_unmeasured")
VIEW_CONSISTENCY_K = 2.0          # flag above 2 x the measured noise (no-fault values on the 5 products: 0.6-1.3 x)
VIEW_CONSISTENCY_FAULTS_MM = (-3.0, -2.0, -1.0, 1.0, 2.0, 3.0)   # sensitivity: the check on the S2 outline grown by these
FAULTS = ("temples_x085", "front_stretch_x105", "lens_dilate_1mm", "scale_x105", "card_lens_dilate_1mm")
# Views whose bounded refine uses the temple-masked loss (mask fixed at the frozen camera). Off: on vb it
# made the SAME mesh (previous candidate vs generator, 0.004 mm apart) score 0.68 vs 0.25 %W on the angled
# view, because the fixed coarse mask and the per-camera native mask disagree; the full-silhouette refine
# (S3's own loss) gives 0.315 vs 0.323 %W. Temple errors therefore reach the front-piece score through the
# refine (bounded to 2 % / 3 deg); see validation.unaffected_deltas.
TEMPLE_MASKED_REFINE_VIEWS: tuple[str, ...] = ()


# ============================================================================== models
@dataclass
class Model:
    """A mesh in MODEL millimetres with a part label per face (FRAME, TEMPLE, LENS)."""
    name: str
    V: np.ndarray
    F: np.ndarray
    labels: np.ndarray
    kind: str = "candidate"
    glb: str | None = None
    info: dict = field(default_factory=dict)

    def __post_init__(self):
        self.V = np.asarray(self.V, np.float64)
        self.F = np.ascontiguousarray(self.F, np.int32)
        self.labels = _labels(self.labels, len(self.F))
        if self.V.ndim != 2 or self.V.shape[1] != 3 or self.F.ndim != 2 or self.F.shape[1] != 3 or not len(self.F):
            raise ValueError(f"{self.name}: expected V (N,3) and F (M,3)")

    def sub(self, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        faces = np.asarray(faces, bool)
        return (self.V, self.F[faces]) if faces.any() else None

    @property
    def centroids(self) -> np.ndarray:
        return self.V[self.F].mean(axis=1)


def _labels(labels, n: int) -> np.ndarray:
    if labels is None:
        return np.zeros(n, np.int8)
    a = np.asarray(labels)
    if a.dtype.kind in "US":
        a = np.array([LABEL_NAMES[str(x)] for x in a], np.int8)
    a = a.astype(np.int8).ravel()
    if len(a) != n:
        raise ValueError(f"part_labels has {len(a)} entries for {n} faces")
    return a


def glb_parts(path) -> dict:
    """{V (m, raw), F, labels, nodes, origin_mm_model or None} of a GLB (lens = runtime lens rule)."""
    g = contract.read_glb(path)
    Vs, Fs, Ls, off, nodes = [], [], [], 0, []
    for n in g["nodes"]:
        for pm in n["primitives"]:
            if pm["mode"] != 4:
                continue
            f = pm["I"].reshape(-1, 3) + off
            lab = LENS if contract.is_lens_material(pm["material"]) else (
                TEMPLE if n["name"].lower().startswith("temple") else FRAME)
            Vs.append(pm["P"]); Fs.append(f); Ls.append(np.full(len(f), lab, np.int8))
            off += len(pm["P"])
            nodes.append({"name": n["name"], "faces": int(len(f)), "label": int(lab)})
    if not Vs:
        raise ValueError(f"{path}: no triangles")
    origin = (g["doc"].get("extras") or {}).get("bsa", {}).get("origin_mm_model")
    return {"V": np.vstack(Vs), "F": np.vstack(Fs), "labels": np.concatenate(Ls), "nodes": nodes,
            "origin_mm_model": None if origin is None else np.asarray(origin, float)}


def load_glb_model(name: str, path, gen: generator.Generator | None = None, kind: str = "candidate") -> Model:
    """A GLB in the model frame: a BSA export is placed exactly with its recorded ``origin_mm_model``;
    any other GLB (metres, +Z front) is aligned to the canonical generator by a similarity ICP."""
    p = glb_parts(path)
    info = {"nodes": p["nodes"]}
    if p["origin_mm_model"] is not None:
        V = p["V"] * 1000.0 + p["origin_mm_model"]
        info["placement"] = {"method": "bsa_origin_mm_model", "origin_mm_model": p["origin_mm_model"].tolist()}
    else:
        if gen is None:
            raise ValueError("A foreign GLB needs the generator to be aligned into the model frame")
        s, R, t, rep = align_to_generator(p["V"] * 1000.0, p["F"], gen)
        V = (s * (p["V"] * 1000.0) @ R.T) + t
        info["placement"] = {"method": "similarity_icp_to_generator", **rep}
    return Model(name, V, p["F"], p["labels"], kind, str(path), info)


def as_model(name: str, spec, gen: generator.Generator | None = None) -> Model:
    if isinstance(spec, Model):
        return spec
    if isinstance(spec, (str, Path)):
        return load_glb_model(name, spec, gen)
    V, F, labels = spec
    return Model(name, V, F, labels)


# ------------------------------------------------------------------------------ alignment
def sample_surface(V: np.ndarray, F: np.ndarray, n: int, seed: int) -> np.ndarray:
    """Area-weighted surface samples (deterministic for a seed)."""
    A = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    rng = np.random.default_rng(seed)
    f = rng.choice(len(F), n, p=A / A.sum())
    r1, r2 = rng.random(n), rng.random(n)
    s = np.sqrt(r1)
    return V[F[f, 0]] * (1 - s)[:, None] + V[F[f, 1]] * (s * (1 - r2))[:, None] + V[F[f, 2]] * (s * r2)[:, None]


def _closest(scene: o3d.t.geometry.RaycastingScene, P: np.ndarray) -> np.ndarray:
    ans = scene.compute_closest_points(o3d.core.Tensor(np.ascontiguousarray(P, np.float32)))
    return ans["points"].numpy().astype(np.float64)


def _mesh_scene(V: np.ndarray, F: np.ndarray) -> o3d.t.geometry.RaycastingScene:
    sc = o3d.t.geometry.RaycastingScene()
    sc.add_triangles(o3d.core.Tensor(np.ascontiguousarray(V, np.float32)), o3d.core.Tensor(np.ascontiguousarray(F, np.uint32)))
    return sc


def similarity_fit(P: np.ndarray, Q: np.ndarray, w: np.ndarray, rotation: bool) -> tuple[float, np.ndarray, np.ndarray]:
    """Weighted Umeyama: Q ~ s R P + t (R = I when ``rotation`` is False)."""
    w = w / w.sum()
    mp, mq = w @ P, w @ Q
    X, Y = P - mp, Q - mq
    varp = float(w @ (X * X).sum(1))
    if rotation:
        U, S, Vt = np.linalg.svd((Y * w[:, None]).T @ X)
        D = np.eye(3)
        D[2, 2] = np.sign(np.linalg.det(U @ Vt))
        R = U @ D @ Vt
        s = float(np.trace(np.diag(S) @ D) / varp)
    else:
        R = np.eye(3)
        s = float(w @ (X * Y).sum(1) / varp)
    return s, R, mq - s * R @ mp


def align_to_generator(V_mm: np.ndarray, F: np.ndarray, gen: generator.Generator, iters: int = 30) -> tuple:
    """Robust point-to-surface similarity ICP of a mesh (mm, +Z front, +Y up) onto the canonical
    generator (decimated). Scale + translation first, then a free rotation that is kept only when it is
    below ``ALIGN_MAX_ROT_DEG``. Returns (s, R, t, report) with residuals in mm both ways."""
    Vg, Fg = gen.Vd.astype(np.float64), gen.Fd
    gscene = _mesh_scene(Vg, Fg)
    P = sample_surface(V_mm, F, ALIGN_SAMPLES[0], 1)
    Q = sample_surface(Vg, Fg, ALIGN_SAMPLES[1], 2)
    s = float(np.ptp(Q[:, 0]) / np.ptp(P[:, 0]))
    R = np.eye(3)
    t = (Q.min(0) + Q.max(0)) / 2 - s * (P.min(0) + P.max(0)) / 2
    history = {}
    for rotation in (False, True):
        s1, R1, t1 = s, R, t
        for _ in range(iters):
            X = s1 * P @ R1.T + t1
            Y = _closest(gscene, X)
            d = np.linalg.norm(X - Y, axis=1)
            sig = 1.4826 * float(np.median(d)) + 1e-6
            w = 1.0 / np.maximum(1.0, d / (2.0 * sig)) ** 2
            w[d > np.percentile(d, 90)] = 0.0
            s1, R1, t1 = similarity_fit(P, Y, w, rotation)
        ang = math.degrees(math.acos(float(np.clip((np.trace(R1) - 1) / 2, -1, 1))))
        history[rotation] = (s1, R1, t1, ang)
    s, R, t, ang = history[True]
    used_rotation = ang <= ALIGN_MAX_ROT_DEG
    if not used_rotation:
        s, R, t, _ = history[False]
    X = s * P @ R.T + t
    d_cg = np.linalg.norm(X - _closest(gscene, X), axis=1)
    Vc = s * V_mm @ R.T + t
    d_gc = np.linalg.norm(Q - _closest(_mesh_scene(Vc, F), Q), axis=1)
    st = lambda d: {"median": float(np.median(d)), "mean": float(d.mean()), "p95": float(np.percentile(d, 95))}
    rep = {"scale": s, "rotation_deg": float(ang), "rotation_used": bool(used_rotation),
           "rotation_matrix": R.tolist(), "translation_mm": t.tolist(),
           "scale_no_rotation": history[False][0],
           "residual_candidate_to_generator_mm": st(d_cg), "residual_generator_to_candidate_mm": st(d_gc),
           "samples": list(ALIGN_SAMPLES), "note": "point-to-surface distances to the decimated generator (24k faces)"}
    return s, R, t, rep


# ------------------------------------------------------------------------------ S2 / card
def _s2(product: str, run: str) -> tuple[dict, dict]:
    sd = run_dir(run, product) / "s2_front"
    res = json.loads((sd / "result.json").read_text())
    arr = dict(np.load(sd / "arrays.npz", allow_pickle=False))
    return res, arr


def s2_view(s2res: dict) -> str:
    """The photo the S2 outline lives in: 'front', or 'back' for the mirrored back source."""
    return "back" if s2res["outline_source"] == "back_mirrored" else "front"


def s2_to_view_px(s2res: dict, P: np.ndarray, view_shape: tuple[int, int]) -> np.ndarray:
    """S2 outline-source px -> native px of ``s2_view`` (mirror u' = W - 1 - u for back_mirrored)."""
    P = np.asarray(P, float)
    if s2res["outline_source"] == "back_mirrored":
        return np.column_stack([view_shape[1] - 1 - P[:, 0], P[:, 1]])
    return P.copy()


def s2_lens_polys(s2res: dict, s2arr: dict, view_shape) -> list[np.ndarray]:
    return [s2_to_view_px(s2res, s2arr[f"lens{i}_poly"], view_shape) for i in range(1, len(s2res["lenses"]) + 1)]


def _grow_ring(P: np.ndarray, px: float) -> np.ndarray:
    """A closed ring offset outward by ``px`` (inward when negative; shapely buffer, round joins)."""
    import shapely
    g = shapely.Polygon(P).buffer(px, quad_segs=8)
    if g.geom_type != "Polygon":
        g = max(g.geoms, key=lambda q: q.area)
    return np.asarray(g.exterior.coords)[:-1]


def lens_view_consistency(product: str, run: str = "m1") -> dict:
    """Geometric front/back consistency of the S2 lens outline (replaces S2's 2D 'front_back_disagree', which compared
    raw detector outlines after a 2D similarity and so measured the parallax of a curved 3D frame, not an error).

    Each S2 outline (source px) is lifted through its source camera onto the S4 lens surface (``depth.lift_px``; the S4
    front surface where the lens surface misses) and projected through the frozen S3 camera of the OTHER source view
    (back for a front outline, front for a back_mirrored outline); it is compared with that view's detector lens
    proposal (S0, cleaned as in S2): symmetric mean contour distance in mm at the lens (``geo_mean_mm``).

    Noise, measured on the same product: ``detector_mm`` = the source-view detector's distance from the S2 outline (how
    far this detector sits from a measured edge on this product; the other view's detector is assumed as good),
    ``camera_mm`` = the other camera's S3 silhouette contour residual, ``pixel_mm`` = one pixel of the other photo at the
    lens; ``noise_mm`` = their root sum of squares. Flag ``lens_views_inconsistent`` when the mean over lenses exceeds
    VIEW_CONSISTENCY_K x noise. ``sensitivity`` re-runs the check on the S2 outline grown/shrunk by 1/2/3 mm; the
    smallest such error that would be flagged is ``detects_outline_error_mm`` (what the check can see on this product)."""
    from . import core, depth, front
    try:
        s2, a2 = stage_dir(run, product, "s2_front").load()
        s0, a0 = stage_dir(run, product, "s0_intake").load()
        frame, cams, s3 = C.load_cameras(product, run)
        df = depth.load_depth(product, run)
    except (FileNotFoundError, KeyError) as e:
        return {"available": False, "reason": f"{type(e).__name__}: {e}", "flag": False}
    mirrored = s2["outline_source"] == "back_mirrored"
    src_view, oth_view = ("back", "front") if mirrored else ("front", "back")
    mw = int(s2["source_shape"][1]) if mirrored else None
    cs_o = front.proposal_contours(front.clean_lens(a0[f"lens_{oth_view}"], a0[f"fg_{oth_view}"]))
    lens_s, fg_s = a0[f"lens_{src_view}"], a0[f"fg_{src_view}"]
    if mirrored:
        lens_s, fg_s = np.ascontiguousarray(lens_s[:, ::-1]), np.ascontiguousarray(fg_s[:, ::-1])
    cs_s = front.proposal_contours(front.clean_lens(lens_s, fg_s))
    out = {"available": True, "source_view": src_view, "other_view": oth_view, "k": VIEW_CONSISTENCY_K, "lenses": []}
    if not cs_o or not cs_s:
        out.update({"available": False, "reason": "no detector lens proposal in one of the views", "flag": False})
        return out

    def project(P, i):
        X = depth.lift_px(P, cams[src_view], frame, df.lens_surface(i), mirror_width=mw)
        miss = ~np.isfinite(X).all(1)
        if miss.any():
            X[miss] = depth.lift_px(P[miss], cams[src_view], frame, df.front_surface(), mirror_width=mw)
        ok = np.isfinite(X).all(1)
        return core.project_mm(X[ok], cams[oth_view], frame), X[ok], int((~ok).sum())

    def nearest(cs, P):
        c = front.poly_centroid(P)
        return min(cs, key=lambda q: float(np.hypot(*(front.poly_centroid(q) - c))))

    n = len(s2["lenses"])
    geo, det, ppm_o_all = [], [], []
    for i in range(1, n + 1):
        P = np.asarray(a2[f"lens{i}_poly"], float)
        uv, X, missed = project(P, i)
        if len(uv) < 16:
            out["lenses"].append({"lens": i, "error": "lift failed"})
            continue
        ctr = X.mean(0)
        ppm_o = C.px_per_mm_at(cams[oth_view], frame, ctr)
        ppm_s = C.px_per_mm_at(cams[src_view], frame, ctr)
        g_mean, g_p95 = front.contour_distance(uv, nearest(cs_o, uv))
        d_mean, _ = front.contour_distance(P, nearest(cs_s, P))
        rec = {"lens": i, "missed_lift": missed, "geo_mean_mm": round(g_mean / ppm_o, 3), "geo_p95_mm": round(g_p95 / ppm_o, 3),
               "detector_mm": round(d_mean / ppm_s, 3), "other_px_per_mm": round(ppm_o, 3)}
        sens = {}
        for f_mm in VIEW_CONSISTENCY_FAULTS_MM:
            uvf, _, _ = project(_grow_ring(P, f_mm * ppm_s), i)
            sens[f"{f_mm:+g}mm"] = round(front.contour_distance(uvf, nearest(cs_o, uvf))[0] / ppm_o, 3) if len(uvf) >= 16 else None
        rec["sensitivity_geo_mean_mm"] = sens
        out["lenses"].append(rec)
        geo.append(rec["geo_mean_mm"]); det.append(rec["detector_mm"]); ppm_o_all.append(ppm_o)
    if not geo:
        out.update({"available": False, "reason": "no lens could be lifted", "flag": False})
        return out
    cam = s3["cameras"][oth_view]
    camera_mm = float(cam["contour_mean_px"]) / float(cam["px_per_mm"])
    pixel_mm = 1.0 / float(np.mean(ppm_o_all))
    noise = float(np.sqrt(np.mean(det) ** 2 + camera_mm ** 2 + pixel_mm ** 2))
    g = float(np.mean(geo))
    out.update({"geo_mean_mm": round(g, 3), "noise_mm": round(noise, 3),
                "noise_terms_mm": {"detector": round(float(np.mean(det)), 3), "camera": round(camera_mm, 3), "pixel": round(pixel_mm, 3)},
                "ratio": round(g / noise, 3), "threshold_mm": round(VIEW_CONSISTENCY_K * noise, 3),
                "flag": bool(g > VIEW_CONSISTENCY_K * noise)})
    sens_ratio = {}
    for f_mm in VIEW_CONSISTENCY_FAULTS_MM:
        v = [l["sensitivity_geo_mean_mm"].get(f"{f_mm:+g}mm") for l in out["lenses"] if "sensitivity_geo_mean_mm" in l]
        v = [x for x in v if x is not None]
        sens_ratio[f"{f_mm:+g}mm"] = round(float(np.mean(v)) / noise, 3) if v else None
    out["sensitivity_ratio"] = sens_ratio
    detect = [abs(f) for f in VIEW_CONSISTENCY_FAULTS_MM if (sens_ratio.get(f"{f:+g}mm") or 0) > VIEW_CONSISTENCY_K]
    out["detects_outline_error_mm"] = min(detect) if detect else None
    return out


def front_plane(gen: generator.Generator) -> tuple[np.ndarray, float, dict]:
    """Robust plane z = a + b x + c y (Huber IRLS) through the front-most surface of the generator's front
    piece seen by an orthographic front camera. Returns (unit normal with n_z > 0, d with n.p = d, info)."""
    fr = gen.frame
    ppm = 3.0
    n_px = int(math.ceil(1.15 * fr.extent * ppm))
    cam = raster.view_camera(fr, 0.0, 0.0, ppm, (n_px, n_px))
    r = gen.scene(decimated=True).render(cam, (n_px, n_px), 1, want_points=True)
    fid = r["face_id"]
    zc = gen.Vd[gen.Fd].mean(axis=1)[:, 2]
    in_front = zc >= float(gen.result["front_z_mm"]) - float(gen.result["front_depth_mm"])
    ok = fid >= 0
    ok[ok] = in_front[fid[ok]]
    P = r["points"][ok]
    A = np.column_stack([np.ones(len(P)), P[:, 0], P[:, 1]])
    w = np.ones(len(P))
    coef = np.zeros(3)
    for _ in range(15):
        coef = np.linalg.lstsq(A * w[:, None], P[:, 2] * w, rcond=None)[0]
        res = P[:, 2] - A @ coef
        sig = 1.4826 * float(np.median(np.abs(res))) + 1e-6
        w = np.sqrt(1.0 / np.maximum(1.0, np.abs(res) / (1.5 * sig)))
    a, b, c = coef
    n = np.array([-b, -c, 1.0])
    n /= np.linalg.norm(n)
    res = P[:, 2] - A @ coef
    info = {"z_mm": [float(a), float(b), float(c)], "tilt_x_deg": math.degrees(math.atan(b)),
            "tilt_y_deg": math.degrees(math.atan(c)), "points": int(len(P)),
            "residual_mm": {"median_abs": float(np.median(np.abs(res))), "p95_abs": float(np.percentile(np.abs(res), 95))}}
    return n, float(n @ np.array([0.0, 0.0, a])), info


def lift_to_plane(cam: Camera, frame: NormFrame, uv: np.ndarray, n: np.ndarray, d: float) -> np.ndarray:
    """Model-mm points where the camera rays through native pixels ``uv`` meet the plane n.p = d."""
    O, D = raster.camera_rays(cam, uv[:, 0], uv[:, 1], 1.0)
    O_mm, D_mm = frame.to_mm(O), D * frame.extent
    t = (d - O_mm @ n) / (D_mm @ n)
    return O_mm + t[:, None] * D_mm


def _mask_polygons(mask: np.ndarray, min_hole_px: float = 20.0, simplify_px: float = 0.5):
    """Shapely polygons (with holes) of a bool mask; boundaries through pixel-edge midpoints."""
    import shapely
    from skimage import measure
    pad = np.pad(mask.astype(np.float32), 1)
    polys = []
    lab, n = ndimage.label(mask)
    for k in range(1, n + 1):
        comp = lab == k
        if comp.sum() < 20:
            continue
        filled = ndimage.binary_fill_holes(comp)
        cs = measure.find_contours(np.pad(filled.astype(np.float32), 1), 0.5)
        outer = max(cs, key=len)[:, ::-1] - 1.0
        holes = []
        hl, hn = ndimage.label(filled & ~comp)
        for h in range(1, hn + 1):
            hm = hl == h
            if hm.sum() < min_hole_px:
                continue
            hc = measure.find_contours(np.pad(hm.astype(np.float32), 1), 0.5)
            holes.append(max(hc, key=len)[:, ::-1] - 1.0)
        poly = shapely.Polygon(outer, holes).buffer(0)
        polys.append(poly.simplify(simplify_px, preserve_topology=True))
    del pad
    return shapely.union_all(polys) if polys else shapely.Polygon()


def _extrude(poly, lift, thickness: float, n: np.ndarray):
    """Closed prism of a shapely (Multi)Polygon: CDT caps lifted by ``lift`` (px -> mm), back cap offset
    by ``thickness`` along -n, walls on every ring. Returns (V, F)."""
    import shapely
    geoms = list(poly.geoms) if hasattr(poly, "geoms") else [poly]
    Vs, Fs, off = [], [], 0
    for g in geoms:
        if g.is_empty or g.area <= 0:
            continue
        tris = shapely.constrained_delaunay_triangles(g)
        T = np.array([shapely.get_coordinates(t)[:3] for t in tris.geoms])      # (K,3,2)
        rings = [np.asarray(g.exterior.coords)[:-1]] + [np.asarray(r.coords)[:-1] for r in g.interiors]
        allp = np.vstack([T.reshape(-1, 2)] + rings)
        key = np.round(allp * 1e6).astype(np.int64)
        uniq, inv = np.unique(key, axis=0, return_inverse=True)
        inv = inv.ravel()
        P2 = uniq.astype(np.float64) / 1e6
        nT = len(T) * 3
        Fcap = inv[:nT].reshape(-1, 3)
        front = lift(P2)
        back = front - thickness * n
        m = len(P2)
        Fg = [Fcap, Fcap[:, ::-1] + m]
        pos = nT
        for r in rings:
            idx = inv[pos:pos + len(r)]
            pos += len(r)
            a, b = idx, np.roll(idx, -1)
            Fg.append(np.column_stack([a, b, b + m]))
            Fg.append(np.column_stack([a, b + m, a + m]))
        Vs.append(np.vstack([front, back]))
        Fs.append(np.vstack(Fg) + off)
        off += 2 * m
    if not Vs:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int32)
    return np.vstack(Vs), np.vstack(Fs).astype(np.int32)


def tilted_card(product: str, gen: generator.Generator, cams: dict, run: str = "m1", lens_offset_mm: float = 0.0,
                name: str = "card") -> Model:
    """Negative control: the S2 frame region (symmetrised matte minus lenses) and lens polygons lifted
    through the S2 source camera onto the robust plane of the generator front, 3 mm thick, no temples.
    ``lens_offset_mm`` > 0 grows the lens polygons (the frame hole follows): a lens-outline fault."""
    import shapely
    s2res, s2arr = _s2(product, run)
    view = s2_view(s2res)
    cam = cams[view]
    shape = tuple(s2res["source_shape"])
    fg = s2arr["fg_sym"]
    if s2res["outline_source"] == "back_mirrored":
        fg = np.ascontiguousarray(fg[:, ::-1])
    fg_poly = _mask_polygons(fg)
    lens_polys = [shapely.Polygon(p).buffer(0) for p in s2_lens_polys(s2res, s2arr, shape)]
    if lens_offset_mm:
        ppm = C.px_per_mm_at(cam, gen.frame, C.front_piece_centre(gen))
        lens_polys = [lp.buffer(lens_offset_mm * ppm, quad_segs=8).simplify(0.2) for lp in lens_polys]
    frame_poly = fg_poly.difference(shapely.union_all(lens_polys))
    n, d, info = front_plane(gen)
    lift = lambda uv: lift_to_plane(cam, gen.frame, uv, n, d)
    parts = [(_extrude(frame_poly, lift, CARD_THICKNESS_MM, n), FRAME)]
    parts += [(_extrude(lp, lift, CARD_THICKNESS_MM, n), LENS) for lp in lens_polys]
    Vs, Fs, Ls, off = [], [], [], 0
    for (V, F), lab in parts:
        if not len(F):
            continue
        Vs.append(V); Fs.append(F + off); Ls.append(np.full(len(F), lab, np.int8)); off += len(V)
    info.update({"source_view": view, "thickness_mm": CARD_THICKNESS_MM, "lenses": len(lens_polys),
                 "frame_area_px": float(frame_poly.area), "lens_offset_mm": lens_offset_mm})
    return Model(name, np.vstack(Vs), np.vstack(Fs), np.concatenate(Ls), "control" if not lens_offset_mm else "fault",
                 None, {"plane": info})


# ------------------------------------------------------------------------------ other models
def generator_model(gen: generator.Generator, cams: dict, product: str, run: str) -> Model:
    """The canonical generator (decimated) with the S3 lens-face labelling (in-sample reference)."""
    s2res, s2arr = _s2(product, run)
    polys = [s2arr[f"lens{i}_poly_front"] for i in range(1, len(s2res["lenses"]) + 1)]
    ppm = C.px_per_mm_at(cams["front"], gen.frame, C.front_piece_centre(gen))
    shape = tuple(json.loads((run_dir(run, product) / "s0_intake" / "result.json").read_text())["views"]["front"]["shape"])
    lens = C.lens_faces(gen, cams["front"], shape, polys, C.LENS_ERODE_MM * ppm)
    return Model("generator", gen.Vd, gen.Fd, np.where(lens, LENS, FRAME).astype(np.int8), "reference", None,
                 {"source": "S1 decimated generator; lens faces labelled as in S3 (cameras.lens_faces)"})


def _provenance(files) -> dict:
    from .core import sha256_file
    out = {}
    for f in files:
        f = Path(f)
        if f.exists():
            out[f"{f.parent.name}/{f.name}"] = {"sha256": sha256_file(f), "mtime": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(f.stat().st_mtime))}
    return out


def bsa_model(product: str, run: str = "m1") -> Model | None:
    """The BSA model: s9_export/model.glb when exported, else the S6 front + S5 temples arrays; None if absent.
    A file that cannot be read (e.g. mid-write by its stage) raises; ``run`` records it as a flag."""
    rd = run_dir(run, product)
    glb = rd / "s9_export" / "model.glb"
    if glb.exists() and (rd / "s9_export" / "result.json").exists():
        m = load_glb_model("bsa", glb, None, "bsa")
        m.info["s9_result"] = str(rd / "s9_export" / "result.json")
        m.info["provenance"] = _provenance([glb, rd / "s9_export" / "result.json"])
        return m
    s6 = rd / "s6_assembly"
    if not (s6 / "result.json").exists() or not (s6 / "arrays.npz").exists():
        return None
    a = dict(np.load(s6 / "arrays.npz", allow_pickle=False))
    parts = [("frame_V", "frame_F", FRAME)]
    parts += [(f"lens{i}_V", f"lens{i}_F", LENS) for i in range(1, 5) if f"lens{i}_V" in a]
    t = {}
    s5 = rd / "s5_temples"
    files = [s6 / "result.json", s6 / "arrays.npz"]
    if (s5 / "arrays.npz").exists():
        t = dict(np.load(s5 / "arrays.npz", allow_pickle=False))
        files += [s5 / "result.json", s5 / "arrays.npz"]
        parts += [(f"temple_{s}_V", f"temple_{s}_F", TEMPLE) for s in "RL" if f"temple_{s}_V" in t]
    Vs, Fs, Ls, off = [], [], [], 0
    for kv, kf, lab in parts:
        src = a if kv in a else t
        if kv not in src:
            continue
        V, F = np.asarray(src[kv], float), np.asarray(src[kf], np.int64)
        Vs.append(V); Fs.append(F + off); Ls.append(np.full(len(F), lab, np.int8)); off += len(V)
    if not Vs:
        return None
    s5acc = {}
    if (s5 / "result.json").exists():
        r5 = json.loads((s5 / "result.json").read_text())
        s5acc = {k: {"accepted": v.get("accepted"), "reason": v.get("reason")} for k, v in r5.items()
                 if isinstance(v, dict) and "accepted" in v}
    return Model("bsa", np.vstack(Vs), np.vstack(Fs), np.concatenate(Ls), "bsa", None,
                 {"source": "s6_assembly arrays" + (" + s5_temples" if t else " (no temples)"),
                  "s5_temples_accepted": s5acc, "provenance": _provenance(files)})


def slab_cut_z(model: Model, gen: generator.Generator) -> float:
    """z below which a face is 'behind the front-piece slab': the model's own front-most z minus the S1
    front-piece depth."""
    return float(model.V[:, 2].max()) - float(gen.result["front_depth_mm"])


def dilate_lens_parts(model: Model, offset_mm: float, bins: int = 90) -> tuple[np.ndarray, dict]:
    """Grow every connected lens part radially in x/y about its own centroid so that its outline moves out by
    ``offset_mm`` (radial; along the outline normal where the outline is round). Only vertices used by lens
    faces alone move. Returns (V, info)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    V = model.V.copy()
    lens = model.labels == LENS
    if not lens.any():
        return V, {"components": 0}
    other = np.zeros(len(V), bool)
    other[model.F[~lens].ravel()] = True
    FL = model.F[lens]
    n = len(V)
    e = np.concatenate([FL[:, [0, 1]], FL[:, [1, 2]], FL[:, [2, 0]]])
    g = coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n))
    _, comp = connected_components(g, directed=False)
    used = np.zeros(n, bool)
    used[FL.ravel()] = True
    moved, ncomp = 0, 0
    for c in np.unique(comp[used]):
        idx = np.nonzero(used & (comp == c))[0]
        if len(idx) < 10:
            continue
        ncomp += 1
        P = V[idx, :2]
        ctr = 0.5 * (P.min(0) + P.max(0))
        d = P - ctr
        r = np.hypot(d[:, 0], d[:, 1])
        th = np.arctan2(d[:, 1], d[:, 0])
        b = np.floor((th + np.pi) / (2 * np.pi) * bins).astype(int) % bins
        R = np.zeros(bins)
        np.maximum.at(R, b, r)
        ok = R > 0
        if ok.sum() < 3:
            continue
        xs = np.nonzero(ok)[0]
        R = np.interp(np.arange(bins), xs, R[ok], period=bins)
        R = np.maximum(R, np.roll(R, 1))                  # conservative across bin edges
        Rv = np.interp((th + np.pi) / (2 * np.pi) * bins - 0.5, np.arange(bins), R, period=bins)
        k = 1.0 + offset_mm / np.maximum(Rv, 1.0)
        mv = ~other[idx]
        V[idx[mv], :2] = ctr + d[mv] * k[mv, None]
        moved += int(mv.sum())
    return V, {"components": ncomp, "moved_vertices": moved, "offset_mm": offset_mm}


def _lens_ring_faces(scene: raster.RasterScene, labels: np.ndarray, cam: Camera, shape, gen, ring_mm: float) -> np.ndarray:
    """Frame faces seen (first hit, ~10 px/mm) within ``ring_mm`` outside the visible lens region."""
    ppm = C.px_per_mm_at(cam, gen.frame, C.front_piece_centre(gen))
    k = max(1.0, SEAM_PX_PER_MM / ppm)
    roi = _hull_roi(scene, cam, shape, 0.03)
    cz = C.resized_camera(cam, k, roi[0], roi[1])
    shp = (int(math.ceil((roi[3] - roi[1]) * k)), int(math.ceil((roi[2] - roi[0]) * k)))
    fid = scene.render(cz, shp, 1)["face_id"]
    lens_vis = np.zeros(fid.shape, bool)
    lens_vis[fid >= 0] = labels[fid[fid >= 0]] == LENS
    ring = (cv2.distanceTransform((~lens_vis).astype(np.uint8), cv2.DIST_L2, 5) <= ring_mm * ppm * k) & ~lens_vis & (fid >= 0)
    faces = np.unique(fid[ring])
    return faces[labels[faces] == FRAME]


def make_faults(base: Model, gen: generator.Generator, cams: dict, shapes: dict) -> dict[str, Model]:
    """Fault-injected copies of ``base`` (DESIGN M2 list, subset):
    temples_x085: everything behind the slab compressed to 85 % of its length (z only, continuous at the cut);
    front_stretch_x105: front piece stretched 5 % in x, temples shifted rigidly with the endpieces;
    lens_dilate_1mm: every lens part grown 1 mm at its outline (free/rimless edges move), then the frame faces
      seen within 1 mm outside the visible lens in the front and back views relabelled lens (frame-bounded
      edges move);
    scale_x105: the whole model scaled 5 % about the front-piece centre (the camera may absorb 2 %)."""
    out = {}
    zc = slab_cut_z(base, gen)
    V = base.V.copy()
    behind = V[:, 2] < zc
    V[behind, 2] = zc + 0.85 * (V[behind, 2] - zc)
    out["temples_x085"] = Model("temples_x085", V, base.F, base.labels, "fault", None, {"cut_z_mm": zc})
    V = base.V.copy()
    h = float(np.max(np.abs(base.V[base.V[:, 2] >= zc, 0])))
    x = V[:, 0]
    V[:, 0] = np.where(V[:, 2] >= zc, 1.05 * x, x + 0.05 * np.clip(x, -h, h))
    out["front_stretch_x105"] = Model("front_stretch_x105", V, base.F, base.labels, "fault", None,
                                      {"cut_z_mm": zc, "half_width_mm": h})
    if (base.labels == LENS).any():
        Vd, dinfo = dilate_lens_parts(base, 1.0)
        scene = raster.RasterScene(Vd, base.F, gen.frame)
        lab = base.labels.copy()
        per_view = {}
        for view in ("front", "back"):
            if view not in cams or view not in shapes:
                continue
            faces = _lens_ring_faces(scene, base.labels, cams[view], shapes[view], gen, 1.0)
            lab[faces] = LENS
            per_view[view] = int(len(faces))
        out["lens_dilate_1mm"] = Model("lens_dilate_1mm", Vd, base.F, lab, "fault", None,
                                       {"relabelled_faces": per_view, **dinfo})
    c = C.front_piece_centre(gen)
    out["scale_x105"] = Model("scale_x105", c + 1.05 * (base.V - c), base.F, base.labels, "fault", None,
                              {"about_mm": c.tolist()})
    return out


# ============================================================================== measurement
def _hull_roi(scene: raster.RasterScene, cam: Camera, shape, margin: float, extra=None) -> tuple[int, int, int, int]:
    pts = raster._project_norm(scene.hull, cam)
    if pts is None:
        raise ValueError("model crosses the camera near plane")
    x0, y0 = pts.min(0)
    x1, y1 = pts.max(0) + 1
    if extra is not None:
        x0, y0, x1, y1 = min(x0, extra[0]), min(y0, extra[1]), max(x1, extra[2]), max(y1, extra[3])
    m = margin * (x1 - x0)
    H, W = shape
    return (int(max(0, math.floor(x0 - m))), int(max(0, math.floor(y0 - m))),
            int(min(W, math.ceil(x1 + m))), int(min(H, math.ceil(y1 + m))))


def _bbox(mask: np.ndarray):
    ys, xs = np.nonzero(mask)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def masked_loss(lv: C.Level, scene: raster.RasterScene, cam: Camera, keep: np.ndarray | None) -> float:
    """The S3 loss (1 - soft IoU + 4 x boundary / width + outside penalty) restricted to grid pixels where
    ``keep`` (None = the plain ``Level.loss``)."""
    if keep is None:
        return lv.loss(scene, cam)
    r = lv.render(scene, cam)
    if r is None:
        return C._BAD
    mask = r["mask"]
    opt = lv.ignored(r)
    ref = lv.ref
    if opt is not None:
        hard = mask & ~opt
        inter = float(ref[mask & keep].sum())
        union = float((hard & keep).sum()) + float(ref[~hard & keep].sum())
    else:
        inter = float(ref[mask & keep].sum())
        union = float((mask & keep).sum()) + float(ref[keep].sum()) - inter
    if not (mask & keep).any() or union <= 0:
        return C._BAD
    eff = lv.effective(mask, opt)
    ee = C._edge(eff)
    er, pe = ee & keep, lv.edge & keep
    d1 = float(np.minimum(lv.dist[er], lv.trunc).mean()) if er.any() else lv.trunc
    d2 = float(np.minimum(C._dist_to(ee)[pe], lv.trunc).mean()) if pe.any() else lv.trunc
    bd = 0.5 * (d1 + d2) / lv.width
    return (1.0 - inter / union) + C.BOUNDARY_WEIGHT * bd + C.OUTSIDE_PENALTY * lv.outside_share(scene, cam)


def refine_keep(scenes: dict, lv: C.Level, cam: Camera, gen: generator.Generator) -> np.ndarray | None:
    """Grid pixels outside the model's temple region at the frozen camera (None: nothing to mask)."""
    if scenes["behind"] is None or scenes["front"] is None:
        return None
    fr, bh = lv.render(scenes["front"], cam), lv.render(scenes["behind"], cam)
    if fr is None or bh is None:
        return None
    ppm = C.px_per_mm_at(cam, gen.frame, C.front_piece_centre(gen)) / lv.stride
    M = temple_region(fr["mask"], bh["mask"], TEMPLE_DILATE_MM * ppm)
    return None if not M.any() else ~M


def refine_camera(scene: raster.RasterScene, lv: C.Level, cam0: Camera, pivot, maxfev: int = REFINE_MAXFEV,
                  bounds: dict = REFINE_BOUNDS, keep: np.ndarray | None = None) -> tuple[Camera, dict]:
    """Bounded refine of a frozen camera (perspective fixed): angles +/- rotation_deg, magnification at the
    pivot +/- scale_pct, pivot image position +/- translation_pct_width of the matte width. With ``keep``
    the loss ignores the other grid pixels (the model's temple region)."""
    unit = lv.width_native
    x0 = C.pack(cam0, unit, pivot)
    free = [0, 1, 2, 4, 5, 6]
    ds = 100.0 * math.log(1.0 + bounds["scale_pct"] / 100.0)
    half = [bounds["rotation_deg"]] * 3 + [ds, bounds["translation_pct_width"], bounds["translation_pct_width"]]
    bnds = [(x0[i] - h, x0[i] + h) for i, h in zip(free, half)]
    best = [masked_loss(lv, scene, cam0, keep), cam0]
    loss0 = best[0]

    def f(z):
        x = x0.copy()
        x[free] = z
        c = C.unpack(x, unit, pivot)
        v = masked_loss(lv, scene, c, keep)
        if v < best[0]:
            best[0], best[1] = v, c
        return v

    res = optimize.minimize(f, x0[free], method="Powell", bounds=bnds,
                            options={"maxfev": maxfev, "xtol": 1e-2, "ftol": 1e-5})
    c = best[1]
    xb = C.pack(c, unit, pivot)
    info = {"loss_frozen": float(loss0), "loss_refined": float(best[0]), "nfev": int(res.nfev),
            "temple_masked": keep is not None,
            "d_yaw": float(xb[0] - x0[0]), "d_pitch": float(xb[1] - x0[1]), "d_roll": float(xb[2] - x0[2]),
            "d_scale_pct": float(100.0 * (math.exp((xb[4] - x0[4]) / 100.0) - 1.0)),
            "d_pivot_pct_w": [float(xb[5] - x0[5]), float(xb[6] - x0[6])],
            "at_bound": [bool(abs(xb[i] - x0[i]) >= 0.98 * h) for i, h in zip(free, half)]}
    return c, info


def _edge(m: np.ndarray) -> np.ndarray:
    return C._edge(m)


def _dist(m: np.ndarray) -> np.ndarray:
    return C._dist_to(m)


def _contour_pair(a_edge, b_edge, da, db, keep) -> dict:
    """Symmetric contour distance restricted to edge pixels where ``keep``: da = distance to a's edge,
    db = distance to b's edge (px)."""
    x = db[a_edge & keep]
    y = da[b_edge & keep]
    if not len(x) and not len(y):
        return {"n": 0, "mean_px": None, "p95_px": None}
    parts = [v for v in (x, y) if len(v)]
    return {"n": int(len(x) + len(y)), "n_model": int(len(x)), "n_photo": int(len(y)),
            "mean_px": float(np.mean([v.mean() for v in parts])),
            "p95_px": float(max(np.percentile(v, 95) for v in parts)),
            "model_to_photo_px": float(x.mean()) if len(x) else None,
            "photo_to_model_px": float(y.mean()) if len(y) else None}


def _to_mm(d: dict, ppm: float, W: float) -> dict:
    out = dict(d)
    if d.get("mean_px") is not None:
        out["mean_mm"] = d["mean_px"] / ppm
        out["p95_mm"] = d["p95_px"] / ppm
        out["pct_w"] = 100.0 * out["mean_mm"] / W
    return out


def temple_region(front_mask: np.ndarray, behind_mask: np.ndarray, dil_px: float) -> np.ndarray:
    """Per-model temple mask: the behind-slab projection outside the front-piece projection, dilated, plus
    every pixel nearer to it than to the front-piece projection."""
    Bv = behind_mask & ~front_mask
    if not Bv.any():
        return np.zeros_like(front_mask)
    dB = _dist(Bv)
    out = dB <= dil_px
    if front_mask.any():
        out |= dB < _dist(front_mask)
    else:
        out[:] = True
    return out


@dataclass
class ViewCtx:
    view: str
    fg: np.ndarray
    level: C.Level
    cam: Camera
    pivot: np.ndarray | None
    roi: tuple[int, int, int, int]
    gen_temple: np.ndarray        # generator temple region, full image
    optional_lens: bool


def _render_masks(model: Model, scenes: dict, cam: Camera, shape, roi, zc: float):
    r = scenes["full"].render(cam, shape, 1, roi)
    fid = r["face_id"]
    hit = fid >= 0
    lab = np.full(fid.shape, -1, np.int8)
    lab[hit] = model.labels[fid[hit]]
    fr = scenes["front"].render(cam, shape, 1, roi)["mask"] if scenes["front"] is not None else np.zeros_like(hit)
    bh = scenes["behind"].render(cam, shape, 1, roi)["mask"] if scenes["behind"] is not None else np.zeros_like(hit)
    return hit, lab, fr, bh, r


def view_metrics(model: Model, scenes: dict, ctx: ViewCtx, cam: Camera, gen: generator.Generator, W: float,
                 zc: float) -> tuple[dict, dict]:
    """Native-resolution metrics of one model in one view (see the module docstring)."""
    shape = ctx.fg.shape
    roi = _hull_roi(scenes["full"], cam, shape, 0.03, ctx.roi)
    x0, y0, x1, y1 = roi
    hit, lab, fr, bh, _ = _render_masks(model, scenes, cam, shape, roi, zc)
    fg = ctx.fg[y0:y1, x0:x1]
    eff = hit
    if ctx.optional_lens:
        lens = lab == LENS
        eff = (hit & ~lens) | (lens & fg)
    ppm = C.px_per_mm_at(cam, gen.frame, C.front_piece_centre(gen))
    M = temple_region(fr, bh, TEMPLE_DILATE_MM * ppm)
    T = ctx.gen_temple[y0:y1, x0:x1]
    Mf, Pf = ndimage.binary_fill_holes(eff), ndimage.binary_fill_holes(fg)
    Em, Ep = _edge(Mf), _edge(Pf)
    dEm, dEp = _dist(Em), _dist(Ep)
    fp = _to_mm(_contour_pair(Em, Ep, dEm, dEp, ~M), ppm, W)
    keep = ~M
    fp["iou"] = float((Mf & Pf & keep).sum() / max(((Mf | Pf) & keep).sum(), 1))
    common = _to_mm(_contour_pair(Em, Ep, dEm, dEp, ~(M | T)), ppm, W)
    common["iou"] = float((Mf & Pf & ~(M | T)).sum() / max(((Mf | Pf) & ~(M | T)).sum(), 1))
    Eh, Eph = _edge(eff), _edge(fg)
    holes = _to_mm(_contour_pair(Eh, Eph, _dist(Eh), _dist(Eph), ~M), ppm, W)
    temple = _to_mm(_contour_pair(Em, Ep, dEm, dEp, M | T), ppm, W)
    ext = {}
    if Mf.any() and Pf.any():
        (my, mx), (py, px) = np.nonzero(Mf), np.nonzero(Pf)
        ext = {"render_w_px": int(mx.max() - mx.min() + 1), "photo_w_px": int(px.max() - px.min() + 1),
               "render_h_px": int(my.max() - my.min() + 1), "photo_h_px": int(py.max() - py.min() + 1)}
        ext["ratio_w"] = ext["render_w_px"] / ext["photo_w_px"]
        ext["ratio_h"] = ext["render_h_px"] / ext["photo_h_px"]
    out = {"px_per_mm": ppm, "roi": list(roi), "extent": ext, "front_piece": fp, "front_piece_common": common,
           "front_piece_with_holes": holes, "temple": temple,
           "iou_full": float((eff & fg).sum() / max((eff | fg).sum(), 1)),
           "temple_mask_share_of_roi": float(M.mean())}
    tiles = {"roi": roi, "render": eff, "mask": M}
    return out, tiles


# ------------------------------------------------------------------------------ lens metrics
def visible_lens(model: Model, scene: raster.RasterScene, cam: Camera, shape, frame: NormFrame, k: int = LENS_SS):
    """Outer boundaries (native px polylines) of the visible lens region (first hit on a lens face) at k x
    native, its hole-filled mask on the k-grid, the grid mapping and the lens-centroid px/mm."""
    from skimage import measure
    roi = _hull_roi(scene, cam, shape, 0.03)
    x0, y0, x1, y1 = roi
    cz = C.resized_camera(cam, k, x0, y0)
    shp = ((y1 - y0) * k, (x1 - x0) * k)
    r = scene.render(cz, shp, 1, want_points=True)
    fid = r["face_id"]
    L = np.zeros(fid.shape, bool)
    L[fid >= 0] = model.labels[fid[fid >= 0]] == LENS
    if not L.any():
        return None
    centre = r["points"][L].mean(axis=0)
    ppm = C.px_per_mm_at(cam, frame, centre)
    lab, n = ndimage.label(L)
    min_px = LENS_MIN_AREA_MM2 * (ppm * k) ** 2
    sizes = ndimage.sum(np.ones_like(L), lab, np.arange(1, n + 1))
    keep = np.isin(lab, 1 + np.nonzero(sizes >= min_px)[0])
    keep = ndimage.binary_fill_holes(keep)
    cs = measure.find_contours(np.pad(keep.astype(np.float32), 1), 0.5)
    lines = [np.column_stack([(c[:, 1] - 1 + 0.5) / k - 0.5 + x0, (c[:, 0] - 1 + 0.5) / k - 0.5 + y0]) for c in cs
             if len(c) >= 8]
    return {"lines": lines, "mask": keep, "roi": roi, "k": k, "ppm": ppm, "centre_mm": centre.tolist(),
            "components": int((sizes >= min_px).sum())}


def _inside(vis: dict, P: np.ndarray) -> np.ndarray:
    x0, y0 = vis["roi"][:2]
    k = vis["k"]
    j = np.round((P[:, 0] - x0 + 0.5) * k - 0.5).astype(int)
    i = np.round((P[:, 1] - y0 + 0.5) * k - 0.5).astype(int)
    m = vis["mask"]
    ok = (i >= 0) & (i < m.shape[0]) & (j >= 0) & (j < m.shape[1])
    out = np.zeros(len(P), bool)
    out[ok] = m[i[ok], j[ok]]
    return out


def _resample(lines: list[np.ndarray], step: float = 0.5) -> np.ndarray:
    out = []
    for c in lines:
        seg = np.diff(c, axis=0)
        L = np.concatenate([[0], np.cumsum(np.hypot(seg[:, 0], seg[:, 1]))])
        if L[-1] <= 0:
            continue
        s = np.arange(0, L[-1], step)
        out.append(np.column_stack([np.interp(s, L, c[:, 0]), np.interp(s, L, c[:, 1])]))
    return np.vstack(out) if out else np.zeros((0, 2))


def _stats_mm(d: np.ndarray, ppm: float, signed: np.ndarray | None = None) -> dict:
    if not len(d):
        return {"n": 0, "mean_mm": None, "p95_mm": None}
    out = {"n": int(len(d)), "mean_mm": float(d.mean() / ppm), "median_mm": float(np.median(d) / ppm),
           "p95_mm": float(np.percentile(d, 95) / ppm), "max_mm": float(d.max() / ppm)}
    if signed is not None:
        out["signed_mean_mm"] = float(signed.mean() / ppm)
    return out


def outline_distance(vis: dict, rings: list[np.ndarray], ring_types: list[np.ndarray] | None = None,
                     ring_occluded: list[np.ndarray] | None = None, want_type: str | None = None) -> dict:
    """Both directions between the visible lens boundary and reference rings (px, native): reference ->
    model over reference samples of ``want_type`` (non-occluded), model -> reference over model samples
    whose nearest reference sample is of that type. ``symmetric_mean_mm`` = mean of the two means;
    signed > 0 = model outside the reference."""
    import shapely
    from scipy.spatial import cKDTree
    from .ground_truth import boundary_samples
    S, ST, SO = [], [], []
    for i, r in enumerate(rings):
        t = None if ring_types is None else ring_types[i]
        o = None if ring_occluded is None else ring_occluded[i]
        s, st, so = boundary_samples(r, t, o, 0.25)
        S.append(s); ST.append(st); SO.append(so)
    S, ST, SO = np.vstack(S), np.concatenate(ST), np.concatenate(SO)
    good = ~SO if want_type is None else (ST == want_type) & ~SO
    ppm = vis["ppm"]
    if not vis["lines"] or not good.any():
        return {"reference_to_model": {"n": 0}, "model_to_reference": {"n": 0}, "symmetric_mean_mm": None}
    ml = shapely.MultiLineString([l for l in vis["lines"]])
    Rs = S[good][::2]                                                  # 0.5 px spacing
    d1 = shapely.distance(shapely.points(Rs), ml)
    ins = _inside(vis, Rs)
    r2m = _stats_mm(d1, ppm, np.where(ins, d1, -d1))
    Ms = _resample(vis["lines"], 0.5)
    d_all, idx = cKDTree(S).query(Ms)
    sel = good[idx]
    ref_poly = shapely.union_all([shapely.Polygon(r).buffer(0) for r in rings])
    inside_ref = shapely.contains_xy(ref_poly, Ms[sel, 0], Ms[sel, 1])
    d2 = d_all[sel]
    m2r = _stats_mm(d2, ppm, np.where(inside_ref, -d2, d2))
    sym = None
    if r2m.get("mean_mm") is not None and m2r.get("mean_mm") is not None:
        sym = 0.5 * (r2m["mean_mm"] + m2r["mean_mm"])
    return {"reference_to_model": r2m, "model_to_reference": m2r, "symmetric_mean_mm": sym, "px_per_mm": ppm}


def gt_frame_truth(gt, photo: str) -> dict:
    """Is there frame-bounded, non-occluded ground truth to verify the lens edge against on the criterion photo?
    From the truth file alone (``ground_truth``): the answer never depends on the model or on a measurement."""
    from . import ground_truth
    if gt is None:
        return {"applicable": False, "reason": "no ground truth for this product"}
    n = 0
    for ln in gt.get("lenses") or []:
        if ln.get("photo") != photo:
            continue
        _, ST, SO = ground_truth.boundary_samples(ln["points_px"], ln.get("segment_types"), ln.get("occluded"))
        n += int(np.sum((ST == "frame") & ~SO))
    if not n:
        return {"applicable": False, "reason": "no non-occluded frame-bounded ground-truth segment", "photo": photo}
    return {"applicable": True, "frame_points": n, "photo": photo}


def lens_metrics(model: Model, scene: raster.RasterScene, cams: dict, frame: NormFrame, s2res: dict, s2arr: dict,
                 shapes: dict, gt: dict | None, frozen: dict | None = None) -> dict:
    out = {"has_lens": bool((model.labels == LENS).any())}
    if not out["has_lens"]:
        return out
    view = s2_view(s2res)
    vis = {v: visible_lens(model, scene, cams[v], shapes[v], frame) for v in {view} | (
        {l["photo"] for l in gt["lenses"]} if gt else set()) if v in cams}
    if vis.get(view) is None:
        out["visible"] = False
        return out
    out["visible"] = True
    out["components"] = {v: (x["components"] if x else 0) for v, x in vis.items()}
    rings = s2_lens_polys(s2res, s2arr, shapes[view])
    out["s2"] = {"view": view, **outline_distance(vis[view], rings)}
    out["_lines"] = vis[view]["lines"]
    if frozen is not None:
        # the same comparison through the FROZEN camera (no refine): for the card, a pure check of the
        # lift -> render chain; for the others, the lens edge before the bounded refine
        vf = visible_lens(model, scene, frozen[view], shapes[view], frame)
        out["s2_frozen_camera"] = None if vf is None else {"view": view, **outline_distance(vf, rings)}
    if gt:
        per = []
        for photo in sorted({l["photo"] for l in gt["lenses"]}):
            if vis.get(photo) is None:
                continue
            ls = [l for l in gt["lenses"] if l["photo"] == photo]
            res = outline_distance(vis[photo], [np.asarray(l["points_px"], float) for l in ls],
                                   [l["segment_types"] for l in ls], [l.get("occluded") for l in ls], "frame")
            allt = outline_distance(vis[photo], [np.asarray(l["points_px"], float) for l in ls],
                                    [l["segment_types"] for l in ls], [l.get("occluded") for l in ls], None)
            per.append({"photo": photo, "sides": [l["side"] for l in ls],
                        "confidence": [l.get("confidence") for l in ls], **res,
                        "all_types_symmetric_mean_mm": allt["symmetric_mean_mm"],
                        "all_types_signed_mean_mm": allt["reference_to_model"].get("signed_mean_mm")})
        out["gt"] = {"entries": per, "criterion_photo": view,
                     "frame_symmetric_mean_mm": next((e["symmetric_mean_mm"] for e in per if e["photo"] == view), None)}
    return out


def seam_metrics(model: Model, scenes: dict, cam: Camera, shape, frame: NormFrame, gen, fg: np.ndarray | None = None) -> dict:
    """Lens-vs-frame gaps in the front render at ~10 px/mm. A gap pixel is a hole of the render (background
    enclosed by the model) that (1) touches the lens silhouette, (2) belongs to a hole component no wider than
    ``SEAM_MAX_WIDTH_MM`` (a sliver; 2 x its largest inscribed radius) and (3) lies where the front photo
    shows material (S0 matte eroded by one native pixel). Reported apart: ``legit_holes_px`` (the photo shows
    backdrop there too, e.g. inside a rimless hinge arch), ``wide_holes_touching_lens_px`` (wider holes over
    photo material: see-through holes in a lens, or backdrop behind which the photo shows a temple) and
    ``single_px_cracks`` (float32 ray cracks). Without ``fg`` rule (3) is skipped."""
    if scenes["lens"] is None:
        return {"has_lens": False}
    ppm = C.px_per_mm_at(cam, frame, C.front_piece_centre(gen))
    k = max(1.0, SEAM_PX_PER_MM / ppm)
    px_mm = ppm * k
    roi = _hull_roi(scenes["full"], cam, shape, 0.03)
    cz = C.resized_camera(cam, k, roi[0], roi[1])
    shp = (int(math.ceil((roi[3] - roi[1]) * k)), int(math.ceil((roi[2] - roi[0]) * k)))
    A = scenes["full"].render(cz, shp, 1)["mask"]
    L = scenes["lens"].render(cz, shp, 1)["mask"]
    holes = ndimage.binary_fill_holes(A) & ~A
    touch = cv2.dilate(L.astype(np.uint8), np.ones((2 * SEAM_TOUCH_PX + 1,) * 2, np.uint8)).astype(bool)
    hl, hn = ndimage.label(holes)
    thin = np.zeros_like(holes)
    if hn:
        dt = cv2.distanceTransform(holes.astype(np.uint8), cv2.DIST_L2, 5)
        width_px = 2.0 * ndimage.maximum(dt, hl, np.arange(1, hn + 1))
        thin = np.isin(hl, 1 + np.nonzero(width_px <= SEAM_MAX_WIDTH_MM * px_mm)[0])
    g = holes & touch
    photo_solid = np.ones_like(g)
    if fg is not None:
        solid = cv2.erode(fg.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        jj, ii = np.meshgrid(np.arange(shp[1]), np.arange(shp[0]))
        u = np.round((jj + 0.5) / k - 0.5 + roi[0]).astype(int)       # native pixel of each k-grid sample
        v = np.round((ii + 0.5) / k - 0.5 + roi[1]).astype(int)
        ok = (u >= 0) & (u < fg.shape[1]) & (v >= 0) & (v < fg.shape[0])
        photo_solid = np.zeros_like(g)
        photo_solid[ok] = solid[v[ok], u[ok]]
    legit = g & ~photo_solid
    wide = g & photo_solid & ~thin
    g = g & photo_solid & thin
    lab, n = ndimage.label(g, structure=np.ones((3, 3)))
    sizes = ndimage.sum(np.ones_like(g), lab, np.arange(1, n + 1)) if n else np.zeros(0)
    big = sizes >= SEAM_MIN_COMPONENT_PX
    gap = np.isin(lab, 1 + np.nonzero(big)[0]) if n else g
    return {"has_lens": True, "px_per_mm": px_mm, "gap_pixels": int(sizes[big].sum()), "gap_components": int(big.sum()),
            "gap_area_mm2": float(sizes[big].sum() / px_mm ** 2), "single_px_cracks": int((~big).sum()),
            "legit_holes_px": int(legit.sum()), "wide_holes_touching_lens_px": int(wide.sum()),
            "holes_px_total": int(holes.sum()), "photo_rule": fg is not None, "_masks": (A, L, gap, wide)}


def phantom_share(model: Model, W: float) -> dict:
    """Area share (%) of faces with centroid |x| < 0.3 W and z < z_max - 0.15 W (the wearer's head)."""
    V, F = model.V, model.F
    A = 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)
    c = V[F].mean(axis=1)
    zmax = float(V[:, 2].max())
    head = (np.abs(c[:, 0]) < PHANTOM_X_FRAC * W) & (c[:, 2] < zmax - PHANTOM_Z_FRAC * W)
    return {"pct": float(100.0 * A[head].sum() / max(A.sum(), 1e-12)), "area_mm2": float(A[head].sum()),
            "faces": int(head.sum())}


# ============================================================================== evaluation
def _scenes(model: Model, zc: float, frame: NormFrame) -> dict:
    zf = model.centroids[:, 2]
    front, behind, lens = zf >= zc, zf < zc, model.labels == LENS
    mk = lambda sel: raster.RasterScene(model.V, model.F[sel], frame) if sel.any() else None
    return {"full": raster.RasterScene(model.V, model.F, frame), "front": mk(front), "behind": mk(behind),
            "lens": mk(lens)}


def _context(product: str, gen, cams: dict, s0arr: dict, s3res: dict) -> dict[str, ViewCtx]:
    ctx = {}
    optional = set(s3res.get("lens_faces", {}).get("optional_in", []))
    gscene = gen.scene(decimated=True)
    zc = float(gen.Vd[:, 2].max()) - float(gen.result["front_depth_mm"])
    zf = gen.Vd[gen.Fd].mean(axis=1)[:, 2]
    fs = raster.RasterScene(gen.Vd, gen.Fd[zf >= zc], gen.frame)
    bs = raster.RasterScene(gen.Vd, gen.Fd[zf < zc], gen.frame)
    fp = gen.frame.to_norm(C.front_piece_centre(gen).reshape(1, 3))[0]
    for v in VIEWS:
        fg = s0arr[f"fg_{v}"]
        cam = cams[v]
        lv = C.Level(fg, REFINE_LEVEL_PX, v, C.ROI_MARGIN)
        roi = _hull_roi(gscene, cam, fg.shape, ROI_MARGIN, _bbox(fg))
        ppm = C.px_per_mm_at(cam, gen.frame, C.front_piece_centre(gen))
        fr = fs.render(cam, fg.shape, 1)["mask"]
        bh = bs.render(cam, fg.shape, 1)["mask"]
        T = temple_region(fr, bh, TEMPLE_DILATE_MM * ppm)
        ctx[v] = ViewCtx(v, fg, lv, cam, None if v in ("left", "right") else fp, roi, T, v in optional)
    return ctx


def evaluate(product: str, models: dict, run: str = "m1", *, views=VIEWS, log=print,
             keep_tiles: bool = False) -> dict:
    """Score ``models`` ({name: (V_mm, F, part_labels) | GLB path | Model}) for ``product`` with the frozen
    S3 cameras and the same bounded refine per model. Returns {models: {name: metrics}, context}."""
    from . import ground_truth
    gen = generator.load(product, run)
    frame, cams, s3res = C.load_cameras(product, run)
    s0arr = dict(np.load(run_dir(run, product) / "s0_intake" / "arrays.npz", allow_pickle=False))
    s2res, s2arr = _s2(product, run)
    gt = ground_truth.load(product) if ground_truth.json_path(product).exists() else None
    W = float(PRODUCTS[product].front_width_mm)
    ctx = _context(product, gen, cams, s0arr, s3res)
    shapes = {v: s0arr[f"fg_{v}"].shape for v in VIEWS}
    out, tiles = {}, {}
    for name, spec in models.items():
        t0 = time.time()
        m = as_model(name, spec, gen)
        zc = slab_cut_z(m, gen)
        sc = _scenes(m, zc, gen.frame)
        rec = {"kind": m.kind, "glb": m.glb, "faces": int(len(m.F)),
               "faces_by_label": {k: int((m.labels == v).sum()) for k, v in LABEL_NAMES.items()},
               "slab_cut_z_mm": zc, "info": m.info, "views": {}}
        refined = {}
        for v in views:
            c = ctx[v]
            c.level.ignore_faces = (m.labels == LENS) if c.optional_lens else None
            try:
                keep = refine_keep(sc, c.level, c.cam, gen) if v in TEMPLE_MASKED_REFINE_VIEWS else None
                cam, rinfo = refine_camera(sc["full"], c.level, c.cam, c.pivot, keep=keep)
                vm, tl = view_metrics(m, sc, c, cam, gen, W, zc)
            except ValueError as e:      # e.g. geometry across the camera near plane: this view is unscored
                rec["views"][v] = {"error": str(e), "held_out": v in HELD_OUT_VIEWS}
                rec.setdefault("errors", []).append(f"{v}: {e}")
                continue
            refined[v] = cam
            vm["refine"] = rinfo
            vm["camera"] = {k: float(x) for k, x in cam.to_dict().items()}
            vm["held_out"] = v in HELD_OUT_VIEWS
            rec["views"][v] = vm
            if keep_tiles:
                tiles[(name, v)] = tl
        rcams = {v: refined.get(v, cams[v]) for v in VIEWS}
        try:
            rec["lens"] = lens_metrics(m, sc["full"], rcams, gen.frame, s2res, s2arr, shapes, gt, cams)
        except ValueError as e:
            rec["lens"] = {"has_lens": bool((m.labels == LENS).any()), "error": str(e)}
            rec.setdefault("errors", []).append(f"lens: {e}")
        rec["gt_frame_truth"] = gt_frame_truth(gt, s2_view(s2res))
        lines = rec["lens"].pop("_lines", None)
        if keep_tiles:
            tiles[(name, "lens_lines")] = lines
        try:
            seam = seam_metrics(m, sc, rcams["front"], shapes["front"], gen.frame, gen, s0arr["fg_front"])
        except ValueError as e:
            seam = {"has_lens": bool((m.labels == LENS).any()), "error": str(e)}
            rec.setdefault("errors", []).append(f"seam: {e}")
        masks = seam.pop("_masks", None)
        if keep_tiles:
            tiles[(name, "seam")] = masks
        rec["seam"] = seam
        rec["phantom"] = phantom_share(m, W)
        if m.glb:
            chk = contract.check(m.glb)
            rec["contract"] = {"ok": chk["ok"], "failures": chk["failures"], "summary": chk.get("summary")}
        rec["seconds"] = round(time.time() - t0, 2)
        out[name] = rec
        log(f"  {product}:{name} " + " ".join(
            f"{v}:{(rec['views'][v].get('front_piece') or {}).get('pct_w', float('nan')):.3f}%W" for v in views)
            + f" lensS2 {((rec['lens'].get('s2') or {}).get('symmetric_mean_mm'))} "
            + (f"ERRORS {rec['errors']} " if rec.get("errors") else "")
            + f"gap {rec['seam'].get('gap_pixels')} ({rec['seconds']} s)")
    res = {"product": product, "models": out,
           "context": {"front_width_mm": W, "views": {v: {"roi": list(ctx[v].roi), "optional_lens": ctx[v].optional_lens}
                                                        for v in views},
                       "s2_view": s2_view(s2res), "ground_truth": gt is not None}}
    if keep_tiles:
        res["_tiles"] = tiles
    return res


# ============================================================================== criteria / decision
def _fp(rec: dict, view: str, key: str = "pct_w", which: str = "front_piece"):
    try:
        return rec["views"][view][which][key]
    except KeyError:
        return None


def temple_side_mean_mm(rec: dict) -> float | None:
    vals = [(rec["views"][v].get("temple") or {}).get("mean_mm") for v in ("left", "right") if v in rec["views"]]
    vals = [x for x in vals if x is not None]
    return float(np.mean(vals)) if vals else None


def criteria(rec: dict, previous: dict | None, s9: dict | None = None, sensitivity: float | None = None) -> dict:
    """M1 criteria (DESIGN.md) for one model; ``previous`` = the previous candidate's record; ``sensitivity`` = the
    gate's temple sensitivity of the angled front-piece score (validation, informational on c3)."""
    c = {}
    if rec.get("kind") == "bsa":
        if s9 is not None and "m1_criterion_1" in s9:
            c["c1_contract_ar_lenses"] = {"pass": bool(s9["m1_criterion_1"]), "source": "s9_export/result.json"}
        elif "contract" in rec:
            c["c1_contract_ar_lenses"] = {"pass": None, "contract_ok": rec["contract"]["ok"],
                                          "note": "AR harness not run by the gate (S9 owns it)"}
        else:
            c["c1_contract_ar_lenses"] = {"pass": None, "note": "no GLB (S9 not exported)"}
    elif "contract" in rec:
        c["c1_contract_ar_lenses"] = {"pass": None, "contract_ok": rec["contract"]["ok"],
                                      "contract_failures": rec["contract"]["failures"],
                                      "note": "contract only; the AR harness is S9's"}
    lens = rec.get("lens") or {}
    g = lens.get("gt") or {}
    v2 = g.get("frame_symmetric_mean_mm")
    c["c2_lens_edge_vs_gt"] = {"value_mm": v2, "limit_mm": CRIT2_MM, "pass": None if v2 is None else bool(v2 <= CRIT2_MM),
                               "photo": g.get("criterion_photo")}
    if v2 is None:
        # applicability is decided by the GROUND TRUTH alone (``gt_frame_truth``): not applicable only when there is no
        # truth file or its criterion photo has no non-occluded frame-bounded segment (a rimless lens). A measurement
        # failure (lens not visible, a near-plane error, no model outline) leaves c2 applicable and unevaluated: RETRY.
        truth = rec.get("gt_frame_truth")
        if truth is not None and not truth.get("applicable", True):
            c["c2_lens_edge_vs_gt"].update(applicable=False, reason=truth.get("reason"))
        elif lens.get("error") or lens.get("visible") is False:
            c["c2_lens_edge_vs_gt"].update(applicable=True, reason=f"not measured: {lens.get('error') or 'lens not visible'}")
    a = _fp(rec, "angled")
    b = _fp(previous, "angled") if previous else None
    c["c3_heldout_angled_front_piece"] = {"value_pct_w": a, "previous_pct_w": b, "limit_pct_w": None if b is None else b + CRIT3_PCT_W,
                                          "pass": None if (a is None or b is None) else bool(a <= b + CRIT3_PCT_W)}
    if sensitivity is not None and a is not None and b is not None:
        # how far the gate's bounded refine lets a temple error move this score (fault temples_x085 on the previous
        # candidate): a pass whose margin is below it is not a clean front-shape verdict
        margin = b + CRIT3_PCT_W - a
        c["c3_heldout_angled_front_piece"].update(margin_pct_w=round(margin, 4),
                                                  temple_sensitivity_pct_w=round(abs(sensitivity), 4),
                                                  clean=bool(margin > abs(sensitivity)))
    if previous is None:
        c["c3_heldout_angled_front_piece"].update(applicable=False, reason="no previous candidate to compare with")
    s = rec.get("seam") or {}
    c["c4_zero_seam_gaps"] = {"gap_pixels": s.get("gap_pixels"),
                              "pass": None if (not s.get("has_lens") or "gap_pixels" not in s) else bool(s["gap_pixels"] == 0)}
    return c


def plan_gates(rec: dict, previous: dict | None) -> dict:
    """The plan's initial gates (diagnostic until frozen after the M1 owner ratings)."""
    a, b = _fp(rec, "angled"), _fp(previous, "angled") if previous else None
    lim = PLAN_GATES["angled_front_piece_pct_w_max"] if b is None else min(PLAN_GATES["angled_front_piece_pct_w_max"], b + CRIT3_PCT_W)
    lens = ((rec.get("lens") or {}).get("gt") or {}).get("frame_symmetric_mean_mm")
    return {"angled_front_piece": {"value_pct_w": a, "limit_pct_w": lim, "pass": None if a is None else bool(a <= lim)},
            "lens_edge": {"value_mm": lens, "limit_mm": PLAN_GATES["lens_edge_mm_max"],
                          "pass": None if lens is None else bool(lens <= PLAN_GATES["lens_edge_mm_max"])},
            "phantom": {"value_pct": rec["phantom"]["pct"], "limit_pct": PLAN_GATES["phantom_pct_max"],
                        "pass": bool(rec["phantom"]["pct"] <= PLAN_GATES["phantom_pct_max"])},
            "seam": {"gap_pixels": (rec.get("seam") or {}).get("gap_pixels"),
                     "pass": None if "gap_pixels" not in (rec.get("seam") or {}) else bool(rec["seam"]["gap_pixels"] == 0)}}


def review_rule(flag: str) -> str | None:
    """The REVIEW_RULES key a flag triggers (``lens_components_<n>`` -> ``lens_components``), or None."""
    if flag in REVIEW_RULES:
        return flag
    if flag.startswith("lens_components_"):
        return "lens_components"
    return None


def decide(crit: dict | None, flags: list[str], has_bsa: bool) -> dict:
    """The gate decision; ``flags`` = the S2 flags plus the gate's own (``lens_views_inconsistent``).

    REVIEW  a REVIEW_RULES flag is raised or there is no BSA model: the result cannot be verified automatically or is
            risky, and a retry would not change that. Takes precedence.
    RETRY   otherwise, an applicable criterion failed or could not be evaluated (a missing or failed artifact): a retry
            of the route or of the failed stage can fix it.
    READY   every applicable criterion passes. A criterion marked ``applicable: False`` (no truth to compare with, e.g.
            c2 for a rimless lens) neither blocks READY nor sends to REVIEW by itself; an unmeasurable rimless outline
            is caught by its own rule (rimless_low_confidence).
    INFORMATIONAL_FLAGS (and any other flag) are reported in ``reasons`` as ``info:<flag>`` and never change the
    decision."""
    review = sorted({r for r in (review_rule(f) for f in flags) if r})
    if not has_bsa:
        review = ["bsa_model_missing"] + review
    reasons = [f"review:{r}" for r in review]
    failed, unevaluated = [], []
    for k, v in (crit or {}).items():
        if v.get("pass") is False:
            failed.append(k)
        elif v.get("pass") is None:
            if v.get("applicable") is False:
                reasons.append(f"n/a:{k}")
            else:
                unevaluated.append(k)
    reasons += [f"failed:{k}" for k in failed] + [f"unevaluated:{k}" for k in unevaluated]
    reasons += [f"info:{f}" for f in sorted(set(flags)) if review_rule(f) is None]
    if review:
        d = "REVIEW"
    elif failed or unevaluated or crit is None:
        d = "RETRY"
    else:
        d = "READY"
    return {"decision": d, "reasons": reasons, "review_rules": {r: REVIEW_RULES[r] for r in review}}


def _delta(a: dict, b: dict, fn):
    x, y = fn(a), fn(b)
    return None if None in (x, y) else y - x


def validation(models: dict) -> dict:
    """Gate self-check: the card must be clearly worse than the previous candidate on the held-out view,
    and each fault must move its metric the right way (worse)."""
    prev = models.get("previous")
    if prev is None:
        return {}
    out = {}
    card = models.get("card")
    if card:
        a, b = _fp(prev, "angled"), _fp(card, "angled")
        ac, bc = _fp(prev, "angled", which="front_piece_common"), _fp(card, "angled", which="front_piece_common")
        out["card_vs_previous_angled"] = {
            "previous_pct_w": a, "card_pct_w": b, "delta_pct_w": None if None in (a, b) else b - a,
            "previous_iou": _fp(prev, "angled", "iou"), "card_iou": _fp(card, "angled", "iou"),
            "common_mask_previous_pct_w": ac, "common_mask_card_pct_w": bc,
            "card_fails_c3": None if None in (a, b) else bool(b > a + CRIT3_PCT_W),
            "card_fails_c3_common_mask": None if None in (ac, bc) else bool(bc > ac + CRIT3_PCT_W)}
    s2_abs = lambda r: ((r.get("lens") or {}).get("s2_frozen_camera") or {}).get("symmetric_mean_mm")
    s2_signed_frozen = lambda r: (((r.get("lens") or {}).get("s2_frozen_camera") or {}).get("reference_to_model") or {}).get("signed_mean_mm")

    def signed(key):
        def fn(r):
            lens = r.get("lens") or {}
            if key == "s2":
                return ((lens.get("s2") or {}).get("reference_to_model") or {}).get("signed_mean_mm")
            g = lens.get("gt") or {}
            e = [x for x in (g.get("entries") or []) if x["photo"] == g.get("criterion_photo")]
            return e[0].get("all_types_signed_mean_mm") if e else None
        return fn
    inc = lambda b, v: v > b * 1.05 and v - b > 0.02
    one_mm = lambda b, v: 0.5 <= v - b <= 1.5
    # one PRIMARY row per fault measures the perturbed quantity directly (signed where the product's own
    # base error could point either way); SECONDARY rows are the gate's decision metrics, reported and judged
    # but allowed to fail when the base model is biased the same way as the fault (then listed).
    checks = {
        "temples_x085": [("side temple contour mean mm (left/right)", temple_side_mean_mm, "previous", inc, "increase", True)],
        "front_stretch_x105": [("front silhouette width ratio render/photo (signed)",
                                lambda r: (r["views"].get("front", {}).get("extent") or {}).get("ratio_w"), "previous",
                                lambda b, v: 0.02 <= v - b <= 0.06, "+0.02..0.06", True),
                               ("front front-piece mean mm", lambda r: _fp(r, "front", "mean_mm"), "previous", inc, "increase", False),
                               ("back front-piece mean mm", lambda r: _fp(r, "back", "mean_mm"), "previous", inc, "increase", False)],
        # the previous candidate's lens may sit inside the truth, so its |error| may fall: the primary row is
        # the signed outward shift (+0.5..1.5 mm)
        "lens_dilate_1mm": [("lens vs S2 signed mean mm (outward +)", signed("s2"), "previous", one_mm, "+0.5..1.5 mm", True),
                            ("lens vs GT signed mean mm, all non-occluded types (outward +)", signed("gt"), "previous", one_mm,
                             "+0.5..1.5 mm", False)],
        # the card is S2 lifted: through the frozen camera its lens edge reproduces S2 (~0), so the absolute
        # error must rise by ~1 mm (the refined camera moves a temple-less card; see lens.s2)
        "card_lens_dilate_1mm": [("lens vs S2 symmetric mean mm (frozen camera)", s2_abs, "card", one_mm, "+0.5..1.5 mm", True),
                                 ("lens vs S2 signed mean mm (frozen camera, outward +)", s2_signed_frozen, "card", one_mm,
                                  "+0.5..1.5 mm", False)],
        "scale_x105": [("front front-piece mean mm", lambda r: _fp(r, "front", "mean_mm"), "previous", inc, "increase", True),
                       ("angled front-piece mean mm", lambda r: _fp(r, "angled", "mean_mm"), "previous", inc, "increase", False)],
    }
    faults = {}
    for f, lst in checks.items():
        if f not in models:
            continue
        rows = []
        for label, fn, ref, rule, expected, primary in lst:
            if ref not in models:
                continue
            base, val = fn(models[ref]), fn(models[f])
            ok = None if None in (base, val) else bool(rule(base, val))
            rows.append({"metric": label, "primary": primary, "baseline_model": ref, "baseline": base, "faulted": val,
                         "delta": None if None in (base, val) else val - base, "expected": expected, "pass": ok})
        faults[f] = rows
    # metrics a fault does not touch should barely move
    side = {}
    if "lens_dilate_1mm" in models:
        side["lens_dilate_1mm_front_front_piece_delta_mm"] = _delta(prev, models["lens_dilate_1mm"], lambda r: _fp(r, "front", "mean_mm"))
    if "temples_x085" in models:
        side["temples_x085_angled_front_piece_delta_pct_w"] = _delta(prev, models["temples_x085"], lambda r: _fp(r, "angled"))
        side["temples_x085_front_front_piece_delta_pct_w"] = _delta(prev, models["temples_x085"], lambda r: _fp(r, "front"))
    if "card_lens_dilate_1mm" in models and card:
        side["card_lens_dilate_front_front_piece_delta_mm"] = _delta(card, models["card_lens_dilate_1mm"], lambda r: _fp(r, "front", "mean_mm"))
    out["unaffected_deltas"] = side
    gen_rec = models.get("generator")
    if gen_rec is not None:
        # the previous candidate IS the generator geometry (alignment residual ~0.005 mm): their difference
        # is the gate's own noise floor (refine + rasterisation), to compare with the 0.1 %W tolerance of c3
        out["same_mesh_noise_pct_w"] = {v: _delta(prev, gen_rec, lambda r, v=v: _fp(r, v)) for v in VIEWS}
    out["faults"] = faults
    out["unevaluated"] = [f"{f}:{r['metric']}" for f, rows in faults.items() for r in rows if r["pass"] is None]
    out["secondary_failures"] = [f"{f}:{r['metric']}" for f, rows in faults.items() for r in rows
                                 if not r["primary"] and r["pass"] is False]
    if card:
        pg = plan_gates(card, prev)
        out["card_plan_gates_failed"] = sorted(k for k, v in pg.items() if v["pass"] is False)
    prim = [r for rows in faults.values() for r in rows if r["primary"]]
    out["all_pass"] = bool(prim and all(r["pass"] is True for r in prim)
                           and out.get("card_vs_previous_angled", {}).get("card_fails_c3", False))
    return out


# ============================================================================== integrity (model only)
def _components(F: np.ndarray, n: int) -> np.ndarray:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    F = np.asarray(F, np.int64)
    E = np.concatenate([F[:, [0, 1]], F[:, [1, 2]]])
    A = coo_matrix((np.ones(len(E)), (E[:, 0], E[:, 1])), shape=(n, n))
    return connected_components(A, directed=False)[1]


def floating_parts(model: Model, weld_mm: float = 1e-3) -> dict:
    """Connected components of the delivered geometry whose area >= FLOAT_MIN_AREA_MM2 and whose every vertex is
    farther than FLOAT_GAP_MM from the SURFACE of every other component (point-to-triangle distance: a component
    resting on a coarsely triangulated arm is not floating): detached hardware, a wisp of plate, a sliver of donor.
    Lenses count as neighbours (a lens holds rimless hardware) but are never flagged themselves: a lens sits inside
    its rim's groove by construction, clear of the rim surface."""
    V = np.asarray(model.V, float)
    key = np.round(V / weld_mm).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    F = inv[np.asarray(model.F, np.int64)]
    n = int(inv.max()) + 1
    Vw = np.zeros((n, 3))
    Vw[inv] = V
    lab_f = _components(F, n)[F[:, 0]]
    comps = np.unique(lab_f)
    A = 0.5 * np.linalg.norm(np.cross(Vw[F[:, 1]] - Vw[F[:, 0]], Vw[F[:, 2]] - Vw[F[:, 0]]), axis=1)
    labels = np.asarray(model.labels)
    out = []
    if len(comps) >= 2:
        for c in comps:
            own = lab_f == c
            area = float(A[own].sum())
            if area < FLOAT_MIN_AREA_MM2 or np.all(labels[own] == LENS):
                continue
            scene = o3d.t.geometry.RaycastingScene()
            scene.add_triangles(o3d.core.Tensor(Vw.astype(np.float32)), o3d.core.Tensor(F[~own].astype(np.uint32)))
            vs = np.unique(F[own])
            d = scene.compute_distance(o3d.core.Tensor(Vw[vs].astype(np.float32))).numpy()
            if float(d.min()) > FLOAT_GAP_MM:
                out.append({"area_mm2": round(area, 2), "gap_mm": round(float(d.min()), 3),
                            "centroid_mm": np.round(Vw[vs].mean(0), 2).tolist(), "vertices": int(len(vs))})
    return {"components": int(len(comps)), "floating": sorted(out, key=lambda x: -x["area_mm2"])[:12],
            "rule": f"component area >= {FLOAT_MIN_AREA_MM2} mm2 with no other component's surface within {FLOAT_GAP_MM} mm"}


def silhouette_roughness(model: Model, frame: NormFrame, zc: float, px_per_mm: float = 8.0) -> dict:
    """Front-piece silhouette roughness in synthetic views (no photo; ``ROUGHNESS_VIEWS``): the outer contour of the
    hole-filled silhouette of the faces in front of the slab cut ``zc``, its length over the length of the same
    contour Gaussian-smoothed by ``ROUGHNESS_SIGMA_MM`` (1 = smooth; pleats, fins and notches raise it)."""
    from skimage import measure
    front = np.nonzero(model.V[model.F].max(axis=1)[:, 2] >= zc)[0]
    if not len(front):
        return {}
    Fp = model.F[front]
    out = {}
    for name, yaw, pitch, roll in ROUGHNESS_VIEWS:
        ctr = 0.5 * (model.V[Fp].reshape(-1, 3).min(0) + model.V[Fp].reshape(-1, 3).max(0))
        ext = float(np.max(np.ptp(model.V[Fp].reshape(-1, 3), axis=0)))
        size = int(ext * px_per_mm * 1.2) + 20
        shape = (size, size)
        cam = raster.view_camera(frame, yaw, pitch, px_per_mm, shape, roll=roll, center_mm=ctr)
        m = raster.render(model.V, Fp, cam, frame, shape, 1, None)["mask"]
        m = ndimage.binary_fill_holes(m)
        cs = measure.find_contours(np.pad(m.astype(float), 2), 0.5)
        if not cs:
            continue
        c = max(cs, key=len)
        seg = np.hypot(*np.diff(c, axis=0).T)
        L = float(seg.sum())
        sig = ROUGHNESS_SIGMA_MM * px_per_mm
        cs_ = np.stack([ndimage.gaussian_filter1d(c[:, k], sig / max(np.median(seg), 1e-6), mode="wrap") for k in (0, 1)], 1)
        Ls = float(np.hypot(*np.diff(cs_, axis=0).T).sum())
        out[name] = round(L / max(Ls, 1e-9), 4)
    return out


def integrity(models: dict, gen: generator.Generator) -> dict:
    """Model-only integrity of the BSA model (the review's shopper-visible defects the photo metrics miss):
    floating parts, and off-axis silhouette roughness against the generator's. REVIEW flags: floating_part,
    rough_silhouette."""
    bsa = models.get("bsa")
    if bsa is None:
        return {}
    out = {"flags": []}
    fl = floating_parts(bsa)
    out["floating"] = fl
    if fl.get("floating"):
        out["flags"].append("floating_part")
    ref = models.get("generator")
    rb = silhouette_roughness(bsa, gen.frame, slab_cut_z(bsa, gen))
    rg = silhouette_roughness(ref, gen.frame, slab_cut_z(ref, gen)) if ref is not None else {}
    ratio = {v: round((rb[v] - 1.0) / max(rg[v] - 1.0, 1e-4), 3) for v in rb if v in rg}
    out["roughness"] = {"bsa": rb, "generator": rg, "excess_ratio": ratio,
                        "rule": f"(length / smoothed length - 1) of the BSA front-piece silhouette over the generator's; "
                                f"flag above {ROUGHNESS_MAX_RATIO} in any view"}
    if any(r > ROUGHNESS_MAX_RATIO for r in ratio.values()):
        out["flags"].append("rough_silhouette")
    return out


# ============================================================================== stage
def _strip(o):
    """JSON-safe copy (drops arrays)."""
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if not isinstance(v, np.ndarray)}
    if isinstance(o, (list, tuple)):
        return [_strip(v) for v in o]
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def finalize(result: dict, run: str = "m1") -> dict:
    """(Re)compute the M1 criteria, the plan gates, the decision and the gate validation from the per-model
    records of a result (no rendering); also used to re-decide a saved result after a threshold change."""
    product = result["product"]
    recs = result["models"]
    s9p = run_dir(run, product) / "s9_export" / "result.json"
    s9 = json.loads(s9p.read_text()) if s9p.exists() else None
    val = validation(recs)
    if any("gt_frame_truth" not in r for r in recs.values()):   # results saved before the rule existed
        from . import ground_truth
        s2res, _ = _s2(product, run)
        gt = ground_truth.load(product) if ground_truth.json_path(product).exists() else None
        truth = gt_frame_truth(gt, s2_view(s2res))
        for r in recs.values():
            r.setdefault("gt_frame_truth", truth)
    sens = (val.get("unaffected_deltas") or {}).get("temples_x085_angled_front_piece_delta_pct_w") if val else None
    crit = {n: criteria(r, recs.get("previous"), s9 if n == "bsa" else None, sens if n == "bsa" else None)
            for n, r in recs.items() if r["kind"] in ("bsa", "previous", "control", "reference")}
    gates = {n: plan_gates(r, recs.get("previous")) for n, r in recs.items() if r["kind"] != "fault"}
    flags = list(result.get("load_flags", []))
    # S7/S8 fallbacks (a harness failure left a known-bad material) reach the decision
    stage_flags = {}
    for st, wanted in STAGE_REVIEW_FLAGS.items():
        rp = run_dir(run, product) / st / "result.json"
        if rp.exists():
            got = [f for f in json.loads(rp.read_text()).get("flags", []) if f in wanted]
            stage_flags[st] = got
            flags += got
    if (crit.get("bsa") or {}).get("c2_lens_edge_vs_gt", {}).get("applicable") is False:
        flags.append("no_lens_edge_criterion")
    integ = result.get("integrity") or {}
    flags += [f for f in integ.get("flags", []) if f in REVIEW_RULES]
    prev = recs.get("previous")
    if prev is not None:
        pl = prev["info"]["placement"]
        if pl["residual_candidate_to_generator_mm"]["median"] > 0.5:
            flags.append("previous_alignment_residual_high")
    if val and not val.get("all_pass"):
        flags.append("gate_validation_failed")
    vc = result.get("lens_view_consistency")
    if vc is None:                      # results saved before the check existed: compute it from the artifacts
        vc = lens_view_consistency(product, run)
    if vc.get("flag"):
        flags.append("lens_views_inconsistent")
    lc = lens_colour(product, run)
    if lc.get("flag"):
        flags.append(lc["flag"])
    out = dict(result)
    out.update({"decision": decide(crit.get("bsa"), list(result.get("s2_flags", [])) + flags, "bsa" in recs),
                "m1_criteria": crit, "plan_gates": gates, "validation": val, "flags": flags,
                "stage_review_flags": stage_flags, "lens_view_consistency": vc, "lens_colour": lc})
    head = {k: out[k] for k in ("stage", "product", "run", "decision", "m1_criteria", "plan_gates", "validation", "flags")
            if k in out}
    return _strip({**head, **{k: v for k, v in out.items() if k not in head}})


def lens_colour(product: str, run: str = "m1") -> dict:
    """S8's rendered lens-colour check read for the decision: {rendered_dE00, limit, flag} with flag
    ``lens_colour_mismatch`` above ``LENS_COLOUR_DE00_MAX``, ``lens_colour_unchecked`` when S8 carries no check (the
    harness failure is S8's own flag, ``lens_colour_check_failed``)."""
    rp = run_dir(run, product) / "s8_lens" / "result.json"
    out = {"limit_dE00": LENS_COLOUR_DE00_MAX, "rendered_dE00": None, "flag": None}
    if not rp.exists():
        out["flag"] = "lens_colour_unchecked"
        return out
    chk = json.loads(rp.read_text()).get("lens_colour_check")
    if not chk:
        out["flag"] = "lens_colour_unchecked"
    elif chk.get("ok"):
        out["rendered_dE00"] = chk.get("rendered_dE00")
        out["rendered_dE00_pooled"] = chk.get("rendered_dE00_pooled")
        if chk.get("rendered_dE00") is None or chk["rendered_dE00"] > LENS_COLOUR_DE00_MAX:
            out["flag"] = "lens_colour_mismatch"
    return out


def refinalize(product: str, run: str = "m1") -> dict:
    """Re-decide a saved s10_gate result in place (criteria/gates/decision/validation only)."""
    sd = stage_dir(run, product, STAGE)
    result = finalize(json.loads(sd.result_path.read_text()), run)
    tmp = sd.result_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=1))
    tmp.replace(sd.result_path)
    return result


def run(product: str, run: str = "m1", force: bool = False, faults: bool = True, sheet: bool = True, log=print) -> dict:
    sd = stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return sd.load()[0]
    t0 = time.time()
    gen = generator.load(product, run)
    frame, cams, s3res = C.load_cameras(product, run)
    s2res, _ = _s2(product, run)
    prod = PRODUCTS[product]
    models: dict[str, Model] = {}
    load_flags = []
    try:
        bsa = bsa_model(product, run)
    except Exception as e:           # noqa: BLE001  (an upstream stage writing its arrays right now)
        bsa = None
        load_flags.append(f"bsa_model_unreadable:{type(e).__name__}")
    if bsa is not None:
        models["bsa"] = bsa
    prev = None
    if prod.candidate_glb is not None and Path(prod.candidate_glb).exists():
        prev = load_glb_model("previous", prod.candidate_glb, gen, "previous")
        models["previous"] = prev
    card = tilted_card(product, gen, cams, run)
    models["card"] = card
    models["generator"] = generator_model(gen, cams, product, run)
    if faults and prev is not None:
        s0v = json.loads((run_dir(run, product) / "s0_intake" / "result.json").read_text())["views"]
        models.update(make_faults(prev, gen, cams, {v: tuple(s0v[v]["shape"]) for v in s0v}))
    if faults:
        models["card_lens_dilate_1mm"] = tilted_card(product, gen, cams, run, 1.0, "card_lens_dilate_1mm")
    ev = evaluate(product, models, run, log=log, keep_tiles=sheet)
    tiles = ev.pop("_tiles", {})
    recs = ev["models"]
    integ = integrity(models, gen)
    result = {"stage": STAGE, "product": product, "run": run, "integrity": integ,
              "models": recs, "context": ev["context"], "s2_flags": s2res.get("flags", []),
              "policy": {"refine_bounds": REFINE_BOUNDS, "refine_level_px": REFINE_LEVEL_PX,
                         "refine_maxfev": REFINE_MAXFEV, "refine_loss": "cameras.Level loss (1 - soft IoU + 4 x boundary / width), full silhouette, perspective fixed",
                         "temple_mask": f"behind-slab projection outside the front-piece projection, dilated {TEMPLE_DILATE_MM} mm, plus its Voronoi region vs the front piece; per model",
                         "slab": "z < model z_max - S1 front_depth_mm", "contours": "hole-filled silhouettes; mm via local px/mm at the S1 front-piece centre",
                         "lens": f"visible lens (first hit) at {LENS_SS}x, outer boundary, components >= {LENS_MIN_AREA_MM2} mm2; GT frame-bounded non-occluded, symmetric mean",
                         "seam": f"hole components <= {SEAM_MAX_WIDTH_MM} mm wide touching the lens silhouette at {SEAM_PX_PER_MM} px/mm where the front matte (eroded 1 px) shows material, components >= {SEAM_MIN_COMPONENT_PX} px",
                         "phantom": f"|x| < {PHANTOM_X_FRAC} W, z < z_max - {PHANTOM_Z_FRAC} W, face-area share",
                         "held_out": list(HELD_OUT_VIEWS), "criteria": {"c2_mm": CRIT2_MM, "c3_pct_w": CRIT3_PCT_W},
                         "review_rules": REVIEW_RULES, "informational_flags": list(INFORMATIONAL_FLAGS),
                         "view_consistency_k": VIEW_CONSISTENCY_K},
              "inputs": {"s3_frame": frame.to_dict(), "previous_glb": str(prod.candidate_glb) if prev else None,
                         "bsa_source": (bsa.info.get("source") or bsa.glb) if bsa else None},
              "load_flags": load_flags, "lens_view_consistency": lens_view_consistency(product, run),
              "timings_s": round(time.time() - t0, 1)}
    result = finalize(_strip(result), run)
    sd.save(result, {"card_V": card.V.astype(np.float32), "card_F": card.F, "card_labels": card.labels})
    if sheet:
        make_sheet(product, run, result, tiles, sd.root / "sheet.png")
    return result


# ============================================================================== sheet
def _font(size: int):
    return C._font(size)


def _tile(photo: np.ndarray, fg: np.ndarray, tl: dict, width: int) -> np.ndarray:
    x0, y0, x1, y1 = tl["roi"]
    rgb = photo[y0:y1, x0:x1].astype(np.float32)
    ref = fg[y0:y1, x0:x1]
    m = tl["render"]
    po, ro = ref & ~m, m & ~ref
    rgb[po] = rgb[po] * 0.35 + np.array([235, 30, 30]) * 0.65
    rgb[ro] = rgb[ro] * 0.35 + np.array([30, 200, 30]) * 0.65
    M = tl["mask"]
    rgb[M] = rgb[M] * 0.6 + np.array([255, 230, 120]) * 0.4
    k = width / rgb.shape[1]
    small = cv2.resize(rgb.astype(np.uint8), (width, max(1, int(round(rgb.shape[0] * k)))), interpolation=cv2.INTER_AREA)
    mk = cv2.resize(m.astype(np.uint8) * 255, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) >= 128
    small[C._edge(mk)] = (20, 60, 255)
    return small


def make_sheet(product: str, run: str, result: dict, tiles: dict, path: Path, tile_w: int = 330) -> None:
    prod = PRODUCTS[product]
    s0arr = dict(np.load(run_dir(run, product) / "s0_intake" / "arrays.npz", allow_pickle=False))
    photos = {v: load_photo(prod, v) for v in VIEWS}
    names = [n for n in ("bsa", "previous", "card", "generator") + FAULTS if n in result["models"]]
    font, small = _font(17), _font(14)
    label_w = 200
    rows = []
    for n in names:
        rec = result["models"][n]
        ims = []
        for v in VIEWS:
            if (n, v) not in tiles:
                ims.append(np.full((10, tile_w, 3), 255, np.uint8))
                continue
            im = _tile(photos[v], s0arr[f"fg_{v}"], tiles[(n, v)], tile_w)
            vm = rec["views"][v]
            pim = Image.fromarray(im)
            d = ImageDraw.Draw(pim)
            fp, tp = vm["front_piece"], vm["temple"]
            txt = f"FP {fp.get('mean_mm', float('nan')):.2f}mm {fp.get('pct_w', float('nan')):.2f}%W IoU {fp['iou']:.3f}"
            if v in ("left", "right") and tp.get("mean_mm") is not None:
                txt += f"\ntemple {tp['mean_mm']:.2f}mm"
            d.rectangle([0, 0, tile_w, 36 if "\n" in txt else 19], fill=(255, 255, 255))
            d.text((3, 1), txt, fill=(0, 0, 0), font=small)
            ims.append(np.asarray(pim))
        h = max(i.shape[0] for i in ims)
        row = Image.new("RGB", (label_w + len(VIEWS) * (tile_w + 4), h + 4), (255, 255, 255))
        for j, i in enumerate(ims):
            row.paste(Image.fromarray(i), (label_w + j * (tile_w + 4), 2))
        d = ImageDraw.Draw(row)
        lens = rec.get("lens") or {}
        s2m = (lens.get("s2") or {}).get("symmetric_mean_mm")
        gtm = (lens.get("gt") or {}).get("frame_symmetric_mean_mm")
        seam = rec.get("seam") or {}
        lines = [n, f"[{rec['kind']}]", f"lens vs S2 {s2m:.2f} mm" if s2m is not None else "lens vs S2 -",
                 f"lens vs GT {gtm:.2f} mm" if gtm is not None else "lens vs GT -",
                 f"gap px {seam.get('gap_pixels', '-')}", f"phantom {rec['phantom']['pct']:.2f}%",
                 f"side temple {temple_side_mean_mm(rec):.2f} mm" if temple_side_mean_mm(rec) is not None else ""]
        d.multiline_text((6, 6), "\n".join(lines), fill=(0, 0, 0), font=small, spacing=3)
        rows.append(row)
    # lens panel: S2-source photo crop with GT (green), S2 (magenta) and model lens outlines
    lens_panel = _lens_panel(product, run, result, tiles, photos)
    head = Image.new("RGB", (rows[0].width if rows else 1200, 64), (255, 255, 255))
    d = ImageDraw.Draw(head)
    dec = result["decision"]
    d.text((6, 4), f"S10 gate {product} ({run})  decision {dec['decision']}  {', '.join(dec['reasons'])[:150]}",
           fill=(0, 0, 0), font=font)
    d.text((6, 30), "red = photo only, green = render only, blue = render contour, yellow tint = the model's temple mask; "
           "FP = temple-masked front-piece contour (refined frozen camera); angled is held out", fill=(60, 60, 60), font=small)
    colh = Image.new("RGB", (head.width, 24), (255, 255, 255))
    d = ImageDraw.Draw(colh)
    for j, v in enumerate(VIEWS):
        d.text((label_w + j * (tile_w + 4) + 4, 3), v + (" (HELD OUT)" if v in HELD_OUT_VIEWS else ""), fill=(0, 0, 0), font=font)
    seam_panel = _seam_panel(result, tiles)
    parts = [head, colh] + rows + [p for p in (lens_panel, seam_panel) if p is not None]
    Wt = max(p.width for p in parts)
    sheet = Image.new("RGB", (Wt, sum(p.height for p in parts)), (255, 255, 255))
    y = 0
    for p in parts:
        sheet.paste(p, (0, y))
        y += p.height
    sheet.save(path)


def _seam_panel(result: dict, tiles: dict, width: int = 620) -> Image.Image | None:
    """Front render at ~10 px/mm per model: grey frame, light blue lens silhouette, red gap pixels (dilated)."""
    ims = []
    for n in ("bsa", "previous", "card", "generator"):
        m = tiles.get((n, "seam"))
        if m is None:
            continue
        A, L, g, wide = m
        img = np.full(A.shape + (3,), 255, np.uint8)
        img[A] = (150, 150, 150)
        img[L & A] = (150, 200, 255)
        img[L & ~A] = (200, 225, 255)
        img[cv2.dilate(wide.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)] = (255, 160, 0)
        gd = cv2.dilate(g.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        img[gd] = (255, 0, 0)
        k = width / img.shape[1]
        small = cv2.resize(img, (width, max(1, int(round(img.shape[0] * k)))), interpolation=cv2.INTER_AREA)
        small[cv2.resize(gd.astype(np.uint8) * 255, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) > 0] = (255, 0, 0)
        pim = Image.fromarray(small)
        d = ImageDraw.Draw(pim)
        s = result["models"][n]["seam"]
        d.text((4, 2), f"{n}: gap px {s.get('gap_pixels')} ({s.get('gap_area_mm2', 0):.2f} mm2), wide {s.get('wide_holes_touching_lens_px')}, "
                       f"cracks {s.get('single_px_cracks')}",
               fill=(0, 0, 0), font=_font(15))
        ims.append(pim)
    if not ims:
        return None
    Wt = sum(i.width + 6 for i in ims)
    out = Image.new("RGB", (Wt, max(i.height for i in ims) + 26), (255, 255, 255))
    ImageDraw.Draw(out).text((4, 2), "seam check (front camera, ~10 px/mm): red = sliver gaps (<= 1 mm) touching the lens over "
                             "photo material; orange = wider such holes (reported, not gaps)",
                             fill=(0, 0, 0), font=_font(15))
    x = 0
    for i in ims:
        out.paste(i, (x, 24))
        x += i.width + 6
    return out


def _lens_panel(product: str, run: str, result: dict, tiles: dict, photos: dict) -> Image.Image | None:
    from . import ground_truth
    s2res, s2arr = _s2(product, run)
    view = s2_view(s2res)
    img = photos[view]
    shape = img.shape[:2]
    rings = s2_lens_polys(s2res, s2arr, shape)
    allp = np.vstack(rings)
    m = 0.08 * np.ptp(allp[:, 0])
    x0, x1 = int(max(allp[:, 0].min() - m, 0)), int(min(allp[:, 0].max() + m, shape[1]))
    y0, y1 = int(max(allp[:, 1].min() - m, 0)), int(min(allp[:, 1].max() + m, shape[0]))
    k = 1500 / (x1 - x0)
    crop = Image.fromarray(img[y0:y1, x0:x1]).resize((int((x1 - x0) * k), int((y1 - y0) * k)), Image.LANCZOS)
    d = ImageDraw.Draw(crop)
    xf = lambda P: [((p[0] - x0 + 0.5) * k - 0.5, (p[1] - y0 + 0.5) * k - 0.5) for p in P]
    cols = {"previous": (0, 200, 255), "card": (255, 140, 0), "bsa": (30, 60, 255), "lens_dilate_1mm": (255, 255, 0)}
    gts = ground_truth.lenses(product, view) if ground_truth.json_path(product).exists() else []

    def draw_all(dd, tf, w):
        for r in rings:
            dd.line(tf(np.vstack([r, r[:1]])), fill=(255, 0, 220), width=w)
        for l in gts:
            P = np.asarray(l["points_px"], float)
            dd.line(tf(np.vstack([P, P[:1]])), fill=(40, 220, 40), width=w)
        for n, col in cols.items():
            for L in tiles.get((n, "lens_lines")) or []:
                dd.line(tf(L), fill=col, width=max(1, w - 1))

    draw_all(d, xf, 2)
    font = _font(15)
    d.rectangle([0, 0, 1500, 22], fill=(255, 255, 255))
    d.text((4, 3), f"lens edges on the {view} photo: magenta S2, green ground truth, cyan previous, orange card, "
           "blue BSA, yellow lens_dilate_1mm (insets: 12 mm squares)", fill=(0, 0, 0), font=font)
    # zoomed insets: top, outer side and bottom of each outline, 12 mm square
    ppm = 1.0 / float(s2res["mm_per_px_provisional"])
    half = max(8, int(round(6.0 * ppm)))
    zoom = 250.0 / (2 * half)
    axis = float(np.mean([r[:, 0].mean() for r in rings]))
    insets = []
    for r in rings[:2]:
        outer = r[np.argmax(np.abs(r[:, 0] - axis))]
        for c in (r[np.argmin(r[:, 1])], outer, r[np.argmax(r[:, 1])]):
            cx = int(np.clip(round(c[0]) - half, 0, max(shape[1] - 2 * half, 0)))
            cy = int(np.clip(round(c[1]) - half, 0, max(shape[0] - 2 * half, 0)))
            ins = Image.fromarray(img[cy:cy + 2 * half, cx:cx + 2 * half]).resize((250, 250), Image.NEAREST)
            dd = ImageDraw.Draw(ins)
            draw_all(dd, lambda P, cx=cx, cy=cy: [((p[0] - cx + 0.5) * zoom, (p[1] - cy + 0.5) * zoom) for p in P], 2)
            insets.append(ins)
    if not insets:
        return crop
    out = Image.new("RGB", (max(crop.width, len(insets) * 256), crop.height + 256), (255, 255, 255))
    out.paste(crop, (0, 0))
    for i, ins in enumerate(insets):
        out.paste(ins, (i * 256, crop.height + 3))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="S10 gate")
    ap.add_argument("--product", choices=list(PRODUCTS))
    ap.add_argument("--run", default="m1")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--refinalize", action="store_true", help="re-decide saved results without re-rendering")
    a = ap.parse_args(argv)
    for p in ([a.product] if a.product else list(PRODUCTS)):
        r = refinalize(p, a.run) if a.refinalize else run(p, a.run, a.force)
        print(p, json.dumps(r["decision"]), json.dumps(r.get("validation", {}).get("card_vs_previous_angled")))


if __name__ == "__main__":
    main()
