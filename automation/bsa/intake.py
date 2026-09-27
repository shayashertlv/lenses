"""S0 intake: per-view glasses matte and lens proposal at native resolution.

Method (DESIGN.md S0):
- backdrop: quadratic Lab model fitted robustly to the image border ring (handles grey
  gradients such as the RayBan back photo);
- foreground: contrast to the backdrop with the low-chroma shadow rule (a pixel that is
  only a moderate, achromatic darkening is a floor shadow, not glasses), bounded 3 px
  hysteresis growth into weak contrast (anti-aliased edges only);
- floor reflections: a vertical flip test about a horizontal mirror row below the object:
  (a) attached band: weak, attenuated pixels below the row whose mirror source is
  foreground, with no strong object pixel below them in their column and outside the lens
  proposal; flip correlation = explained share > 0.6 and >= 0.2 % of the foreground -> cut;
  (b) detached components below the object that flip onto it (> 0.6) and are attenuated
  and weak -> cut;
- components smaller than 0.2 % of the largest are dropped; for front/back/angled the
  offline lens proposal (detector `contrast_crop` variant) is united into the matte, minus its
  backdrop OPENINGS (``lens_openings``): enclosed backdrop the proposal swallowed next to a tinted
  lens (a brow vent; the backdrop a back view encloses under the lens), found with the non-lens carve
  that S2 shares (``carve_nonlens``, backdrop evidence). The proposal (``lens_<view>``) excludes them too.

Artifacts (`data/bsa/runs/<run>/<product>/s0_intake/`): arrays `fg_<view>`, `lens_<view>`
(bool, native HxW), extra `matte_<view>` (the pure contrast matte before the lens union,
reflection cuts applied; S2 builds the frame mask from it), `lens_openings_<view>` (front/back/angled: the
backdrop openings cut from the proposal before the union), `result.json` with per-view shape, backdrop, fg pixels, bbox, flags,
mirror IoU (front/back) and photo sha256. The quadratic backdrop coefficients are stored
too (`backdrop_lab_coef`) so later stages can rebuild the backdrop with `backdrop_lab`.
"""
from __future__ import annotations

import time

import cv2
import numpy as np
from scipy import ndimage

from . import core

STAGE = "s0_intake"
LENS_VIEWS = ("front", "back", "angled")

# Matte policy (fixed for every product and view; never tuned per photo).
T_LO = 7.0            # weak contrast: reachable only by the 3 px hysteresis growth
T_MID = 11.0          # contrast that counts when chromatic or inside a lens proposal
T_HI = 24.0           # strong contrast: foreground on its own
CHROMA_AB = 10.0      # |ab - ab_backdrop| above which a moderate contrast is not a shadow
SHADOW_DARK_WEIGHT = 0.6   # achromatic darkening counts at 60 % (shadow rule)
GROW_PX = 3
MIN_COMPONENT_FRACTION = 0.002
REFLECTION_NCC = 0.6
REFLECTION_ATTENUATION = 0.8
LOW_RES_WIDTH_PX = 600
MIRROR_IOU_LOW = 0.94

# Non-lens carve, shared by S0 (``lens_openings``: backdrop openings a tinted lens proposal swallowed) and S2
# (front.py steps 3b/3c). Chromaticity (a*, b*) / (L* + 16) is darkening-invariant above the CIELAB toe.
BAND_L_MIN = 8.0                     # CIELAB linear toe: below L* = 8, (a, b) / (L* + 16) is not darkening-invariant
BAND_CHROMA_SEP = 0.12               # ~3x the chromaticity noise (+/-1 a/b unit of JPEG noise at L* ~ 10: 1/26)
CARVE_CORE_MM = 1.5                  # lens core = lens mask eroded by this (clear of the edge and its blur)
CARVE_REF_SIGMA_MM = 3.0             # local lens colour reference (a gradient / mirror lens varies over ~10 mm)
CARVE_RING_MM = (0.5, 3.0)           # frame reference: matte pixels outside the lens between these distances
CARVE_FRAME_SIGMA_MM = 1.0           # smoothing of the frame reference along the ring
CARVE_MIN_LAB = 15.0                 # not lens-coloured: Lab distance to the local lens colour above max(this, core p97)
CARVE_LENS_P = 97.0
CARVE_THICK_MM = 0.6                 # thinner structures are the edge refinement's business (bevel, groove, blur)
CARVE_EVIDENCE_MM2 = 1.0             # confident non-lens area a carved component must contain
CARVE_RING_DEPTH_MM = 0.5            # frame reference pixels lie >= this inside the matte (frame >= 1 mm thick)
CARVE_FRAME_TOL_MIN = 3.0            # frame-coloured tolerance floor (Lab; JPEG noise on a flat frame)
VENT_FRAME_SHARE = 0.3               # an enclosed opening is a vent when this share of its surroundings is frame-like
                                     # (``opening_onto_frame``: oakley's brow vents 0.44-0.46, a highlight disc touching
                                     # the rim 0.12-0.18; the threshold sits between them)

_ENGINE = None


# --------------------------------------------------------------------------- colour

def lab_image(rgb: np.ndarray) -> np.ndarray:
    """8-bit sRGB -> float32 Lab (L 0..100, a/b centred on 0) via OpenCV's D65 transform."""
    lab = cv2.cvtColor(np.ascontiguousarray(rgb, np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)
    lab[..., 0] *= 100.0 / 255.0
    lab[..., 1:] -= 128.0
    return lab


def _design(h: int, w: int, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    u = xs / float(w)
    v = ys / float(h)
    return np.stack([np.ones_like(u), u, v, u * u, v * v, u * v], 1)


def backdrop_model(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Robust quadratic fit of each Lab channel to the border ring.

    Returns (backdrop Lab image HxWx3 float32, coefficients (3, 6))."""
    h, w = lab.shape[:2]
    b = max(4, int(0.02 * min(h, w)))
    ring = np.zeros((h, w), bool)
    ring[:b] = ring[-b:] = True
    ring[:, :b] = ring[:, -b:] = True
    ys, xs = np.nonzero(ring)
    X = _design(h, w, ys.astype(float), xs.astype(float))
    coef = np.zeros((3, 6))
    for c in range(3):
        v = lab[..., c][ring].astype(float)
        keep = np.ones(len(v), bool)
        for _ in range(3):   # IRLS: drop ring pixels that belong to an object touching the border
            cc, *_ = np.linalg.lstsq(X[keep], v[keep], rcond=None)
            r = np.abs(X @ cc - v)
            keep = r < max(2.0, 3.0 * float(np.median(r[keep])) + 1e-6)
        coef[c] = cc
    return backdrop_lab(coef, (h, w)), coef


def backdrop_lab(coef: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Rebuild the backdrop Lab image from stored coefficients (3, 6)."""
    h, w = int(shape[0]), int(shape[1])
    yy, xx = np.mgrid[0:h, 0:w]
    X = _design(h, w, yy.ravel().astype(float), xx.ravel().astype(float))
    return (X @ np.asarray(coef, float).T).reshape(h, w, 3).astype(np.float32)


def lab_to_rgb(lab_px: np.ndarray) -> np.ndarray:
    """Float Lab (L 0..100) -> 8-bit sRGB for a small array of colours (..., 3)."""
    a = np.asarray(lab_px, np.float32).reshape(-1, 1, 3).copy()
    a[..., 0] *= 255.0 / 100.0
    a[..., 1:] += 128.0
    rgb = cv2.cvtColor(np.clip(np.round(a), 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)
    return rgb.reshape(np.asarray(lab_px).shape)


def contrast_score(lab: np.ndarray, bg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shadow-weighted contrast to the backdrop and the chromatic difference |ab - ab_bg|."""
    dL = lab[..., 0] - bg[..., 0]
    dab = np.sqrt(((lab[..., 1:] - bg[..., 1:]) ** 2).sum(-1))
    score = np.sqrt((SHADOW_DARK_WEIGHT * np.minimum(dL, 0)) ** 2 + np.maximum(dL, 0) ** 2 + dab ** 2)
    return score.astype(np.float32), dab.astype(np.float32)


# --------------------------------------------------------------------------- masks

def chromaticity(lab: np.ndarray) -> np.ndarray:
    """(a*, b*) / (L* + 16): unchanged when a colour is darkened (linear RGB x k scales L* + 16, a* and b*
    by k^(1/3) above the CIELAB linear toe, L* > 8)."""
    lab = np.asarray(lab, np.float64)
    return lab[..., 1:] / (lab[..., :1] + 16.0)


def _nconv(values: np.ndarray, mask: np.ndarray, sigma: float) -> tuple[np.ndarray, np.ndarray]:
    """Normalized Gaussian convolution of a (H, W, C) field over ``mask``: the local mean of the masked values and
    the kernel weight that supported it."""
    w = ndimage.gaussian_filter(mask.astype(np.float32), sigma)
    out = np.stack([ndimage.gaussian_filter(np.where(mask, values[..., c], 0.0).astype(np.float32), sigma)
                    for c in range(values.shape[-1])], -1)
    return out / np.maximum(w, 1e-6)[..., None], w


def carve_nonlens(R: np.ndarray, lab: np.ndarray, matte: np.ndarray, bg_lab: np.ndarray | None, mm_px: float,
                  evidence: tuple[str, ...] = ("frame", "backdrop")) -> tuple[np.ndarray, np.ndarray, dict]:
    """Non-lens structure inside a lens mask (front.py module doc, steps 3b/3c; S0 ``lens_openings``). The edge refinement moves points along the
    proposal's normals; it cannot remove material the detector swallowed WHOLE: a centre stem joining the brow to the
    nose piece, an endpiece or nose pad over the lens edge, a brow vent (backdrop seen between the lens's top edge
    and the brow). Returns (carve mask, class map: 1 backdrop, 2 frame, 0 elsewhere, info).

    Per pixel of R, against LOCAL references (the lens colour varies: gradients, mirror coatings):
    - lens colour: normalized Gaussian (CARVE_REF_SIGMA_MM) over the lens core (R eroded CARVE_CORE_MM);
      not lens-coloured = Lab distance > max(CARVE_MIN_LAB, the core's own p97);
    - frame colour: the nearest FRAME ring pixel's smoothed colour and chromaticity; the ring is the matte outside R
      (CARVE_RING_MM) at least CARVE_RING_DEPTH_MM inside the matte (frame structure, not a free lens edge's own
      grazing band or an anti-aliased halo); frame-coloured = within the ring's own p75 deviation from that reference
      (the frame's texture and JPEG noise; CARVE_FRAME_TOL_MIN at least);
    - backdrop: the S0 contrast score below T_MID.
    The connecting set is "not lens-coloured and closer to the frame than to the lens (but not within CARVE_THICK_MM
    of the open backdrop: there a dark band is a free edge's own grazing band), or enclosed backdrop" (backdrop the
    glasses surround: not connected to the crop border). A component of it (opened to CARVE_THICK_MM, with the
    enclosed backdrop outside R as support; touching the outside of R) is carved (inside R) when it holds at least
    CARVE_EVIDENCE_MM2 of CONFIDENT non-lens evidence of a kind in ``evidence``:
    - "backdrop": ENCLOSED backdrop while the lens core itself contrasts with the backdrop (median score >= T_HI: a
      tinted lens cannot look like the backdrop; a clear lens can, so a clear lens never gives this evidence), and
      only an opening onto the FRAME (``opening_onto_frame``: it continues beyond R, or frame-like structure surrounds
      it): a specular highlight on the lens that touches the rim is enclosed backdrop colour too, but lens surrounds it;
    - "frame": frame-coloured, the lens is tinted and distinct from the frame (|qL|, |qL - qF| >= BAND_CHROMA_SEP), the
      pixel is measurable (L* >= BAND_L_MIN) and NOT lens-tinted, i.e. less than half-way from the frame and from
      neutral toward the lens chromaticity (the band rule's test, step 2b, inverted). Frame seen THROUGH a tinted lens
      keeps the lens's chromaticity (T * frame + r) and its colour departs from the frame's; a neutral or clear lens
      cannot tell frame seen through it from frame in front of it, so it never gives this evidence (nor does a lens
      whose core median chroma is below BAND_CHROMA_SEP). The pixel must lie within CARVE_RING_MM[1] of the ring (the
      frame next to it, not a frame part across the lens). A clear lens gives no evidence of either kind.
    Dark unmeasurable pixels (black, L* < BAND_L_MIN) only connect evidence to the outside (the black brow lip over a
    vent), they are never evidence."""
    H, W = R.shape
    empty = np.zeros((H, W), bool)
    info = {"components": 0, "carved_px": 0, "carved_mm2": 0.0}
    core = ndimage.binary_erosion(R, iterations=max(1, int(round(CARVE_CORE_MM / mm_px))))
    if core.sum() < 200 or R.all():
        return empty, np.zeros((H, W), np.int8), info
    ys, xs = np.nonzero(R)
    pad = int(np.ceil(3 * CARVE_REF_SIGMA_MM / mm_px + CARVE_RING_MM[1] / mm_px)) + 2
    y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + pad + 1)
    Rc, core_c, matte_c = R[y0:y1, x0:x1], core[y0:y1, x0:x1], matte[y0:y1, x0:x1]
    lab_c = lab[y0:y1, x0:x1].astype(np.float64)
    # the lens reference must not be the structure it is to find (a 5 mm stem is inside the core): a first pass drops
    # the core pixels far from it (median + 3 robust sigma), the second is the reference
    lens_ref, _ = _nconv(lab_c, core_c, CARVE_REF_SIGMA_MM / mm_px)
    d0 = np.linalg.norm(lab_c - lens_ref, axis=-1)[core_c]
    med0 = float(np.median(d0))
    keep0 = np.linalg.norm(lab_c - lens_ref, axis=-1) <= max(CARVE_MIN_LAB, med0 + 3.0 * 1.4826 * float(np.median(np.abs(d0 - med0))))
    if (core_c & keep0).sum() >= 200:
        core_c = core_c & keep0
        lens_ref, _ = _nconv(lab_c, core_c, CARVE_REF_SIGMA_MM / mm_px)
    dL = np.linalg.norm(lab_c - lens_ref, axis=-1)
    t = max(CARVE_MIN_LAB, float(np.percentile(dL[core_c], CARVE_LENS_P)))
    dist_out = ndimage.distance_transform_edt(~Rc)
    depth = ndimage.distance_transform_edt(np.pad(matte_c, 1))[1:-1, 1:-1]
    ring = (matte_c & ~Rc & (dist_out * mm_px >= CARVE_RING_MM[0]) & (dist_out * mm_px <= CARVE_RING_MM[1])
            & (depth * mm_px >= CARVE_RING_DEPTH_MM))
    if ring.sum() < 20:
        return empty, np.zeros((H, W), np.int8), info
    q = chromaticity(lab_c)
    meas = lab_c[..., 0] >= BAND_L_MIN
    frame_ref, _ = _nconv(lab_c, ring, CARVE_FRAME_SIGMA_MM / mm_px)
    tol_f = max(CARVE_FRAME_TOL_MIN, float(np.percentile(np.linalg.norm(lab_c - frame_ref, axis=-1)[ring], 75)))
    # a frame too dark to measure has no chromaticity: neutral (as in band_extend)
    qF_ring, _ = _nconv(np.where(meas[..., None], q, 0.0), ring, CARVE_FRAME_SIGMA_MM / mm_px)
    d_ring, (iy, ix) = ndimage.distance_transform_edt(~ring, return_indices=True)
    fref = frame_ref[iy, ix]
    qF = qF_ring[iy, ix]
    dF = np.linalg.norm(lab_c - fref, axis=-1)
    qL, wq = _nconv(q, core_c & meas, CARVE_REF_SIGMA_MM / mm_px)
    far = Rc & (dL > t)
    back_like = np.zeros_like(Rc)
    lens_contrast = None
    if bg_lab is not None:
        score, _ = contrast_score(lab[y0:y1, x0:x1], bg_lab[y0:y1, x0:x1])
        back_like = score < T_MID
        lens_contrast = float(np.median(score[core_c]))
    # a clear lens looks like the backdrop and shows the frame behind it unchanged: no evidence of either kind
    clear = lens_contrast is None or lens_contrast < T_HI
    # backdrop evidence is an ENCLOSED opening (a vent: backdrop the glasses surround), never the open backdrop a free
    # edge or an end gap meets (its anti-aliased rim pixels inside R are connected to the crop border)
    hl, _ = ndimage.label(back_like & ~matte_c, np.ones((3, 3), int))
    border = np.unique(np.r_[hl[0], hl[-1], hl[:, 0], hl[:, -1]])
    open_back = (hl > 0) & np.isin(hl, border[border > 0])
    enclosed = (hl > 0) & ~open_back
    # an enclosed opening is a VENT only when it opens onto the FRAME (``opening_onto_frame``); a highlight on a tinted
    # lens that touches the rim is enclosed too, but lens colour surrounds it
    enclosed, op_info = opening_onto_frame(hl, enclosed, Rc, matte_c, (dL > t) & (dF < dL), mm_px)
    info["openings"] = op_info
    back_ev = far & enclosed
    if "backdrop" not in evidence or clear:
        back_ev = np.zeros_like(Rc)
    ax = qL - qF
    na = np.linalg.norm(ax, axis=-1)
    nl = np.linalg.norm(qL, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        proj = np.minimum(((q - qF) * ax).sum(-1) / np.maximum(na * na, 1e-12),
                          (q * qL).sum(-1) / np.maximum(nl * nl, 1e-12))
    # the lens is chromatic (the core's median chroma): a neutral lens cannot tell frame seen through it from frame in
    # front of it, and a local reference contaminated by hardware inside the core must not make it look tinted
    chroma_core = float(np.median(np.linalg.norm(q[core_c & meas], axis=-1))) if (core_c & meas).any() else 0.0
    tinted = (na >= BAND_CHROMA_SEP) & (nl >= BAND_CHROMA_SEP) & (wq > 1e-3) & (chroma_core >= BAND_CHROMA_SEP)
    # continuous with the frame: the reference is the frame next to the pixel, not a frame part across the lens (a free
    # edge's own grazing band has no frame within reach)
    frame_ev = far & (dF <= tol_f) & (dF < dL) & meas & tinted & (proj < 0.5) & (d_ring * mm_px <= CARVE_RING_MM[1])
    if "frame" not in evidence or clear:
        frame_ev = np.zeros_like(Rc)
    # the connecting set never runs along the open backdrop: within CARVE_THICK_MM of it a dark band is a free edge's
    # own grazing band (the lens's edge), as the free-edge refinement treats it, not frame
    near_open = ndimage.distance_transform_edt(~open_back) * mm_px <= CARVE_THICK_MM
    connect = far & (((dF < dL) & ~near_open) | enclosed)
    # the opening keeps structures at least CARVE_THICK_MM thick (a (2 r + 1) px disc). A vent the outline only grazes
    # is thin INSIDE R: its enclosed part outside R (backdrop not connected to the crop border) joins the support
    holes = enclosed & ~Rc
    r = max(1, int(np.floor((CARVE_THICK_MM / mm_px - 1.0) / 2.0 + 0.5)))
    disc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    opened = cv2.morphologyEx((connect | holes).astype(np.uint8), cv2.MORPH_OPEN, disc).astype(bool)
    comp, n = ndimage.label(opened, np.ones((3, 3), int))
    carve = np.zeros_like(Rc)
    vent = np.zeros_like(Rc)
    if n:
        outside = ndimage.binary_dilation(~Rc, iterations=r + 1)
        touches = np.zeros(n + 1, bool)
        touches[np.unique(comp[opened & outside])] = True
        ev = ndimage.sum_labels(back_ev | frame_ev, comp, np.arange(1, n + 1)) * mm_px * mm_px
        keep = np.zeros(n + 1, bool)
        keep[1:] = touches[1:] & (ev >= CARVE_EVIDENCE_MM2)
        carve = keep[comp] & Rc
        # the whole enclosed opening a kept component holds (inside and outside R): not glasses at all
        vl = np.unique(hl[keep[comp] & enclosed])
        vent = np.isin(hl, vl[vl > 0])
        info["components"] = int(keep.sum())
        info["evidence_mm2"] = [round(float(v), 2) for v in ev[keep[1:]]]
    # class map: 2 frame carved from the lens, 1 an enclosed opening (vent) - also its part outside R
    cls = np.zeros(Rc.shape, np.int8)
    cls[carve & ~back_like] = 2
    cls[vent] = 1
    out, cls_out = empty.copy(), np.zeros((H, W), np.int8)
    out[y0:y1, x0:x1] = carve
    cls_out[y0:y1, x0:x1] = cls
    info.update({"carved_px": int(carve.sum()), "carved_mm2": round(float(carve.sum()) * mm_px * mm_px, 2),
                 "lens_threshold_lab": round(t, 2), "frame_tolerance_lab": round(tol_f, 2), "open_radius_px": r,
                 "lens_backdrop_contrast": None if lens_contrast is None else round(lens_contrast, 2),
                 "lens_core_chroma": round(chroma_core, 3), "vent_px": int((cls == 1).sum()), "frame_px": int((cls == 2).sum())})
    return out, cls_out, info


def opening_onto_frame(hl: np.ndarray, enclosed: np.ndarray, R: np.ndarray, matte: np.ndarray, frame_like: np.ndarray,
                       mm_px: float) -> tuple[np.ndarray, dict]:
    """The enclosed backdrop openings (``hl`` labels under ``enclosed``) that open onto the FRAME, i.e. are a vent (a
    hole between the lens edge and the frame) and not a highlight ON the lens. Backdrop colour alone cannot tell them
    apart (a specular highlight on a tinted lens is as white as a studio backdrop, and one that touches the rim is
    enclosed and touches the outside of R like a vent); what surrounds them can:
    - it continues BEYOND R by at least ``CARVE_THICK_MM`` (the proposal swallowed only part of it: a lens cannot
      extend past its own proposal, so this is backdrop between parts of the glasses), or
    - at least ``VENT_FRAME_SHARE`` of its surroundings (the matte within ``CARVE_THICK_MM`` of it) is frame-like: not
      lens-coloured and closer to the local frame colour than to the lens (``frame_like``). A vent lies between the
      lens's edge and the frame, one whole side against the frame (oakley's four front brow vents, which the proposal
      swallows whole: 44-46 % frame-like; its back-view openings continue 10-11 mm beyond the proposal), a highlight on
      the lens is surrounded by lens colour (a disc touching the rim: 12-18 %, where it meets the rim).
    Limitation: a highlight STREAK hugging the rim along its whole length has a vent's geometry (48 % on a synthetic
    streak) and one photo cannot tell them apart. Returns (the qualified openings, info)."""
    out = np.zeros_like(enclosed)
    info = {"enclosed": 0, "vents": 0, "accepted": [], "rejected": []}
    labs = np.unique(hl[enclosed])
    labs = labs[labs > 0]
    if not len(labs):
        return out, info
    reach = max(1, int(np.ceil(CARVE_THICK_MM / mm_px)))
    dist_R = ndimage.distance_transform_edt(~R) * mm_px
    objs = ndimage.find_objects(hl)
    H, W = hl.shape
    for lb in labs:
        sl = objs[lb - 1]
        y0, y1 = max(0, sl[0].start - reach - 1), min(H, sl[0].stop + reach + 1)
        x0, x1 = max(0, sl[1].start - reach - 1), min(W, sl[1].stop + reach + 1)
        comp = hl[y0:y1, x0:x1] == lb
        outside = comp & ~R[y0:y1, x0:x1]
        beyond = float(dist_R[y0:y1, x0:x1][outside].max()) if outside.any() else 0.0
        ring = ndimage.binary_dilation(comp, iterations=reach) & ~comp & matte[y0:y1, x0:x1]
        share = float(frame_like[y0:y1, x0:x1][ring].mean()) if ring.any() else 0.0
        info["enclosed"] += 1
        rec = {"px": int(comp.sum()), "beyond_mm": round(beyond, 3), "frame_share": round(share, 3)}
        if beyond >= CARVE_THICK_MM or share >= VENT_FRAME_SHARE:
            out[y0:y1, x0:x1] |= comp
            info["vents"] += 1
            info["accepted"].append(rec)
        else:
            info["rejected"].append(rec)
    for k in ("accepted", "rejected"):              # the largest openings of each kind (the report stays small)
        info[k] = sorted(info[k], key=lambda r: -r["px"])[:8]
    return out, info


def lens_openings(lab: np.ndarray, lens: np.ndarray, pure: np.ndarray, bg_lab: np.ndarray, mm_px: float) -> tuple[np.ndarray, dict]:
    """Backdrop OPENINGS inside the detector's lens proposal: the proposal is evidence of a lens, not of material, and
    it swallows enclosed openings next to the lens (a brow vent between the lens's top edge and the brow; the backdrop
    a back view encloses between the lens's lower edge, a nose pad and the floor shadow). Such a pixel is backdrop-
    coloured (contrast < T_MID), outside the pure matte, not connected to the photo border (enclosed by the glasses),
    and its opening joins the outside of the proposal through a structure that is not lens-coloured (``carve_nonlens``
    with backdrop evidence, >= CARVE_EVIDENCE_MM2). A tinted lens cannot look like the backdrop (proposal core median
    contrast >= T_HI); a clear lens can, so a clear proposal is never cut, and a highlight surrounded by lens colour
    never joins the outside nor, touching the rim, counts as an opening (``opening_onto_frame``). Returns (opening mask,
    info); the openings are not united into fg."""
    C, cls, info = carve_nonlens(np.asarray(lens, bool), lab, pure, bg_lab, mm_px, evidence=("backdrop",))
    op = cls == 1
    return op, {"components": info.get("components", 0), "evidence_mm2": info.get("evidence_mm2", []),
                "opening_px": int(op.sum()), "proposal_px_cut": int((op & lens).sum()), "openings": info.get("openings"),
                "lens_backdrop_contrast": info.get("lens_backdrop_contrast"), "mm_per_px": round(float(mm_px), 5)}


def keep_components(mask: np.ndarray, min_fraction: float = MIN_COMPONENT_FRACTION) -> np.ndarray:
    """Drop 8-connected components smaller than `min_fraction` of the largest one."""
    lab, n = ndimage.label(mask, np.ones((3, 3), int))
    if n == 0:
        return mask.copy()
    sizes = ndimage.sum_labels(mask, lab, np.arange(1, n + 1))
    keep = np.zeros(n + 1, bool)
    keep[1:] = sizes >= min_fraction * sizes.max()
    return keep[lab]


def mirror_x(a: np.ndarray, x0: float) -> np.ndarray:
    """Mirror an image about the vertical line x = x0 (x0 may be a half-integer).
    Pixels whose mirror source falls outside the image become False / 0."""
    W = a.shape[1]
    xs = np.arange(W)
    src = 2.0 * x0 - xs
    idx = np.clip(np.round(src).astype(int), 0, W - 1)
    out = a[:, idx].copy()
    invalid = (src < -0.5) | (src > W - 0.5)
    out[:, invalid] = False if a.dtype == bool else 0
    return out


def iou(a: np.ndarray, b: np.ndarray) -> float:
    return float((a & b).sum() / max(1, (a | b).sum()))


def symmetry_axis(mask: np.ndarray, search_fraction: float = 0.06, step: float = 0.5) -> tuple[float, float]:
    """Vertical mirror axis maximising the mask's mirror IoU (coarse 4 px, then `step`)."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return float(mask.shape[1] - 1) / 2.0, 0.0
    x_lo, x_hi = int(xs.min()), int(xs.max())
    pad = int(np.ceil(search_fraction * (x_hi - x_lo))) + 2
    c0 = max(0, x_lo - pad)
    c1 = min(mask.shape[1], x_hi + pad + 1)
    r0, r1 = int(ys.min()), int(ys.max()) + 1
    crop = mask[r0:r1, c0:c1]
    c = (x_lo + x_hi) / 2.0 - c0
    span = x_hi - x_lo

    def score(x0):
        return iou(crop, mirror_x(crop, x0))

    coarse = np.arange(c - search_fraction * span, c + search_fraction * span + 1e-9, 4.0)
    vals = [score(x) for x in coarse]
    xb = float(coarse[int(np.argmax(vals))])
    fine = np.arange(xb - 4.0, xb + 4.0 + 1e-9, step)
    vals = [score(x) for x in fine]
    k = int(np.argmax(vals))
    return float(fine[k] + c0), float(vals[k])


def _flip_overlap(ys: np.ndarray, xs: np.ndarray, target: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """For each mirror row y_m in `rows`: share of the pixels (ys, xs) whose vertical mirror
    image (2 y_m - y, x) lands on `target`."""
    H = target.shape[0]
    out = np.zeros(len(rows))
    for i, ym in enumerate(rows):
        fy = 2 * ym - ys
        ok = (fy >= 0) & (fy < H)
        out[i] = target[fy[ok], xs[ok]].sum() / max(1, len(ys))
    return out


REFLECTION_WEAK = 30.0        # a floor reflection on these studio floors is weak in absolute terms
REFLECTION_MIN_SHARE = 0.002  # cut area must reach 0.2 % of the foreground to count as a reflection


def _explained(fg, score, ym, rows, protect=None):
    """Pixels on `rows` (below the mirror row ym) that are an attenuated, weak mirror image
    of foreground above ym, are not protected (lens proposal) and have no strong object
    pixel below them in their column (an object always sits above its own reflection)."""
    src_rows = 2 * ym - rows
    ok = (src_rows >= 0)
    rows, src_rows = rows[ok], src_rows[ok]
    B = fg[rows]
    S = score[rows]
    Ssrc = score[src_rows]
    E = B & fg[src_rows] & (S < REFLECTION_ATTENUATION * Ssrc) & (S < REFLECTION_WEAK)
    if protect is not None:
        E &= ~protect[rows]
    blocking = B & ~E & (S >= REFLECTION_WEAK)
    below = np.flipud(np.cumsum(np.flipud(blocking), 0) > 0)   # blocking at this row or below
    E &= ~below
    return rows, E, B & (S < REFLECTION_WEAK) & ~below


def reflection_band_cut(fg: np.ndarray, score: np.ndarray, protect: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Floor reflection attached to the object (e.g. through a temple tip).

    For each mirror row y_m in the lower 70 % of the object (searched on a <= 500 px grid),
    E(y_m) = foreground below y_m that is weak (< REFLECTION_WEAK), attenuated (< 0.8 x) and
    whose vertical mirror source is foreground, with no strong object pixel below it in its
    column and outside the lens proposal, opened by 1 px so scattered texture noise and
    thin anti-aliased edges cannot qualify. The row with the largest E wins; the flip
    correlation is |E| / |weak foreground below y_m that could physically be a reflection|. A reflection needs correlation >
    REFLECTION_NCC and |E| >= REFLECTION_MIN_SHARE of the foreground; then E (grown 1 px
    inside the foreground) is cut at native resolution."""
    info = {"ncc": 0.0, "mirror_row": None, "cut_pixels": 0}
    ys = np.nonzero(fg.any(1))[0]
    if len(ys) < 12:
        return fg, info
    H, W = fg.shape
    f = min(1.0, 500.0 / max(H, W))
    size = (max(1, int(round(W * f))), max(1, int(round(H * f))))
    fg_s = cv2.resize(fg.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST).astype(bool)
    pr_s = None if protect is None else cv2.resize(protect.astype(np.uint8), size, interpolation=cv2.INTER_NEAREST).astype(bool)
    sc_s = cv2.resize(score, size, interpolation=cv2.INTER_AREA)
    ys_s = np.nonzero(fg_s.any(1))[0]
    y0, y1 = int(ys_s.min()), int(ys_s.max())
    h = y1 - y0
    best = (0, None, 0.0)
    for ym in range(y0 + int(0.3 * h), y1 - 1):
        rows = np.arange(ym + 1, y1 + 1)
        _, E, weak = _explained(fg_s, sc_s, ym, rows, pr_s)
        E = ndimage.binary_opening(E, iterations=1)
        a = int(E.sum())
        if a > best[0]:
            best = (a, ym, a / max(1, int(weak.sum())))
    if best[1] is None:
        return fg, info
    info["ncc"] = round(float(best[2]), 4)
    min_area = REFLECTION_MIN_SHARE * fg.sum()
    if best[2] <= REFLECTION_NCC or best[0] / (f * f) < min_area:
        return fg, info
    ym = int(round((best[1] + 0.5) / f - 0.5))
    rows = np.arange(ym + 1, int(ys.max()) + 1)
    rows, E, _ = _explained(fg, score, ym, rows, protect)
    E = ndimage.binary_opening(E, iterations=max(1, int(round(1 / f))))
    E = ndimage.binary_dilation(E, iterations=1) & fg[rows]
    if protect is not None:
        E &= ~protect[rows]
    out = fg.copy()
    out[rows] &= ~E
    info["mirror_row"] = ym
    info["cut_pixels"] = int(E.sum())
    return out, info


def floor_reflection_cut(fg: np.ndarray, score: np.ndarray, protect: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Band test (attached reflections) followed by the component test (detached ones).
    `protect` (the dilated lens proposal) is never cut by the band test."""
    fg1, band = reflection_band_cut(fg, score, protect)
    fg2, comp = reflection_component_cut(fg1, score)
    info = {"ncc": max(band["ncc"], comp["ncc"]), "mirror_row": band["mirror_row"] if band["mirror_row"] is not None
            else comp["mirror_row"], "cut_pixels": band["cut_pixels"] + comp["cut_pixels"],
            "band": band, "components": comp}
    return fg2, info


def reflection_component_cut(fg: np.ndarray, score: np.ndarray) -> tuple[np.ndarray, dict]:
    """Cut floor reflections: a component below the object that is a flipped, attenuated copy.

    Every connected component other than the main object whose centre lies below the main
    object's median row is flipped vertically about each candidate mirror row y_m between
    the object's top and the component's top. The flip correlation is the share of the
    flipped component that lands on the main object; a peak above REFLECTION_NCC (0.6)
    together with attenuation (mean contrast below REFLECTION_ATTENUATION x the contrast of
    the object pixels it mirrors) marks a reflection, which is removed. Attached parts of the
    object (temple tips resting on the floor) are never touched: they are in the main
    component, and reflections attached by weak contrast do not survive the 3 px growth."""
    info = {"ncc": 0.0, "mirror_row": None, "cut_pixels": 0, "cut_components": 0}
    lab, n = ndimage.label(fg, np.ones((3, 3), int))
    if n < 2:
        return fg, info
    sizes = ndimage.sum_labels(fg, lab, np.arange(1, n + 1))
    main_id = int(np.argmax(sizes)) + 1
    main = lab == main_id
    ys_main = np.nonzero(main)[0]
    y_top, y_med = int(ys_main.min()), float(np.median(ys_main))
    objs = ndimage.find_objects(lab)
    out = fg.copy()
    rows_cut = []
    for k in range(1, n + 1):
        if k == main_id or objs[k - 1] is None:
            continue
        sl = objs[k - 1]
        ys, xs = np.nonzero(lab[sl] == k)
        ys = ys + sl[0].start
        xs = xs + sl[1].start
        if ys.mean() <= y_med:
            continue
        if len(ys) > 4000:   # deterministic subsample for speed
            idx = np.linspace(0, len(ys) - 1, 4000).astype(int)
            ys_s, xs_s = ys[idx], xs[idx]
        else:
            ys_s, xs_s = ys, xs
        rows = np.arange(y_top, int(ys.min()) + 1)
        if len(rows) == 0:
            continue
        ov = _flip_overlap(ys_s, xs_s, main, rows)
        j = int(np.argmax(ov))
        ncc, ym = float(ov[j]), int(rows[j])
        if ncc > info["ncc"]:
            info["ncc"] = round(ncc, 4)
        if ncc <= REFLECTION_NCC:
            continue
        fy = 2 * ym - ys
        ok = (fy >= 0) & main[np.clip(fy, 0, fg.shape[0] - 1), xs]
        src = float(score[fy[ok], xs[ok]].mean()) if ok.any() else 0.0
        own = float(score[ys, xs].mean())
        if own >= REFLECTION_ATTENUATION * src or own >= REFLECTION_WEAK:
            continue
        out[ys, xs] = False
        info["cut_pixels"] += int(len(ys))
        info["cut_components"] += 1
        rows_cut.append(ym)
    if rows_cut:
        info["mirror_row"] = int(np.median(rows_cut))
    return out, info


def matte(rgb: np.ndarray, lens: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Foreground matte of one photo (before the lens union). Returns (mask, info)."""
    lab = lab_image(rgb)
    bg, coef = backdrop_model(lab)
    score, dab = contrast_score(lab, bg)
    lens = np.zeros(score.shape, bool) if lens is None else lens
    strong = (score > T_HI) | ((score > T_MID) & ((dab > CHROMA_AB) | lens))
    grown = ndimage.binary_dilation(strong, iterations=GROW_PX)
    fg = strong | ((score > T_LO) & grown)
    fg = ndimage.binary_opening(fg, iterations=1) | (strong & fg)
    fg = keep_components(fg)
    return fg, {"backdrop_lab_coef": coef, "score": score,
                "backdrop_lab_center": bg[bg.shape[0] // 2, bg.shape[1] // 2]}


def detector():
    global _ENGINE
    if _ENGINE is None:
        from reconstruction.photo_apertures import OfflineLensApertureEngine
        _ENGINE = OfflineLensApertureEngine(core.DETECTOR_WEIGHTS, runtime_dir=core.BSA_DATA / ".detector_runtime")
    return _ENGINE


def lens_proposal(rgb: np.ndarray) -> np.ndarray:
    return np.asarray(detector().propose(rgb)["variants"]["contrast_crop"]["mask"], bool)


def bbox_xyxy(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def front_piece_width_px(pure: np.ndarray, lens: np.ndarray) -> float:
    """Width (px) of the FRONT PIECE in a lens view: the matte's extent over the rows the lens proposal spans (the
    endpieces sit at lens height). The whole matte is wider wherever a temple reaches beyond the endpieces (a back
    view's temples come toward the camera and may splay), which would shrink every millimetre threshold."""
    rows = np.nonzero(np.asarray(lens, bool).any(1))[0]
    band = np.asarray(pure, bool)[rows.min():rows.max() + 1] if len(rows) else np.asarray(pure, bool)
    xs = np.nonzero(band.any(0))[0]
    if not len(xs):
        xs = np.nonzero(np.asarray(pure, bool).any(0))[0]
    return float(xs.max() - xs.min() + 1)


def intake_view(rgb: np.ndarray, view: str, lens: np.ndarray | None = None,
                front_width_mm: float = core.DEFAULT_FRONT_WIDTH_MM) -> tuple[np.ndarray, np.ndarray, dict]:
    """Matte + lens proposal of one view. `lens` may be injected (tests); otherwise the
    offline detector runs for front/back/angled and left/right get an all-False mask.
    The pure contrast matte (before the lens union) is returned in info["matte"]. The lens proposal minus its
    backdrop openings (``lens_openings``; millimetres from ``front_width_mm`` over the front piece's width,
    ``front_piece_width_px``) is united
    into the foreground."""
    H, W = rgb.shape[:2]
    if view in LENS_VIEWS:
        lens = lens_proposal(rgb) if lens is None else np.asarray(lens, bool)
    else:
        lens = np.zeros((H, W), bool)
    fg, info = matte(rgb, lens)
    pure = fg.copy()
    flags = []
    openings = None
    if view in LENS_VIEWS:
        if lens.any() and pure.any():
            mm_px = float(front_width_mm) / front_piece_width_px(pure, lens)
            bg = backdrop_lab(info["backdrop_lab_coef"], (H, W))
            openings, info["lens_openings"] = lens_openings(lab_image(rgb), lens, pure, bg, mm_px)
            if info["lens_openings"]["proposal_px_cut"]:
                flags.append("lens_openings_cut")
            lens = lens & ~openings
        fg = fg | lens
    protect = ndimage.binary_dilation(lens, iterations=3) if lens.any() else None
    fg, info["reflection"] = floor_reflection_cut(fg, info["score"], protect)
    fg = keep_components(fg)
    lens = lens & fg
    pure &= fg     # reflection cuts apply to the pure matte too
    if info["reflection"]["mirror_row"] is not None:
        flags.append("floor_reflection_cut")
    bb = bbox_xyxy(fg)
    border = bool(fg[:2].any() or fg[-2:].any() or fg[:, :2].any() or fg[:, -2:].any())
    if border:
        flags.append("border_contact")
    width_px = (bb[2] - bb[0] + 1) if bb else 0
    if width_px < LOW_RES_WIDTH_PX:
        flags.append("low_resolution")
    out = {"shape": [H, W],
           "backdrop_rgb": [int(x) for x in lab_to_rgb(info["backdrop_lab_center"][None])[0]],
           "backdrop_lab_coef": np.round(info["backdrop_lab_coef"], 6).tolist(),
           "fg_pixels": int(fg.sum()), "lens_pixels": int(lens.sum()), "bbox_xyxy": bb, "width_px": int(width_px),
           "reflection": info["reflection"], "flags": flags, "matte_pixels": int(pure.sum())}
    if "lens_openings" in info:
        out["lens_openings"] = info["lens_openings"]
    if view in ("front", "back"):
        axis, m_iou = symmetry_axis(fg)
        out["mirror_iou"] = round(m_iou, 4)
        out["mirror_axis_x"] = axis
        if m_iou < MIRROR_IOU_LOW:
            flags.append("mirror_iou_low")
    out["_matte"] = pure   # popped by run(); stored as the extra array matte_<view>
    if openings is not None:
        out["_openings"] = openings & ~fg   # popped by run(); extra array lens_openings_<view>
    return fg, lens, out


def run(product: str, run: str = "m1", force: bool = False) -> dict:
    sd = core.stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return sd.load()[0]
    prod = core.PRODUCTS[product]
    t0 = time.time()
    views, arrays = {}, {}
    for view in core.VIEWS:
        rgb = core.load_photo(prod, view)
        fg, lens, info = intake_view(rgb, view, front_width_mm=prod.front_width_mm)
        arrays[f"matte_{view}"] = info.pop("_matte")
        if "_openings" in info:
            arrays[f"lens_openings_{view}"] = info.pop("_openings")
        info["photo_sha256"] = core.sha256_file(prod.photo_path(view))
        views[view] = info
        arrays[f"fg_{view}"] = fg
        arrays[f"lens_{view}"] = lens
    result = {"stage": STAGE, "product": product, "run": run, "views": views,
              "detector": detector().describe()["weights"],
              "policy": {"T_LO": T_LO, "T_MID": T_MID, "T_HI": T_HI, "CHROMA_AB": CHROMA_AB,
                         "shadow_dark_weight": SHADOW_DARK_WEIGHT, "grow_px": GROW_PX,
                         "min_component_fraction": MIN_COMPONENT_FRACTION, "reflection_ncc": REFLECTION_NCC,
                         "lens_openings": {"carve_thick_mm": CARVE_THICK_MM, "evidence_mm2": CARVE_EVIDENCE_MM2,
                                           "tinted_lens_contrast_min": T_HI}},
              "seconds": round(time.time() - t0, 1)}
    sd.save(result, arrays)
    _sheet(product, run, arrays, views)
    return result


def _sheet(product: str, run: str, arrays: dict, views: dict) -> None:
    """Five-view overlay: backdrop tinted, matte blue edge, lens proposal red, lens openings cyan."""
    prod = core.PRODUCTS[product]
    tiles = []
    for view in core.VIEWS:
        rgb = core.load_photo(prod, view)
        fg, lens = arrays[f"fg_{view}"], arrays[f"lens_{view}"]
        ov = rgb.astype(np.float32)
        ov[~fg] = ov[~fg] * 0.45 + np.array([255, 90, 90]) * 0.55
        ov[lens] = ov[lens] * 0.5 + np.array([230, 40, 40]) * 0.5
        op = arrays.get(f"lens_openings_{view}")
        if op is not None and op.any():   # backdrop openings cut from the proposal: cyan, grown to stay visible
            op = ndimage.binary_dilation(op, iterations=max(1, int(max(rgb.shape) / 500)))
            ov[op] = (0, 220, 255)
        edge = fg & ~ndimage.binary_erosion(fg)
        grow = int(max(rgb.shape) / 700)
        if grow >= 1:   # iterations=0 would mean "until stable" and flood the tile
            edge = ndimage.binary_dilation(edge, iterations=grow)
        ov[edge] = (0, 60, 255)
        rm = views[view]["reflection"].get("mirror_row")
        ov = ov.astype(np.uint8)
        if rm is not None:
            cv2.line(ov, (0, rm), (ov.shape[1] - 1, rm), (0, 160, 0), max(1, int(max(rgb.shape) / 600)))
        s = 420.0 / max(ov.shape[:2])
        ov = cv2.resize(ov, (int(ov.shape[1] * s), int(ov.shape[0] * s)), interpolation=cv2.INTER_AREA)
        tile = np.full((420, 420, 3), 255, np.uint8)
        tile[:ov.shape[0], :ov.shape[1]] = ov
        v = views[view]
        txt = f"{view} fg={v['fg_pixels']} " + ",".join(v["flags"])
        cv2.putText(tile, txt[:60], (4, 412), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1, cv2.LINE_AA)
        tiles.append(tile)
    sheet = np.hstack(tiles)
    cv2.imwrite(str(core.stage_dir(run, product, STAGE).root / "sheet.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="m1")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("products", nargs="*", default=list(core.PRODUCTS))
    a = ap.parse_args()
    for p in a.products:
        r = run(p, a.run, a.force)
        print(p, json.dumps({v: {k: r["views"][v][k] for k in ("fg_pixels", "width_px", "flags", "reflection")}
                             for v in core.VIEWS}), flush=True)
