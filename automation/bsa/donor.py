"""Donor geometry: the 3D parts of the S1 generator that the constructed plate (S6) does not model.

The front PLATE (S6) is built from the photo outline: exact outline, exact lens seam, a prism along the S4 depth
axis between the S4 front surface and the plate back ``d_front(s, y) - t(s, y)`` (``depth.DepthField.plate_back_d``).
Everything the generator has BEHIND that plate back - endpiece / hinge blocks between the plate and the temple
arm, nose pads and pad arms, the back of a metal bridge - is taken from the generator here, cut cleanly on the
plate back (``OVERLAP_MM`` into the plate, so the join shows neither a void nor a seam), with the generator UVs
(textured exactly like the S5 temples: ``temple_basecolor.jpg`` = the generator basecolor).

Keep region (every condition is a scalar field on the generator's welded vertices, and every triangle straddling
a field's zero level is SPLIT on it - ``clip_mesh`` - so each cut is a clean surface, not a jagged face set):

1. behind the plate back: d(v) < d_front(s, y) - t(s, y) + OVERLAP_MM (param depth along the S4 depth axis, the
   axis S4 measured the generator's thickness along);
2. in front of the S5 split plane z = split_z (behind it the S5 arm takes over; the arm's loft reaches 1 mm into
   the donor);
3. inside the photo footprint: its projection through the S2 outline-source camera lies inside the symmetrised S2
   matte ``fg_sym`` dilated ``FOOTPRINT_DILATE_PX`` (the front view keeps the photo outline; material the photo
   does not show is not added);
4. in the donor ZONE of the S4 (s, y) grid: where the plate does not exist (lens areas, beyond the frame) or where
   the generator is thicker than the plate by >= ``EXCESS_MIN_MM`` at least ``EXCESS_EDGE_MM`` inside the plate
   (hardware; a ray grazing the plate's rounded edge travels far inside it), dilated ``ZONE_DILATE_MM``. Behind a
   rim band whose generator back is within ``EXCESS_MIN_MM`` of the plate back nothing is taken (it would be a
   patchwork of thin slivers over the photo-textured plate back);
   The zone also holds every (s, y) cell within ``JOIN_ZONE_MM`` of an S5 arm's cut loop (the temple root between
   the plate and the split is always donor). Beyond the front piece in (s, y) (neither plate nor lens cells dilated
   ``BEYOND_DILATE_MM``: a wrapped shield's temple root) there is no plate to be behind: rule 1 does not apply;
5. HARDWARE (``hardware_mask``: frame components that are not a rim band) comes from the generator whole: any
   geometry in front of the split projecting into the hardware region (dilated ``HW_DILATE_MM``, plus the lens area
   within ``HW_REACH_MM`` of it: drill mounts on a rimless lens), rules 1 and 4 do not apply (S6 keeps only a thin
   hidden core there);
6. inside every other fit view's silhouette (``hull_views``: the matte dilated max(2 px, 0.25 mm)) whose S3 camera
   is trusted (``trusted_hulls``: a view S3 down-weighted as ``low_iou`` does not carve - miu's side cameras, contour
   p95 ~5 mm, carved its outer hinge blocks and drill mounts to slivers);
7. per side: x > 0 (temple_R) or x < 0 (temple_L); a central part (a bridge back) is split on x = 0.
8. the LENS AREA away from the plate (S4 lens cells not within ``PLATE_CELL_DILATE_MM`` of a frame cell) has no plate
   to be behind: there rule 1 becomes "behind the generator's lens plate" (``behind_lens``), i.e. structure seen
   through the lens - a centre stem between brow and nose piece (INVU), a pad arm.
The generator's lens plate is removed first (faces projecting inside the S2 lens polygons dilated 1 mm AND within
the generator lens-plate slab of the S4 lens surface). On a RIMLESS front (S2 rim class) the generator's own colours
also separate its lens from its hardware (``hardware_over_lens``): the S2 lens polygon runs around the drill mounts,
screws and bars to the hinge, and the generator is one fused mesh whose drill mounts pass THROUGH its lens plate, so
the slab rule gutted them to flat slivers; a hardware-coloured connected part that holds S2 hardware is kept WHOLE
(whatever its depth, rules 1, 4 and 8 do not apply), and every lens-coloured face in the lens is lens. The rule only
runs when both colour references separate (else the slab rule alone, as before). Components smaller than
``MIN_COMPONENT_AREA_MM2`` or that never reach ``MIN_BEHIND_MM`` behind the plate back are dropped, unless hardware
or mostly in the lens area and anchored outside it (``LENS_AREA_SHARE``); then FLOATING components are dropped (a
non-hardware part, or a hardware fragment < ``HW_FRAGMENT_MM2``, must touch the plate, the side's S5 arm or an
anchored part within ``ANCHOR_GAP_MM``). The rest are decimated, their holes fan-capped (cut loops lie inside the
plate or against the other half), oriented outward and checked closed. The anchoring is tested once more on the
DELIVERED geometry by S6 (``assemble.donor_anchoring``, the S10 integrity rule on the plate as built: a part within
``SNAP_MAX_MM`` of contact is moved into it, a farther one dropped). A projection that crosses the source camera's near
plane falls back and is recorded (``projection_failures``; S5 flags ``donor_projection_failed``, a REVIEW rule).
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from scipy import ndimage

from . import depth
from .core import NormFrame

OVERLAP_MM = 0.3                 # the donor starts this far INSIDE the plate (no void, no visible seam)
EXCESS_MIN_MM = 3.0              # generator thicker than the plate by this much = hardware behind the plate
EXCESS_EDGE_MM = 1.0             # ... measured at least this far inside the plate (no grazing rays at its edge)
ZONE_DILATE_MM = 1.5
PLATE_CELL_DILATE_MM = 1.0       # S4 frame cells (frame mask eroded 0.5 mm) dilated back to the plate edge
FOOTPRINT_DILATE_PX = 1.5
LENS_EXCLUDE_DILATE_MM = 1.0
LENS_SLAB_MARGIN_MM = 0.7
LENS_SLAB_DEFAULT_MM = 3.0
MIN_COMPONENT_AREA_MM2 = 2.0
MIN_BEHIND_MM = 2.5
LENS_AREA_SHARE = 0.5            # a component this much in the lens area (no plate) is structure, kept (``_finish``)
ANCHOR_GAP_MM = 0.5              # a donor part must touch the plate, the arm or an anchored part within this
SNAP_MAX_MM = 0.5                # S6 (``assemble.donor_anchoring``): a part that lost contact by at most this much more
                                 # (decimation, caps, the plate as built) is moved into it
HW_FRAGMENT_MM2 = 25.0           # hardware smaller than this is a fragment: it must be anchored like any other part
TARGET_FACES_PER_SIDE = 8000
HW_DILATE_MM = 0.5               # hardware region (S2 px) dilated for generator/photo misregistration
HW_REACH_MM = 3.0                # ... plus the lens area within this of it
BEYOND_DILATE_MM = 1.5           # "beyond the front piece" = neither plate nor lens cells, dilated this much
JOIN_ZONE_MM = 6.0
GRAZE_COS = 0.342                # cos 70 deg: the source photo sees the plate's face at more than 70 deg: no plate
GRAZE_OVERLAP_MM = 0.5           # the donor reaches this far into the plate's side of that limit               # (s, y) cells within this of an S5 arm's cut loop are donor zone (the temple root)
RIM_MIN_CONTACT = 0.35           # a frame component is a RIM band when it bounds >= this share of a lens outline
HW_CONTACT_PX = 3.0
HW_MIN_AREA_MM2 = 1.0            # a non-rim frame component smaller than this is a speck (matte noise), not hardware
RIM_HUG_MM = 1.0                 # ... or when it hugs a lens outline: >= RIM_HUG_SHARE of it within RIM_HUG_MM of it
RIM_HUG_SHARE = 0.8
RIM_STRIP_ELONGATION = 3.0       # ... and is a strip (principal-axis length >= 3 x width)
HULL_TOL_PX = 2.0                # hull views: matte dilated max(2 px, 0.25 mm) (camera + matte misregistration)
HULL_TOL_MM = 0.25
HULL_MIN_WEIGHT = 1.0            # a hull view carves only where S3 trusts its camera (weight 1, not ``low_iou``)
COLOUR_RATIO = 4.0               # generator colour p(lens) / p(hardware) above which a face is lens-coloured
COLOUR_MIN_FACES = 200           # colour references smaller than this: no colour rule
COLOUR_SEPARATION = 0.9          # ... and each reference must classify itself this well (else no colour rule)
LENS_REF_AWAY_MM = 5.0           # lens colour reference: lens faces this far from any S2 hardware
_NUDGE = 1e-7


# ============================================================================ mesh clipping
def clip_mesh(V: np.ndarray, F: np.ndarray, phi: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The part of a triangle mesh where the per-vertex scalar ``phi`` < 0, phi linear along edges: triangles
    straddling the zero level are split there (one new vertex per cut EDGE, shared by both its triangles, so a
    closed input stays manifold along the cut). Returns (V2, F2, parent) with V2 = [V; cut points] (unused vertices
    are kept; ``compact`` removes them) and ``parent`` = index of the input face of each output face. Orientation
    is preserved."""
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    phi = np.asarray(phi, float).copy()
    phi[np.abs(phi) < _NUDGE] = _NUDGE                        # a vertex on the level counts as outside
    neg = phi < 0
    k = neg[F].sum(axis=1)
    keep_all = np.nonzero(k == 3)[0]
    part = np.nonzero((k == 1) | (k == 2))[0]
    out_F = [F[keep_all]]
    parent = [keep_all]
    newV = []
    if len(part):
        Fp = F[part]
        # rotate each straddling face so that its LONE vertex (the single inside one for k=1, the single outside
        # one for k=2) comes first; rotation keeps the orientation
        lone = np.where(k[part] == 1, np.argmax(neg[Fp], axis=1), np.argmax(~neg[Fp], axis=1))
        idx = (lone[:, None] + np.arange(3)[None, :]) % 3
        R = np.take_along_axis(Fp, idx, axis=1)
        a, b, c = R[:, 0], R[:, 1], R[:, 2]
        edges = np.concatenate([np.stack([a, b], 1), np.stack([a, c], 1)])
        es = np.sort(edges, axis=1)
        uniq, inv = np.unique(es, axis=0, return_inverse=True)
        inv = inv.ravel()
        pa, pb = phi[uniq[:, 0]], phi[uniq[:, 1]]
        t = np.clip(pa / (pa - pb), 1e-6, 1 - 1e-6)
        P = V[uniq[:, 0]] + t[:, None] * (V[uniq[:, 1]] - V[uniq[:, 0]])
        base = len(V)
        newV.append(P)
        m = len(part)
        pab, pac = base + inv[:m], base + inv[m:]
        one = k[part] == 1
        if one.any():                                          # a inside: (a, p_ab, p_ac)
            out_F.append(np.stack([a[one], pab[one], pac[one]], 1))
            parent.append(part[one])
        two = ~one
        if two.any():                                          # a outside: (p_ab, b, c) + (p_ab, c, p_ac)
            out_F.append(np.stack([pab[two], b[two], c[two]], 1))
            out_F.append(np.stack([pab[two], c[two], pac[two]], 1))
            parent += [part[two], part[two]]
    V2 = np.vstack([V] + newV) if newV else V.copy()
    return V2, np.vstack(out_F).astype(np.int64), np.concatenate(parent).astype(np.int64)


def compact(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Drop unreferenced vertices. Returns (V, F, old index of every kept vertex)."""
    used = np.unique(F.ravel())
    remap = np.full(len(V), -1, np.int64)
    remap[used] = np.arange(len(used))
    return V[used], remap[F], used


def face_areas(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    return 0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1)


# ============================================================================ fields
def sample_grid(grid: np.ndarray, s_axis: np.ndarray, y_axis: np.ndarray, s, y, outside: float) -> np.ndarray:
    """Bilinear sample of a (ny, ns) grid over (y_axis, s_axis) (either axis may be descending); ``outside`` beyond."""
    s, y = np.asarray(s, float), np.asarray(y, float)
    fs = (s - s_axis[0]) / (s_axis[1] - s_axis[0])
    fy = (y - y_axis[0]) / (y_axis[1] - y_axis[0])
    out = ndimage.map_coordinates(np.asarray(grid, float), [fy.ravel(), fs.ravel()], order=1, mode="constant",
                                  cval=outside)
    return out.reshape(s.shape)


def signed_distance(mask: np.ndarray, unit: float = 1.0) -> np.ndarray:
    """Signed distance (negative inside ``mask``) in ``unit`` per cell."""
    mask = np.asarray(mask, bool)
    if not mask.any():
        return np.full(mask.shape, 1e6)
    if mask.all():
        return np.full(mask.shape, -1e6)
    return (ndimage.distance_transform_edt(~mask) - ndimage.distance_transform_edt(mask)) * unit


def donor_zone(s4arr: dict, h: float) -> tuple[np.ndarray, dict]:
    """(s, y)-grid mask where donor geometry may be taken (module docstring, rule 4)."""
    cls = np.asarray(s4arr["param_class"])
    t_raw = np.asarray(s4arr["param_t_raw"], float)
    t_plate = np.asarray(s4arr["param_thickness"], float)
    plate = ndimage.binary_dilation(cls == 1, iterations=max(1, int(round(PLATE_CELL_DILATE_MM / h))))
    # a ray grazing the plate's rounded edge travels far inside it: excess only counts away from the plate edge
    interior = ndimage.distance_transform_edt(cls == 1) * h > EXCESS_EDGE_MM
    excess = (cls == 1) & interior & (t_raw > 0) & (t_raw - t_plate >= EXCESS_MIN_MM)
    it = max(1, int(round(ZONE_DILATE_MM / h)))
    hardware = ndimage.binary_dilation(excess, iterations=it)
    zone = ~plate | hardware
    return zone, {"plate_cells": int(plate.sum()), "hardware_cells": int(excess.sum()),
                  "hardware_share_of_plate": round(float(excess.sum() / max(1, (cls == 1).sum())), 4)}


def lens_slab_mm(s4arr: dict) -> float:
    """Generator lens-plate thickness (p95 of the entry/exit distance on lens samples) + margin."""
    cls = np.asarray(s4arr["param_class"])
    t = np.asarray(s4arr["param_t_raw"], float)
    sel = (cls >= 2) & (t > 0) & (t < 20)
    base = float(np.percentile(t[sel], 95)) if sel.sum() >= 50 else LENS_SLAB_DEFAULT_MM
    return base + LENS_SLAB_MARGIN_MM


# ============================================================================ visual hull of the fit views
class HullView:
    """A fit view used as a visual-hull constraint: its frozen S3 camera and the signed distance (px, negative
    inside) to its S0 matte dilated ``tol_px`` (camera and matte misregistration)."""

    def __init__(self, view: str, camera, matte: np.ndarray, frame: NormFrame, tol_px: float, ppm: float = 1.0):
        self.view, self.camera, self.frame, self.ppm = view, camera, frame, float(ppm)
        m = np.asarray(matte, bool)
        if tol_px > 0:
            m = ndimage.binary_dilation(m, iterations=max(1, int(math.ceil(tol_px))))
        self.sdf = signed_distance(m).astype(np.float32)
        self.tol_px = float(tol_px)
        self.failed = False

    def outside(self, P: np.ndarray) -> np.ndarray:
        """Signed distance (px) of each point's projection to the dilated matte (> 0: outside)."""
        from . import raster
        P = np.asarray(P, float).reshape(-1, 3)
        uv = raster._project_norm(self.frame.to_norm(P), self.camera)
        if uv is None:                       # geometry crosses this camera's near plane: it cannot carve (recorded)
            self.failed = True
            return np.full(len(P), -1.0)
        return ndimage.map_coordinates(self.sdf, [uv[:, 1], uv[:, 0]], order=1, mode="constant", cval=1e6)


def hull_views(product: str, run: str, src: depth.SourceView, gen, frame: NormFrame) -> list[HullView]:
    """Every FIT view except the outline source (``core.FIT_VIEWS``; the held-out angled view is never used), matte
    dilated max(``HULL_TOL_PX``, ``HULL_TOL_MM``)."""
    from . import cameras, core
    _, cams, s3 = cameras.load_cameras(product, run)
    a0 = core.stage_dir(run, product, "s0_intake").load()[1]
    ctr = cameras.front_piece_centre(gen)
    out = []
    for v in core.FIT_VIEWS:
        if v == src.view or f"fg_{v}" not in a0:
            continue
        ppm_v = cameras.px_per_mm_at(cams[v], frame, ctr)
        h = HullView(v, cams[v], a0[f"fg_{v}"], frame, max(HULL_TOL_PX, HULL_TOL_MM * ppm_v), ppm_v)
        h.weight = float(((s3.get("cameras") or {}).get(v) or {}).get("weight", 1.0))
        out.append(h)
    return out


def trusted_hulls(hulls: list | None) -> tuple[list, list[str]]:
    """The hull views S3 trusts (weight 1). A view S3 down-weighted (``low_iou``: its silhouette does not fit the
    generator; miu's side cameras have a contour p95 of ~5 mm) misplaces a small part by millimetres and would carve
    0.25 mm-tolerance hardware to slivers. Returns (trusted views, the names of the skipped ones)."""
    kept, skipped = [], []
    for h in hulls or []:
        (kept if getattr(h, "weight", 1.0) >= HULL_MIN_WEIGHT else skipped).append(h)
    return kept, [h.view for h in skipped]


def hull_outside(hulls: list[HullView], P: np.ndarray) -> np.ndarray:
    """Max over the views of the signed distance (px) to the dilated mattes (> 0: outside some view)."""
    out = np.full(len(np.asarray(P).reshape(-1, 3)), -np.inf)
    for h in hulls:
        out = np.maximum(out, h.outside(P))
    return out


def hull_outside_mm(hulls: list[HullView], P: np.ndarray) -> np.ndarray:
    out = np.full(len(np.asarray(P).reshape(-1, 3)), -np.inf)
    for h in hulls:
        out = np.maximum(out, h.outside(P) / h.ppm)
    return out


# ============================================================================ grazing
def facing_cos(P: np.ndarray, df: depth.DepthField, src: depth.SourceView, frame: NormFrame,
               failures: list | None = None) -> np.ndarray:
    """|cos| of the angle between the S4 front-surface normal at P and the source camera's ray through P. The
    plate is built from the source photo's outline, which only determines the plate where the photo sees its face
    (a wrapped shield's lateral end, seen edge-on, is a thin sliver of the photo: the plate there would be a prism
    of a sliver); below ``GRAZE_COS`` the generator's own geometry is the better source (S5 donor)."""
    P = np.asarray(P, float).reshape(-1, 3)
    out = np.ones(len(P))
    if not len(P):
        return out
    try:
        px = src.project(P, frame)
    except ValueError:                        # near plane: no grazing test (``failures`` records it)
        if failures is not None:
            failures.append("facing")
        return out
    _, D = depth.camera_rays_mm(src.camera, frame, px, src.mirror_width)
    n = df.front_surface().normal(P)
    return np.abs(np.sum(n * D, axis=1))


def grazing_cells(s4arr: dict, df: depth.DepthField, src: depth.SourceView, frame: NormFrame,
                  failures: list | None = None) -> np.ndarray:
    """S4 (s, y) grid cells whose front point the source camera sees at grazing incidence (``facing_cos``)."""
    S, Y = np.meshgrid(np.asarray(s4arr["param_s"], float), np.asarray(s4arr["param_y"], float))
    P = df.base.from_param(S.ravel(), Y.ravel(), df.front(S.ravel(), Y.ravel()))
    return (facing_cos(P, df, src, frame, failures) < GRAZE_COS).reshape(S.shape)


# ============================================================================ hardware
def hardware_mask(frame_mask: np.ndarray, lens_polys: list[np.ndarray], lens_types: list[np.ndarray],
                  contact_px: float = HW_CONTACT_PX, ppm: float | None = None) -> tuple[np.ndarray, dict]:
    """Frame-mask components that are NOT a rim band: a rim band bounds (frame-bounded S2 lens edge within
    ``contact_px``) at least ``RIM_MIN_CONTACT`` of some lens's outline length, or (with ``ppm``) HUGS a lens: at
    least ``RIM_HUG_SHARE`` of its pixels lie within ``RIM_HUG_MM`` of a lens outline and it is a strip (elongation
    >= ``RIM_STRIP_ELONGATION``: a thin strip along a shield's lateral edge is rim, not a block). Everything else (a rimless frame's bridge, drill mounts and hinge blocks,
    loose hardware) is hardware and comes from the generator whole. Components are 8-connected (a pixel touching a rim
    diagonally is part of it) and, with ``ppm``, hardware is at least ``HW_MIN_AREA_MM2``: a 1-2 px speck of matte next
    to a lens is not a part, and the donor's hardware reach (``HW_REACH_MM`` into the lens) would take a 3 mm disc of
    generator geometry around it whole."""
    fm = np.asarray(frame_mask, bool)
    lab, n = ndimage.label(fm, np.ones((3, 3), int))
    out = np.zeros_like(fm)
    comps = []
    if n == 0:
        return out, {"components": 0}
    near = ndimage.distance_transform_edt(~fm, return_distances=False, return_indices=True)
    dist = ndimage.distance_transform_edt(~fm)
    H, W = fm.shape
    contact = np.zeros((n + 1, max(1, len(lens_polys))))
    perim = np.zeros(max(1, len(lens_polys)))
    for i, (poly, ty) in enumerate(zip(lens_polys, lens_types)):
        P = np.asarray(poly, float)
        seg = np.roll(P, -1, axis=0) - P
        L = np.hypot(seg[:, 0], seg[:, 1])
        perim[i] = L.sum()
        mid = P + 0.5 * seg
        ui = np.clip(np.round(mid[:, 0]).astype(int), 0, W - 1)
        vi = np.clip(np.round(mid[:, 1]).astype(int), 0, H - 1)
        ok = (np.asarray(ty) == 0) & (dist[vi, ui] <= contact_px)
        c = lab[near[0][vi[ok], ui[ok]], near[1][vi[ok], ui[ok]]]
        np.add.at(contact[:, i], c, L[ok])
    share = contact / np.maximum(perim[None, :], 1e-9)
    sizes = ndimage.sum(np.ones_like(lab), lab, np.arange(1, n + 1))
    hug = np.zeros(n + 1)
    if ppm is not None and lens_polys:
        ring_m = np.zeros(fm.shape, np.uint8)
        for poly in lens_polys:
            cv2.polylines(ring_m, [np.round(np.asarray(poly) * 16).astype(np.int32)], True, 1, shift=4)
        near_ring = ndimage.distance_transform_edt(~ring_m.astype(bool)) <= RIM_HUG_MM * ppm
        hug[1:] = ndimage.mean(near_ring, lab, np.arange(1, n + 1))
    # elongation (principal-axis length / width): a strip along a lens edge, not a compact block that happens to be
    # small enough to lie near the lens everywhere (a drill-mount screw head)
    elong = np.zeros(n + 1)
    ys_, xs_ = np.nonzero(lab)
    ls_ = lab[ys_, xs_]
    for c in range(1, n + 1):
        sel = ls_ == c
        if sel.sum() < 3:
            continue
        ev = np.linalg.eigvalsh(np.cov(np.stack([xs_[sel], ys_[sel]]).astype(float)) + 1e-6 * np.eye(2))
        elong[c] = float(np.sqrt(ev[1] / max(ev[0], 1e-9)))
    min_px = 0.0 if ppm is None else HW_MIN_AREA_MM2 * float(ppm) ** 2
    for c in range(1, n + 1):
        rim = bool(share[c].max() >= RIM_MIN_CONTACT or (hug[c] >= RIM_HUG_SHARE and elong[c] >= RIM_STRIP_ELONGATION))
        speck = bool(not rim and sizes[c - 1] < min_px)
        comps.append({"component": c, "pixels": int(sizes[c - 1]), "max_contact_share": round(float(share[c].max()), 4),
                      "hug_share": round(float(hug[c]), 4), "elongation": round(float(elong[c]), 2), "rim": rim,
                      "speck": speck})
        if not rim and not speck:
            out |= lab == c
    comps.sort(key=lambda d: -d["pixels"])
    return out, {"components": n, "hardware_components": int(sum(not c["rim"] and not c["speck"] for c in comps)),
                 "specks_dropped": int(sum(c["speck"] for c in comps)), "min_area_mm2": HW_MIN_AREA_MM2,
                 "hardware_px": int(out.sum()),
                 "rule": f"rim when a component bounds >= {RIM_MIN_CONTACT} of a lens outline or has >= {RIM_HUG_SHARE} "
                         f"of its pixels within {RIM_HUG_MM} mm of one",
                 "detail": comps[:12]}


# ============================================================================ hardware over the lens
_LAB_EDGES = (np.linspace(0, 100, 21), np.linspace(-90, 90, 31), np.linspace(-90, 90, 31))


def _lab(rgb: np.ndarray) -> np.ndarray:
    from skimage.color import rgb2lab
    return rgb2lab(np.asarray(rgb, float).reshape(-1, 1, 3) / 255.0).reshape(-1, 3)


def lab_hist(rgb: np.ndarray) -> np.ndarray:
    """Normalised 3D Lab histogram (5 L x 6 a/b units), Gaussian-smoothed one bin."""
    h, _ = np.histogramdd(_lab(rgb), bins=_LAB_EDGES)
    h = ndimage.gaussian_filter(h, 1.0, mode="constant")
    return h / max(h.sum(), 1e-12)


def colour_is(rgb: np.ndarray, h_a: np.ndarray, h_b: np.ndarray, ratio: float = COLOUR_RATIO) -> np.ndarray:
    """True where a colour is ``ratio`` x more frequent in histogram ``h_a`` than in ``h_b``."""
    lab = _lab(rgb)
    idx = [np.clip(np.searchsorted(e, lab[:, i], side="right") - 1, 0, len(e) - 2) for i, e in enumerate(_LAB_EDGES)]
    pa, pb = h_a[idx[0], idx[1], idx[2]], h_b[idx[0], idx[1], idx[2]]
    return (pa > ratio * pb) & (pa > 1e-5)


def face_components_subset(Fs: np.ndarray, n_vertices: int) -> np.ndarray:
    """Connected components (shared vertices) of a face set; one label per face."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    m = len(Fs)
    if not m:
        return np.zeros(0, np.int64)
    A = coo_matrix((np.ones(3 * m), (np.repeat(np.arange(m), 3), np.asarray(Fs, np.int64).ravel())),
                   shape=(m, n_vertices)).tocsr()
    return connected_components(A @ A.T, directed=False)[1]


def hardware_over_lens(FW: np.ndarray, n_vertices: int, face_rgb: np.ndarray, in_lens: np.ndarray,
                       lens_ref: np.ndarray, hw_ref: np.ndarray, seed: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    """HARDWARE OVER THE LENS, from the generator's own colours. On a rimless lens the S2 polygon runs around the drill
    mounts, their screws and the bars to the hinge (the photo does not separate them from the lens), and the generator
    is ONE fused mesh whose drill mounts pass THROUGH its lens plate: a depth rule guts them (their walls lie inside the
    lens slab) and cannot tell a bar at the lens depth from the lens. The generator's basecolor can: a colour model of
    its lens (``lens_ref`` faces: in the lens, in the lens slab, far from hardware) against its hardware (``hw_ref``: on
    the S2 hardware, off the lens), used only when >= ``COLOUR_SEPARATION`` of each reference falls on its own side (else
    no colour rule: all False, the depth rule alone). Returns (hardware: in-lens faces of a hardware-coloured connected
    component that holds a ``seed`` face (the S2 hardware) - taken WHOLE; lens_coloured: in-lens faces whose colour is
    the lens's; info). Both decisions need a decisive colour (``COLOUR_RATIO`` x more frequent on its side): a face
    whose colour is a blend of the two (the lens rim next to a pad, texture bleeding) is neither - "not lens-coloured"
    kept such wisps on miu's pads, which enclosed a 3 px seam gap and roughened the off-axis silhouette."""
    n = len(FW)
    none = np.zeros(n, bool)
    info = {"lens_ref_faces": int(lens_ref.sum()), "hw_ref_faces": int(hw_ref.sum()), "active": False}
    if lens_ref.sum() < COLOUR_MIN_FACES or hw_ref.sum() < COLOUR_MIN_FACES or not in_lens.any():
        return none, none, info
    lr, hr = np.nonzero(lens_ref)[0], np.nonzero(hw_ref)[0]
    lr, hr = lr[::max(1, len(lr) // 200_000)], hr[::max(1, len(hr) // 200_000)]
    h_l, h_h = lab_hist(face_rgb[lr]), lab_hist(face_rgb[hr])
    lens_self = float(colour_is(face_rgb[lr], h_l, h_h).mean())
    hw_self = float((~colour_is(face_rgb[hr], h_l, h_h)).mean())      # the decision is "not lens-coloured"
    info.update({"lens_ref_self": round(lens_self, 4), "hw_ref_self": round(hw_self, 4)})
    if min(lens_self, hw_self) < COLOUR_SEPARATION:
        return none, none, info
    sub = np.nonzero(in_lens | seed)[0]
    lens_col = np.zeros(n, bool)
    lens_col[sub] = colour_is(face_rgb[sub], h_l, h_h)
    hw_col = np.zeros(n, bool)                  # hardware-coloured: as decisive as lens-coloured (a blend is neither)
    hw_col[sub] = colour_is(face_rgb[sub], h_h, h_l)
    idx = np.nonzero((in_lens & hw_col) | (seed & ~lens_col))[0]
    hardware = np.zeros(n, bool)
    if len(idx):
        lab = face_components_subset(FW[idx], n_vertices)
        hit = np.unique(lab[seed[idx]])
        hardware[idx] = np.isin(lab, hit)
    hardware &= in_lens
    info.update({"active": True, "hardware_faces": int(hardware.sum()),
                 "lens_coloured_faces": int((lens_col & in_lens).sum())})
    return hardware, lens_col & in_lens, info


# ============================================================================ extraction
def extract(gen_V: np.ndarray, gen_F: np.ndarray, gen_UV: np.ndarray | None, Wpos: np.ndarray, winv: np.ndarray,
            df: depth.DepthField, src: depth.SourceView, frame: NormFrame, fg_sym: np.ndarray,
            lens_polys: list[np.ndarray], s4arr: dict, split_z: float, ppm_src: float,
            hardware: np.ndarray | None = None, hulls: list | None = None, join_points: np.ndarray | None = None,
            target_faces: int = TARGET_FACES_PER_SIDE, log=print, anchors: dict | None = None,
            texture: np.ndarray | None = None, rimless: bool = False) -> dict:
    """Donor parts per side ({'R': part | None, 'L': ...}, 'info'); a part = {V, F (per-vertex UV layout, vertices
    split at UV seams), UV (glTF, v down) or None, components, faces, closed}. See the module docstring. ``texture``
    (the generator basecolor) and ``rimless`` (the S2 rim class) enable the hardware-over-the-lens rule."""
    from . import temples as T
    FW_all = winv[np.asarray(gen_F, np.int64)]
    base = df.base
    info: dict = {"overlap_mm": OVERLAP_MM, "split_z_mm": float(split_z)}
    # a projection through the source camera that crosses its near plane falls back (no lens-plate removal, nothing
    # kept by the footprint, no grazing test): recorded here, flagged ``donor_projection_failed`` by S5 (a REVIEW rule)
    failures: list[str] = []
    info["projection_failures"] = failures
    # candidate faces: touching the front side of the split plane
    zmax = Wpos[:, 2][FW_all].max(axis=1)
    cand = np.nonzero(zmax > split_z)[0]
    FW = FW_all[cand]
    # ---- the generator's lens plate (face level): in the lens polygons (dilated) and in the lens slab ...
    C = Wpos[FW].mean(axis=1)
    poly_m = depth.poly_mask(lens_polys, src.shape) if lens_polys else np.zeros(src.shape, bool)
    lens_mask = poly_m.copy()
    if lens_mask.any():
        lens_mask = ndimage.binary_dilation(lens_mask, iterations=max(1, int(round(LENS_EXCLUDE_DILATE_MM * ppm_src))))
    try:
        uvc = src.project(C, frame)
    except ValueError:
        failures.append("lens_plate")
        uvc = np.full((len(C), 2), -1e6)
    H, W = src.shape
    ui, vi = np.round(uvc[:, 0]).astype(np.int64), np.round(uvc[:, 1]).astype(np.int64)
    inb = (ui >= 0) & (ui < W) & (vi >= 0) & (vi < H)
    in_lens = np.zeros(len(C), bool)
    in_lens[inb] = lens_mask[vi[inb], ui[inb]]
    slab = lens_slab_mm(s4arr)
    in_slab = np.zeros(len(C), bool)
    if in_lens.any() and df.lenses:
        sc, yc, dc = base.to_param(C[in_lens])
        near = np.full(int(in_lens.sum()), False)
        for lm in df.lenses:
            dl = lm(sc, yc)
            near |= (dc <= dl + LENS_SLAB_MARGIN_MM) & (dc >= dl - slab)
        in_slab[np.nonzero(in_lens)[0][near]] = True
    # ... except the hardware over the lens, whole; and (with the colour rule) every lens-coloured face in the lens
    hw_src = np.zeros(src.shape, bool) if hardware is None else np.asarray(hardware, bool)
    over = np.zeros(len(C), bool)
    lens_col = np.zeros(len(C), bool)
    # only on a RIMLESS front: there the hardware is the frame and every lens edge not held by it is bare; a shield's
    # nose piece or brow is not a drill mount (on INVU the rule took a dark generator sheet behind the lens as hardware)
    if rimless and hw_src.any() and texture is not None and gen_UV is not None and in_lens.any():
        body = ndimage.binary_dilation(hw_src, iterations=max(1, int(round(HW_DILATE_MM * ppm_src))))
        away = (ndimage.distance_transform_edt(~hw_src) > LENS_REF_AWAY_MM * ppm_src) & poly_m
        on_body, far = np.zeros(len(C), bool), np.zeros(len(C), bool)
        on_body[inb] = body[vi[inb], ui[inb]]
        far[inb] = away[vi[inb], ui[inb]]
        from . import generator as _gen
        rgb = _gen.sample_texture(texture, np.asarray(gen_UV, np.float64)[np.asarray(gen_F, np.int64)[cand]].mean(axis=1))
        over, lens_col, info["hardware_over_lens"] = hardware_over_lens(
            FW, len(Wpos), rgb, in_lens, in_slab & far, on_body & ~in_lens, on_body)
    lens_face = (in_slab & ~over) | lens_col
    over_v = np.zeros(len(Wpos), bool)                          # kept whatever their depth (``keep_or_hardware``)
    over_v[FW[over & ~lens_face].ravel()] = True
    FW = FW[~lens_face]
    cand = cand[~lens_face]
    info["lens_plate_faces_removed"] = int(lens_face.sum())
    info["lens_slab_mm"] = round(slab, 3)
    # ---- scalar fields in mm (negative = keep), evaluated exactly at any point
    h = float(s4arr["param_s"][1] - s4arr["param_s"][0])
    zone, zinfo = donor_zone(s4arr, h)
    join_mask = np.zeros(zone.shape, bool)
    if join_points is not None and len(join_points):
        # where the S5 arms join: the temple root between the plate and the split is always donor
        sj, yj, _ = base.to_param(np.asarray(join_points, float))
        js = np.round((sj - s4arr["param_s"][0]) / (s4arr["param_s"][1] - s4arr["param_s"][0])).astype(int)
        jy = np.round((yj - s4arr["param_y"][0]) / (s4arr["param_y"][1] - s4arr["param_y"][0])).astype(int)
        ok = (js >= 0) & (js < zone.shape[1]) & (jy >= 0) & (jy < zone.shape[0])
        jm = np.zeros(zone.shape, bool)
        jm[jy[ok], js[ok]] = True
        if jm.any():
            jm = ndimage.binary_dilation(jm, iterations=max(1, int(round(JOIN_ZONE_MM / h))))
            zone |= jm
            join_mask = jm
            zinfo["join_cells"] = int(jm.sum())
    info["zone"] = zinfo
    # beyond the front piece in (s, y) (neither plate nor lens cells, dilated): nothing to be behind; any generator
    # geometry there in front of the split (a wrapped temple root) is donor
    cls4 = np.asarray(s4arr["param_class"])
    beyond = ~ndimage.binary_dilation(cls4 >= 1, iterations=max(1, int(round(BEYOND_DILATE_MM / h))))
    # only where S6 drops plate pixels for the same reason: frame cells (not a lens's own edge)
    graze = grazing_cells(s4arr, df, src, frame, failures) & ndimage.binary_dilation(
        cls4 == 1, iterations=max(1, int(round(PLATE_CELL_DILATE_MM / h))))
    if graze.any():                     # the plate stops where the source photo sees it edge-on (S6 does the same)
        graze = ndimage.binary_dilation(graze, iterations=max(1, int(round(GRAZE_OVERLAP_MM / h))))
        beyond |= graze
        zone |= graze
    info["grazing_cells"] = int(graze.sum())
    # lens area away from the plate (S4 lens cells, not within PLATE_CELL_DILATE_MM of a frame cell): there is no
    # plate there to be behind, so rule 1 is replaced by "behind the generator's lens plate" (``behind_lens``), and the
    # depth-reach filter's reason (thin slivers over the photo-textured plate back) does not apply either: a generator
    # part there is structure seen through the lens - a centre stem between brow and nose piece, a pad arm - kept like
    # hardware (``_finish``)
    lens_cells = (cls4 >= 2) & ~ndimage.binary_dilation(cls4 == 1, iterations=max(1, int(round(PLATE_CELL_DILATE_MM / h))))
    sdf_lens_area = signed_distance(lens_cells, h)
    info["lens_area_cells"] = int(lens_cells.sum())
    sdf_zone = signed_distance(zone, h)
    sdf_beyond = signed_distance(beyond, h)
    # which lens a lens cell belongs to (nearest S4 lens cell), for the lens model it must be behind
    lens_idx = np.where(cls4 >= 2, cls4 - 1, 0).astype(np.int64)
    if (cls4 >= 2).any():
        _, (iy, ix) = ndimage.distance_transform_edt(cls4 < 2, return_indices=True)
        lens_idx = lens_idx[iy, ix]

    def behind_lens(P):
        """> 0: behind the generator's lens plate (its S4 lens model minus the plate slab) - structure seen THROUGH
        the lens from the front (a stem, a pad arm); what lies in front of a lens is frame in the photo, not lens."""
        s, y, d = base.to_param(P)
        if not df.lenses:
            return np.full(len(P), -1e6)
        fs = (np.asarray(s) - s4arr["param_s"][0]) / (s4arr["param_s"][1] - s4arr["param_s"][0])
        fy = (np.asarray(y) - s4arr["param_y"][0]) / (s4arr["param_y"][1] - s4arr["param_y"][0])
        ii = lens_idx[np.clip(np.round(fy).astype(int), 0, lens_idx.shape[0] - 1),
                      np.clip(np.round(fs).astype(int), 0, lens_idx.shape[1] - 1)]
        dl = np.full(len(P), np.nan)
        for k, lm in enumerate(df.lenses, start=1):
            sel = ii == k
            if sel.any():
                dl[sel] = lm(np.asarray(s)[sel], np.asarray(y)[sel])
        return np.where(np.isfinite(dl), (dl - slab) - d, -1e6)
    fp = np.asarray(fg_sym, bool)
    if FOOTPRINT_DILATE_PX > 0:
        fp = ndimage.binary_dilation(fp, iterations=max(1, int(math.ceil(FOOTPRINT_DILATE_PX))))
    sdf_fp = signed_distance(fp) / ppm_src
    hw = np.zeros(src.shape, bool) if hardware is None else np.asarray(hardware, bool)
    info["hardware_px"] = int(hw.sum())
    if hw.any():
        # the hardware itself (dilated for misregistration) plus what lies over the neighbouring lens area (drill
        # mounts and screws on a rimless lens: the S2 lens polygon covers them in the photo)
        reach = ndimage.binary_dilation(hw, iterations=max(1, int(round(HW_REACH_MM * ppm_src))))
        hw = ndimage.binary_dilation(hw, iterations=max(1, int(round(HW_DILATE_MM * ppm_src))))
        if lens_polys:
            hw |= reach & depth.poly_mask(lens_polys, src.shape)
    sdf_hw = signed_distance(hw) / ppm_src

    def behind_plate(P):
        s, y, d = base.to_param(P)
        return df.plate_back_d(s, y) - d                        # > 0: behind the plate back

    def sample_src(sdf, P):
        try:
            uv = src.project(P, frame)
        except ValueError:
            if "footprint" not in failures:
                failures.append("footprint")
            return np.full(len(P), 1e6)
        return ndimage.map_coordinates(sdf, [uv[:, 1], uv[:, 0]], order=1, mode="constant", cval=1e6)

    def keep_field(P):
        """min(region A = behind the plate in the zone, region B = hardware in the front slab), inside the footprint."""
        s, y, _ = base.to_param(P)
        behind_ = np.minimum(-(behind_plate(P) + OVERLAP_MM),
                             sample_grid(sdf_beyond, s4arr["param_s"], s4arr["param_y"], s, y, -1e6))
        # the lens area away from the plate: no plate to be behind; structure behind the lens plate is kept
        lens_area_ = np.maximum(sample_grid(sdf_lens_area, s4arr["param_s"], s4arr["param_y"], s, y, 1e6),
                                -behind_lens(P))
        behind_ = np.minimum(behind_, lens_area_)
        a_ = np.maximum(behind_, sample_grid(sdf_zone, s4arr["param_s"], s4arr["param_y"], s, y, -1e6))
        b_ = sample_src(sdf_hw, P)
        return np.maximum(np.minimum(a_, b_), sample_src(sdf_fp, P))

    V1, F1, used = compact(Wpos, FW)
    forced = over_v[used]

    def keep_or_hardware(P):
        """``keep_field``, and the hardware over the lens (``hardware_over_lens``) whatever its depth."""
        k = keep_field(P)
        return np.where(forced, np.minimum(k, -1.0), k)
    hulls, info["hull_views_skipped_low_weight"] = trusted_hulls(hulls)
    info["hull_views"] = [h_.view for h_ in hulls]
    # the keep clip first: the hardware-over-lens flag is known on the generator's own vertices only
    steps = [("keep", keep_or_hardware), ("split", lambda P: split_z - P[:, 2])]
    if hulls:
        steps.append(("hull", lambda P: hull_outside_mm(hulls, P)))
    for name, fn in steps:
        if not len(F1):
            break
        V1, F1, _ = clip_mesh(V1, F1, fn(V1))
        V1, F1, _ = compact(V1, F1)
    behind = behind_plate(V1) if len(V1) else np.zeros(0)
    in_hw = np.zeros(len(V1), bool)
    in_lens_area = np.zeros(len(V1), bool)
    on_plate = np.zeros(len(V1), bool)
    if len(V1):
        s1_, y1_, _ = base.to_param(V1)
        # hardware, or the temple root where the arm joins (in a region without plate): the depth-reach filter does
        # not apply; any other component must reach MIN_BEHIND_MM behind the plate back
        at_join = sample_grid(signed_distance(join_mask, h), s4arr["param_s"], s4arr["param_y"], s1_, y1_, 1e6) < 0
        beyond_v = sample_grid(sdf_beyond, s4arr["param_s"], s4arr["param_y"], s1_, y1_, -1e6) < 0
        in_hw = (sample_src(sdf_hw, V1) < 0) | (beyond_v & at_join)
        in_lens_area = (sample_grid(sdf_lens_area, s4arr["param_s"], s4arr["param_y"], s1_, y1_, 1e6) < 0) &             (behind_lens(V1) > 0)
        # on the plate: over a plate cell and at (or into) the plate back - where S6 builds the plate it joins
        plate_cells = ndimage.binary_dilation(cls4 == 1, iterations=max(1, int(round(PLATE_CELL_DILATE_MM / h))))
        on_plate = (sample_grid(signed_distance(plate_cells, h), s4arr["param_s"], s4arr["param_y"], s1_, y1_, 1e6) < 0)             & (behind <= ANCHOR_GAP_MM)
    info["clipped_faces"] = int(len(F1))
    failures += [f"hull:{h_.view}" for h_ in hulls if getattr(h_, "failed", False)]
    out = {"info": info}
    for side, sg in (("R", 1.0), ("L", -1.0)):
        if not len(F1):
            out[side] = None
            continue
        Vs, Fs, _ = clip_mesh(V1, F1, -sg * V1[:, 0])
        bs = np.concatenate([behind, np.zeros(len(Vs) - len(V1))])
        hs = np.concatenate([in_hw, np.zeros(len(Vs) - len(V1), bool)])
        ls = np.concatenate([in_lens_area, np.zeros(len(Vs) - len(V1), bool)])
        ps = np.concatenate([on_plate, np.zeros(len(Vs) - len(V1), bool)])
        Vs, Fs, ki = compact(Vs, Fs)
        out[side] = _finish(Vs, Fs, bs[ki], hs[ki], gen_V, gen_F, gen_UV, cand, target_faces, T, side, info, ls[ki],
                            ps[ki], None if anchors is None else anchors.get(side))
    return out


def _finish(V, F, behind, in_hw, gen_V, gen_F, gen_UV, cand, target, T, side, info, in_lens=None, on_plate=None,
            anchor_pts=None) -> dict | None:
    """Filter components (area >= MIN_COMPONENT_AREA_MM2 and: reaches MIN_BEHIND_MM behind the plate back, or is
    hardware, or lies mostly (>= LENS_AREA_SHARE of its area) in the lens area away from the plate), then drop the
    FLOATING ones: a non-hardware component must touch the plate (a vertex over a plate cell at the plate back),
    come within ``ANCHOR_GAP_MM`` of the side's S5 arm (``anchor_pts``) or of an anchored component; a donor
    piece that touches nothing is a detached sliver in the delivered model (oakley's 8-9 mm2 pieces behind the
    temple roots). Hardware is anchored by itself (the S6 lens holds rimless hardware), except a hardware FRAGMENT
    (< ``HW_FRAGMENT_MM2``): a piece that small is a clipped-off corner of a part, and it must touch something like
    any other component (miu's 10.7 mm2 piece 0.535 mm from everything, flagged by the S10 integrity check). Then
    decimate, cap, transfer UVs, orient, split at UV seams. The anchoring is tested again on the DELIVERED geometry
    (decimated, capped, next to the plate as built) by S6 (``assemble.donor_anchoring``, the S10 integrity rule).
    ``anchor_pts`` is the side's S5 arm: its vertices, or (V, F)."""
    rep = {"clipped_faces": int(len(F))}
    if not len(F):
        info[side] = rep
        return None
    lab = T.face_components(F, len(V))
    A = face_areas(V, F)
    ncomp = int(lab.max()) + 1
    area = np.bincount(lab, weights=A, minlength=ncomp)
    vb = behind[F].max(axis=1)
    reach = np.full(ncomp, -np.inf)
    np.maximum.at(reach, lab, vb)
    hw_c = np.bincount(lab, weights=in_hw[F].any(axis=1).astype(float), minlength=ncomp) > 0
    lens_c = np.zeros(ncomp, bool)
    if in_lens is not None:
        # mostly in the lens area AND anchored: some of it outside the lens area (it meets the brow, the bridge or a
        # pad's hardware); an isolated blob floating behind the lens is generator noise, not a stem or a pad arm
        la = np.bincount(lab, weights=A * in_lens[F].all(axis=1), minlength=ncomp)
        anchored = np.bincount(lab, weights=(~in_lens[F]).any(axis=1).astype(float), minlength=ncomp) > 0
        lens_c = (la >= LENS_AREA_SHARE * np.maximum(area, 1e-12)) & anchored
    ok = (area >= MIN_COMPONENT_AREA_MM2) & ((reach >= MIN_BEHIND_MM) | hw_c | lens_c)
    floating_dropped = 0
    if on_plate is not None and ok.any():
        from scipy.spatial import cKDTree
        anch = (hw_c & (area >= HW_FRAGMENT_MM2)) | (np.bincount(lab, weights=on_plate[F].any(axis=1).astype(float),
                                                                  minlength=ncomp) > 0)
        vlab = np.full(len(V), -1)
        vlab[F.ravel()] = np.repeat(lab, 3)
        trees = {c: cKDTree(V[vlab == c]) for c in np.nonzero(ok)[0]}
        arm_pts = anchor_pts[0] if isinstance(anchor_pts, tuple) else anchor_pts
        if arm_pts is not None and len(arm_pts):
            at = cKDTree(np.asarray(arm_pts, float))
            for c, tr in trees.items():
                if not anch[c] and np.isfinite(at.query(tr.data, distance_upper_bound=ANCHOR_GAP_MM)[0]).any():
                    anch[c] = True
        changed = True
        while changed:
            changed = False
            for c, tr in trees.items():
                if anch[c]:
                    continue
                for c2, tr2 in trees.items():
                    if c2 != c and anch[c2] and np.isfinite(tr2.query(tr.data, distance_upper_bound=ANCHOR_GAP_MM)[0]).any():
                        anch[c] = changed = True
                        break
        floating = ok & ~anch
        floating_dropped = int(floating.sum())
        rep["floating_dropped_area_mm2"] = round(float(area[floating].sum()), 2)
        ok &= anch
    rep.update({"components_in": ncomp, "components_kept": int(ok.sum()), "floating_dropped": floating_dropped,
                "kept_in_lens_area": int((ok & lens_c & ~hw_c & (reach < MIN_BEHIND_MM)).sum()),
                "area_kept_in_lens_area_mm2": round(float(area[ok & lens_c & ~hw_c & (reach < MIN_BEHIND_MM)].sum()), 2),
                "area_kept_mm2": round(float(area[ok].sum()), 2), "area_dropped_mm2": round(float(area[~ok].sum()), 2)})
    keep = ok[lab]
    if not keep.any():
        info[side] = rep
        return None
    V, F, _ = compact(V, F[keep])
    tgt = int(max(500, min(target, len(F))))
    Vd, Fd, dinfo = T.decimate(V, F, tgt)
    rep["decimation"] = {k: dinfo[k] for k in ("faces_in", "faces_out", "boundary", "nonmanifold")}
    CUV = None
    if gen_UV is not None:
        # UV source: the original (unwelded) generator faces of the candidate set near this donor
        src_faces = cand
        Fo = np.asarray(gen_F, np.int64)[src_faces]
        lo, hi = Vd.min(0) - 1.0, Vd.max(0) + 1.0
        cen = gen_V[Fo].mean(axis=1)
        near = np.all((cen >= lo) & (cen <= hi), axis=1)
        Fo = Fo[near]
        uo = np.unique(Fo)
        rm = np.full(len(gen_V), -1, np.int64)
        rm[uo] = np.arange(len(uo))
        CUV, uinfo = T.transfer_uv(gen_V[uo].astype(np.float64), rm[Fo], gen_UV[uo].astype(np.float64), Vd, Fd)
        rep["uv"] = {k: uinfo[k] for k in ("seam_faces", "seam_corner_fallbacks", "vertex_to_source_mm_p95")}
    loops = T.boundary_loops(Fd)
    Vc, Fc, CUVc, kinds, _ = T.close_temple(Vd, Fd, [], loops, 0.0, CUV)
    Fc, CUVc, flipped = T.orient_outward(Vc, Fc, CUVc)
    # drop components that are still not closed (non-manifold generator patches)
    lab2 = T.face_components(Fc, len(Vc))
    good = np.ones(int(lab2.max()) + 1, bool)
    for c in range(len(good)):
        topo = T.edge_topology(Fc[lab2 == c])
        good[c] = topo["boundary"] == 0 and topo["nonmanifold"] == 0 and topo["misoriented"] == 0
    rep["components_not_closed_dropped"] = int((~good).sum())
    sel = good[lab2]
    Fc = Fc[sel]
    CUVc = CUVc[sel] if CUVc is not None else None
    if not len(Fc):
        info[side] = rep
        return None
    Vc2, Fc2, ki = compact(Vc, Fc)
    if CUVc is not None:
        Vo, Fo2, UVo = T.split_by_uv(Vc2, Fc2, CUVc)
    else:
        Vo, Fo2, UVo = Vc2, Fc2.astype(np.int32), None
    topo = T.edge_topology(Fc2)
    rep.update({"faces": int(len(Fo2)), "caps": int(len(loops)), "closed": bool(topo["boundary"] == 0 and topo["nonmanifold"] == 0
                                                                                  and topo["misoriented"] == 0),
                "components": int(good.sum()),
                "volume_mm3": round(float(T.signed_volume(Vc2, Fc2)), 2),
                "bbox_mm": [np.round(Vc2.min(0), 2).tolist(), np.round(Vc2.max(0), 2).tolist()]})
    info[side] = rep
    return {"V": Vo, "F": Fo2, "UV": UVo, "Vw": Vc2, "Fw": Fc2, **rep}
