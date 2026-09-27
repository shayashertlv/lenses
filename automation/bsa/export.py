"""S9 export: MODEL-frame parts (mm) -> one AR-ready GLB (DESIGN.md S9).

Output convention (the AR runtime's external-asset contract, ``ar/src/eyewear/external.ts``):
metres, +Y up, +Z toward the viewer (lenses face +Z), origin at the bridge underside on the
symmetry axis, identity node transforms, one node per part named ``frame``, ``temple_R``,
``temple_L`` and ``lens_R`` + ``lens_L`` (pair) or ``lens_C`` (single/shield). Frame and temples
are opaque; every lens material has ``KHR_materials_transmission`` > 0 (the runtime's lens
detection rule, ``ar/src/eyewear/optical-material.ts``) and ``KHR_materials_ior``, and - when S8 supplies one - the
canonical ``LENSES_lens_appearance`` descriptor, which the runtime renders with its own optics (unblurred background,
v-profile density, angular reflectance; ``canonical_sheet``: TEXCOORD_0.y = lens-local height, bottom 0 -> top 1, and
the mesh extras ``partRole``/``lensSurfaceProfile = front_sheet_v1``). The legacy transmission lens is only a fallback.

Lens export rule (M0 finding, see ``LENS_PROFILE``): a lens is written as its FRONT SHEET only - the
+Z-facing cap of the constructed lens solid, single-sided, normals toward +Z, no walls, no back
cap, no ``KHR_materials_volume`` - with shading normals taken from a smooth height-field fitted to
the sheet (or supplied by the caller), never from face averaging. This is the runtime's own
canonical optical profile (``front_sheet_v1`` in ``ar/src/render/lens-material.ts``: FrontSide,
thickness 0, forceSinglePass). On the probe GLBs a closed 2 mm lens slab with face-averaged
normals rendered bright radial streaks in the actual TryOnRenderer; the cause and the controlled
comparison are in ``data/bsa/m0_ar``. ``lens_profile="solid"`` keeps the closed lens (fitted
normals on both caps, flat walls) for diagnostics.

``parts``: name -> dict with
  ``V`` (N,3) float mm MODEL frame; ``F`` (M,3) int; optional ``UV`` (N,2) or (M,3,2) glTF UV
  (u right, v DOWN the image, 0..1); optional ``COLOR`` (N,3|4) or (M,3,3|4) - float = linear 0..1,
  uint8 = sRGB (converted to linear); optional ``N`` (N,3) or (M,3,3) shading normals (MODEL frame);
  ``material``: a material name, or a list of names with ``face_material`` (M,) int indices into it;
  optional ``crease_deg`` (frame/temples); optional ``front_mask`` (M,) bool for lenses; optional
  ``edge_ring`` {"width_mm", "material"} for lenses: the front sheet is cut in place (``split_edge_ring``) and
  the band within width_mm of its outline gets that (transmissive) material as a second primitive.
``materials``: name -> dict with any of
  ``gltf`` (a glTF material dict used as the base; a textureInfo may carry ``{"image": ...}`` in
  place of ``index``), ``base_color`` [r,g,b(,a)] linear, ``base_color_texture``,
  ``metallic``, ``roughness``, ``metallic_roughness_texture``, ``normal_texture``,
  ``transmission``, ``ior``, ``double_sided``. A texture is an (H,W,3|4) uint8 sRGB array, a
  PIL image or an image file path; it is embedded as JPEG (PNG only when it has real alpha),
  longest side <= 2048.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
from pathlib import Path
import struct

import numpy as np
from PIL import Image
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

FRAME_NODE = "frame"
TEMPLE_NODES = ("temple_R", "temple_L")
PAIR_LENS_NODES = ("lens_R", "lens_L")
SINGLE_LENS_NODES = ("lens_C",)
NODE_ORDER = (FRAME_NODE, *TEMPLE_NODES, *PAIR_LENS_NODES, *SINGLE_LENS_NODES)
PART_ROLE = {"frame": "frame", "temple_R": "temple", "temple_L": "temple",
             "lens_R": "lens", "lens_L": "lens", "lens_C": "lens"}

DEFAULT_CREASE_DEG = 40.0
MAX_TEXTURE_PX = 2048
JPEG_QUALITY = 90
LENS_PROFILE = "front_sheet"          # "front_sheet" (default, the M0 rule) | "solid" (diagnostic)
LENS_PATCH_DIHEDRAL_DEG = 60.0        # edges sharper than this bound the lens caps
LENS_FIT_MAX_DEGREE = 4
LENS_FIT_TOL_MM = 0.05                # accept the lowest height-field degree with RMS below this
LENS_FIT_LUMPY_MM = 0.25              # flag a constructed lens whose vertices stray this far from the fit
GENERATOR = "bsa.export v1"
LENS_APPEARANCE_EXTENSION = "LENSES_lens_appearance"   # the runtime's canonical optics (ar/src/eyewear/lens-appearance.ts)
CANONICAL_SURFACE_PROFILE = "front_sheet_v1"           # ar/src/render/lens-material.ts CANONICAL_LENS_SURFACE_PROFILE
CANONICAL_MIN_NZ = 0.02                                # a canonical front-sheet vertex normal must point toward +Z

_GL_FLOAT, _GL_UINT = 5126, 5125
_ARRAY_BUFFER, _ELEMENT_ARRAY_BUFFER = 34962, 34963


# --------------------------------------------------------------------------- geometry helpers
def _face_normals(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Unit face normals and areas (degenerate faces get a zero normal)."""
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    a2 = np.linalg.norm(n, axis=1)
    out = np.zeros_like(n)
    ok = a2 > 1e-18
    out[ok] = n[ok] / a2[ok, None]
    return out, a2 / 2.0


def _corner_angles(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    ang = np.zeros(F.shape, float)
    for k in range(3):
        a = V[F[:, (k + 1) % 3]] - V[F[:, k]]
        b = V[F[:, (k + 2) % 3]] - V[F[:, k]]
        na, nb = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
        c = np.einsum("ij,ij->i", a, b) / np.maximum(na * nb, 1e-30)
        ang[:, k] = np.arccos(np.clip(c, -1.0, 1.0))
    return ang


def weld(V: np.ndarray, F: np.ndarray, tol_mm: float = 1e-4) -> np.ndarray:
    """Face indices into position-welded vertices (for topology only; output keeps V/F)."""
    key = np.round(np.asarray(V, float) / tol_mm).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.ravel()[np.asarray(F, np.int64)]


def crease_normals(V: np.ndarray, F: np.ndarray, crease_deg: float = DEFAULT_CREASE_DEG,
                   chunk: int = 400_000) -> np.ndarray:
    """Per-corner smooth normals (M,3,3): the angle-weighted mean of the faces around the corner's
    vertex whose normal is within ``crease_deg`` of the corner's own face (auto-smooth)."""
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    fn, _ = _face_normals(V, F)
    w = _corner_angles(V, F).ravel()
    cv = weld(V, F).ravel()                                  # incidence on welded positions
    cf = np.repeat(np.arange(len(F)), 3)
    order = np.argsort(cv, kind="stable")
    sv = cv[order]
    starts = np.flatnonzero(np.r_[True, sv[1:] != sv[:-1]])
    counts = np.diff(np.r_[starts, len(sv)])
    gid = np.repeat(np.arange(len(starts)), counts)          # group of each sorted corner
    cos_c = math.cos(math.radians(crease_deg))
    acc = np.zeros((len(cv), 3))
    s_of = counts[gid]
    # process sorted corners in chunks so the (corner, neighbour-corner) pair list stays small
    pos = 0
    while pos < len(sv):
        end = pos
        total = 0
        while end < len(sv) and (total + s_of[end] <= chunk or end == pos):
            total += s_of[end]
            end += 1
        idx = np.arange(pos, end)
        rep = np.repeat(idx, s_of[idx])
        local = np.arange(len(rep)) - np.repeat(np.cumsum(s_of[idx]) - s_of[idx], s_of[idx])
        other = starts[gid[rep]] + local
        c1, c2 = order[rep], order[other]
        f1, f2 = cf[c1], cf[c2]
        keep = np.einsum("ij,ij->i", fn[f1], fn[f2]) >= cos_c
        c1, c2, f2 = c1[keep], c2[keep], f2[keep]
        contrib = fn[f2] * w[c2, None]
        for d in range(3):
            acc[:, d] += np.bincount(c1, weights=contrib[:, d], minlength=len(cv))
        pos = end
    nrm = np.linalg.norm(acc, axis=1)
    bad = nrm < 1e-12
    acc[~bad] /= nrm[~bad, None]
    acc[bad] = fn[cf[bad]]
    return acc.reshape(len(F), 3, 3)


def _poly_terms(x: np.ndarray, y: np.ndarray, deg: int):
    """Monomials x^i y^j (i+j <= deg) and their x / y derivatives."""
    T, Tx, Ty = [], [], []
    for i in range(deg + 1):
        for j in range(deg + 1 - i):
            T.append(x ** i * y ** j)
            Tx.append(i * x ** max(i - 1, 0) * y ** j if i else np.zeros_like(x))
            Ty.append(j * x ** i * y ** max(j - 1, 0) if j else np.zeros_like(x))
    return np.stack(T, 1), np.stack(Tx, 1), np.stack(Ty, 1)


def fit_height_field(P: np.ndarray, max_degree: int = LENS_FIT_MAX_DEGREE, tol_mm: float = LENS_FIT_TOL_MM) -> dict:
    """Robust polynomial z(x, y) through sheet vertices (mm). Returns degree, rms, and a normal fn."""
    P = np.asarray(P, float)
    c = P[:, :2].mean(0)
    s = max(float(np.abs(P[:, :2] - c).max()), 1e-9)
    x, y, z = (P[:, 0] - c[0]) / s, (P[:, 1] - c[1]) / s, P[:, 2]
    best = None
    for deg in range(1, max_degree + 1):
        nterm = (deg + 1) * (deg + 2) // 2
        if len(P) < 3 * nterm:
            break
        T, _, _ = _poly_terms(x, y, deg)
        w = np.ones(len(P))
        for _ in range(3):                       # Huber IRLS, 0.1 mm knee
            coef, *_ = np.linalg.lstsq(T * w[:, None], z * w, rcond=None)
            r = T @ coef - z
            w = np.sqrt(np.minimum(1.0, 0.1 / np.maximum(np.abs(r), 1e-12)))
        rms = float(np.sqrt(np.mean(r ** 2)))
        p95 = float(np.percentile(np.abs(r), 95))
        cand = {"degree": deg, "coef": coef, "rms_mm": rms, "p95_mm": p95}
        if best is None or rms < best["rms_mm"] - 1e-9:
            best = cand
        if rms <= tol_mm:
            best = cand
            break
    if best is None:
        return {"degree": 0, "rms_mm": float("nan"), "p95_mm": float("nan"), "normal": None}
    deg, coef = best["degree"], best["coef"]

    def normal(Q: np.ndarray) -> np.ndarray:
        Q = np.asarray(Q, float)
        qx, qy = (Q[:, 0] - c[0]) / s, (Q[:, 1] - c[1]) / s
        _, Tx, Ty = _poly_terms(qx, qy, deg)
        n = np.stack([-(Tx @ coef) / s, -(Ty @ coef) / s, np.ones(len(Q))], 1)
        return n / np.linalg.norm(n, axis=1, keepdims=True)

    return {"degree": deg, "rms_mm": best["rms_mm"], "p95_mm": best["p95_mm"], "normal": normal}


def smooth_patches(V: np.ndarray, F: np.ndarray, dihedral_deg: float = LENS_PATCH_DIHEDRAL_DEG) -> np.ndarray:
    """Component label per face; faces joined across manifold edges flatter than ``dihedral_deg``."""
    fn, _ = _face_normals(np.asarray(V, float), np.asarray(F, np.int64))
    F = weld(V, F)
    e = np.sort(np.stack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]], 1).reshape(-1, 2), axis=1)
    ef = np.repeat(np.arange(len(F)), 3)
    key = e[:, 0] * (int(F.max()) + 1) + e[:, 1]
    order = np.argsort(key, kind="stable")
    ks = key[order]
    same = np.flatnonzero(ks[1:] == ks[:-1])
    a, b = ef[order[same]], ef[order[same + 1]]
    # only edges shared by exactly two faces
    cnt = np.bincount(np.searchsorted(np.unique(ks), ks))
    uniq_idx = np.searchsorted(np.unique(ks), ks[same])
    two = cnt[uniq_idx] == 2
    a, b = a[two], b[two]
    smooth = np.einsum("ij,ij->i", fn[a], fn[b]) >= math.cos(math.radians(dihedral_deg))
    a, b = a[smooth], b[smooth]
    g = coo_matrix((np.ones(len(a)), (a, b)), shape=(len(F), len(F)))
    _, labels = connected_components(g, directed=False)
    return labels


def lens_front_sheet(V: np.ndarray, F: np.ndarray, front_mask: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Face mask of a lens solid's front (+Z) cap: the smooth patch with the largest +Z-facing area,
    minus any backward-facing face. A lens that is already a sheet returns (almost) all faces."""
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    fn, area = _face_normals(V, F)
    if front_mask is not None:
        mask = np.asarray(front_mask, bool) & (fn[:, 2] > 0)
        return mask, {"method": "front_mask", "faces": int(mask.sum()), "of": int(len(F))}
    labels = smooth_patches(V, F)
    score = np.bincount(labels, weights=area * fn[:, 2])
    front = int(np.argmax(score))
    mask = (labels == front) & (fn[:, 2] > 0)
    return mask, {"method": "largest_front_facing_smooth_patch", "patches": int(labels.max() + 1),
                  "faces": int(mask.sum()), "of": int(len(F)),
                  "min_nz": float(fn[mask, 2].min()) if mask.any() else None}


# --------------------------------------------------------------------------- clear-lens edge ring
def _sheet_boundary(F: np.ndarray) -> np.ndarray:
    """Undirected edges used by exactly one face (the sheet's outline), (B, 2)."""
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    u, c = np.unique(e, axis=0, return_counts=True)
    return u[c == 1]


def _dist_to_segments(P: np.ndarray, A: np.ndarray, B: np.ndarray, chunk: int = 2048) -> np.ndarray:
    """Euclidean distance of each point to the nearest of the segments A[k]-B[k]."""
    out = np.empty(len(P))
    AB = B - A
    L2 = np.maximum((AB * AB).sum(1), 1e-30)
    for s in range(0, len(P), chunk):
        p = P[s:s + chunk, None, :]
        t = np.clip(((p - A[None]) * AB[None]).sum(-1) / L2[None], 0.0, 1.0)
        q = A[None] + t[..., None] * AB[None]
        out[s:s + chunk] = np.sqrt(((p - q) ** 2).sum(-1).min(1))
    return out


def split_edge_ring(V: np.ndarray, F: np.ndarray, width_mm: float, UV: np.ndarray | None = None) -> dict:
    """Cut a lens SHEET into the band within ``width_mm`` of its outline (the clear-lens frosted edge ring) and
    the interior, WITHOUT moving the surface: every new vertex lies on an existing triangle (barycentric lift),
    so the two parts tile the original sheet exactly (no gap, no overlap: the seam check and the silhouette are
    unchanged) and keep its outline vertices.

    The band is measured in the sheet's xy projection (a front sheet faces +Z: n_z > 0, so the projection is a
    valid domain; on a flat-ish clear lens xy distance ~ surface distance). Sheet = union of its triangles in
    xy, interior = sheet eroded by ``width_mm`` (round joins), ring = sheet - interior; every triangle crossing
    the inner curve is clipped by both and its pieces are triangulated (constrained Delaunay) and lifted onto
    the triangle's plane. Returns {V, F, UV, ring (per face bool), parent (per face index into F), info}."""
    import shapely
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    V = np.asarray(V, float)
    F = np.asarray(F, np.int64)
    UV = None if UV is None else np.asarray(UV, float)
    w = float(width_mm)
    xy = V[:, :2]
    tri_xy = xy[F]
    area_xy = 0.5 * ((tri_xy[:, 1, 0] - tri_xy[:, 0, 0]) * (tri_xy[:, 2, 1] - tri_xy[:, 0, 1])
                     - (tri_xy[:, 2, 0] - tri_xy[:, 0, 0]) * (tri_xy[:, 1, 1] - tri_xy[:, 0, 1]))
    good = np.abs(area_xy) > 1e-10
    sheet = unary_union([Polygon(t) for t in tri_xy[good]]).buffer(0)
    inner = sheet.buffer(-w, join_style="round", quad_segs=16)
    ring_region = sheet.difference(inner)
    outline = shapely.get_exterior_ring(sheet) if sheet.geom_type == "Polygon" else None
    boundary = sheet.boundary

    keyed: dict[tuple, int] = {}
    outV, outUV, outF, outRing, outP = [], [], [], [], []

    def vid(P3, uv):
        k = tuple(np.round(P3 * 1e6).astype(np.int64))
        if k not in keyed:
            keyed[k] = len(outV)
            outV.append(P3)
            outUV.append(uv)
        return keyed[k]

    def lift(k, pts):
        """Barycentric (in xy) lift of points onto triangle k."""
        a, b, c = tri_xy[k]
        T = np.array([[b[0] - a[0], c[0] - a[0]], [b[1] - a[1], c[1] - a[1]]])
        l = np.linalg.solve(T, (np.asarray(pts, float) - a).T).T
        bary = np.c_[1 - l.sum(1), l]
        P3 = bary @ V[F[k]]
        uv = None if UV is None else bary @ UV[F[k]]
        return P3, uv

    def emit(k, polys, is_ring):
        sgn = np.sign(area_xy[k])
        for poly in polys:
            if poly.area < 1e-10:
                continue
            for t in shapely.constrained_delaunay_triangles(poly).geoms:
                c = np.asarray(t.exterior.coords)[:3]
                ar = 0.5 * ((c[1, 0] - c[0, 0]) * (c[2, 1] - c[0, 1]) - (c[2, 0] - c[0, 0]) * (c[1, 1] - c[0, 1]))
                if abs(ar) < 1e-12:
                    continue
                if np.sign(ar) != sgn:
                    c = c[::-1]
                P3, uv = lift(k, c)
                ids = [vid(P3[i], None if uv is None else uv[i]) for i in range(3)]
                if len(set(ids)) == 3:
                    outF.append(ids)
                    outRing.append(is_ring)
                    outP.append(k)

    def parts_of(g):
        if g.is_empty:
            return []
        if g.geom_type == "Polygon":
            return [g]
        return [x for x in getattr(g, "geoms", []) if x.geom_type == "Polygon"]
    clipped = 0
    for k in range(len(F)):
        if not good[k]:
            # degenerate in xy (a near-vertical sliver): whole, classified by its centroid's distance to the outline
            cen = shapely.Point(tri_xy[k].mean(0))
            ids = [vid(V[F[k, i]], None if UV is None else UV[F[k, i]]) for i in range(3)]
            if len(set(ids)) == 3:
                outF.append(ids)
                outRing.append(bool(boundary.distance(cen) < w))
                outP.append(k)
            continue
        P = Polygon(tri_xy[k])
        pr = P.intersection(ring_region)
        if pr.area < 1e-9 * max(P.area, 1e-12):
            ids = [vid(V[F[k, i]], None if UV is None else UV[F[k, i]]) for i in range(3)]
            outF.append(ids)
            outRing.append(False)
            outP.append(k)
            continue
        pi = P.intersection(inner)
        if pi.area < 1e-9 * max(P.area, 1e-12):
            ids = [vid(V[F[k, i]], None if UV is None else UV[F[k, i]]) for i in range(3)]
            outF.append(ids)
            outRing.append(True)
            outP.append(k)
            continue
        clipped += 1
        emit(k, parts_of(pr), True)
        emit(k, parts_of(pi), False)
    V2 = np.asarray(outV, float)
    UV2 = None if UV is None else np.asarray(outUV, float)
    F2 = np.asarray(outF, np.int64)
    ring = np.asarray(outRing, bool)
    parent = np.asarray(outP, np.int64)
    _, area = _face_normals(V2, F2)
    _, area0 = _face_normals(V, F)
    info = {"width_mm": w, "domain": "sheet xy projection", "faces_in": int(len(F)), "faces_out": int(len(F2)),
            "faces_clipped": int(clipped), "ring_faces": int(ring.sum()),
            "ring_area_mm2": round(float(area[ring].sum()), 3), "interior_area_mm2": round(float(area[~ring].sum()), 3),
            "area_change_mm2": round(float(area.sum() - area0.sum()), 6),
            "outline_mm": round(float(boundary.length), 2),
            "xy_ring_area_mm2": round(float(ring_region.area), 3)}
    return {"V": V2, "F": F2, "UV": UV2, "ring": ring, "parent": parent, "info": info}


# --------------------------------------------------------------------------- origin
def bridge_underside_mm(parts: dict[str, dict], axis_x: float | None = None) -> tuple[np.ndarray, dict]:
    """Bridge underside on the symmetry axis: the lowest point where the FRONT parts (frame + lenses)
    cross the plane x = axis_x (default: the centre of their x extent). For a pair that is the
    bridge's underside; for a shield, the apex of the lens's nose notch. Depth = middle of the
    crossing's depth span within 0.5 mm of that lowest height."""
    front = [n for n in parts if PART_ROLE.get(n) in ("frame", "lens")]
    if not front:
        raise ValueError("No front parts to place the origin on")
    allv = np.vstack([np.asarray(parts[n]["V"], float) for n in front])
    if axis_x is None:
        axis_x = float((allv[:, 0].min() + allv[:, 0].max()) / 2)
    pts = []
    for n in front:
        V = np.asarray(parts[n]["V"], float)
        F = np.asarray(parts[n]["F"], np.int64)
        for a, b in ((0, 1), (1, 2), (2, 0)):
            pa, pb = V[F[:, a]], V[F[:, b]]
            da, db = pa[:, 0] - axis_x, pb[:, 0] - axis_x
            cross = (da * db <= 0) & (da != db)
            t = da[cross] / (da[cross] - db[cross])
            pts.append(pa[cross] + (pb[cross] - pa[cross]) * t[:, None])
        on = np.abs(V[:, 0] - axis_x) < 1e-9
        pts.append(V[on])
    P = np.vstack(pts) if pts else np.zeros((0, 3))
    if not len(P):
        raise ValueError("The front parts do not cross the symmetry plane; cannot place the bridge origin")
    y0 = float(P[:, 1].min())
    band = P[P[:, 1] <= y0 + 0.5]
    z0 = float((band[:, 2].min() + band[:, 2].max()) / 2)
    origin = np.array([axis_x, y0, z0])
    return origin, {"method": "front_parts_symmetry_plane_lowest_crossing", "axis_x_mm": axis_x,
                    "origin_mm": origin.tolist(), "crossing_points": int(len(P)), "band_points": int(len(band))}


# --------------------------------------------------------------------------- textures / materials
def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(c: np.ndarray) -> np.ndarray:
    c = np.clip(np.asarray(c, float), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def gradient_texture(top_linear_rgb, bottom_linear_rgb, profile=None, n: int = 64) -> np.ndarray:
    """A (n, 4, 3) uint8 sRGB vertical gradient (row 0 = top) for a lens baseColorTexture on uv v.
    ``profile``: optional n-vector of blend weights (0 = top colour, 1 = bottom colour)."""
    t = np.linspace(0.0, 1.0, n) if profile is None else np.clip(np.asarray(profile, float), 0, 1)
    top, bot = np.asarray(top_linear_rgb, float)[:3], np.asarray(bottom_linear_rgb, float)[:3]
    col = top[None, :] * (1 - t[:, None]) + bot[None, :] * t[:, None]
    srgb = np.round(_linear_to_srgb(col) * 255).astype(np.uint8)
    return np.repeat(srgb[:, None, :], 4, axis=1)


def _as_image(src) -> Image.Image:
    if isinstance(src, Image.Image):
        return src
    if isinstance(src, (str, Path)):
        with Image.open(src) as im:
            im.load()
            return im.copy()
    arr = np.asarray(src)
    if arr.dtype != np.uint8:
        arr = np.clip(np.round(arr * 255 if arr.max() <= 1.0 else arr), 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def encode_texture(src, max_px: int = MAX_TEXTURE_PX, quality: int = JPEG_QUALITY) -> tuple[bytes, str, dict]:
    """JPEG (or PNG when the image has real alpha), longest side <= max_px. Deterministic."""
    im = _as_image(src)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    orig = im.size
    if max(im.size) > max_px:
        k = max_px / max(im.size)
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    buf = io.BytesIO()
    if im.mode == "RGBA" and np.asarray(im)[..., 3].min() < 255:
        im.save(buf, "PNG", optimize=False)
        mime = "image/png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=quality, subsampling=0, optimize=False)
        mime = "image/jpeg"
    return buf.getvalue(), mime, {"source_size": list(orig), "size": [im.width, im.height], "mime": mime}


class GlbBuilder:
    """Minimal deterministic glTF 2.0 binary writer (one buffer, embedded images)."""

    def __init__(self):
        self.bin = bytearray()
        self.doc = {"asset": {"version": "2.0", "generator": GENERATOR}, "scene": 0, "scenes": [{"nodes": []}],
                    "nodes": [], "meshes": [], "materials": [], "accessors": [], "bufferViews": [],
                    "buffers": [], "images": [], "textures": [], "samplers": []}
        self._tex_cache: dict[str, int] = {}
        self.texture_log: list[dict] = []
        self.extensions: set[str] = set()

    def view(self, data: bytes, target: int | None = None) -> int:
        self.bin += b"\0" * (-len(self.bin) % 4)
        bv = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bin += data
        self.doc["bufferViews"].append(bv)
        return len(self.doc["bufferViews"]) - 1

    def accessor(self, arr: np.ndarray, kind: str, target: int, minmax: bool = False) -> int:
        arr = np.ascontiguousarray(arr)
        comp = _GL_UINT if arr.dtype == np.uint32 else _GL_FLOAT
        a = {"bufferView": self.view(arr.tobytes(), target), "componentType": comp, "count": int(arr.shape[0]), "type": kind}
        if minmax:
            a["min"] = [float(v) for v in arr.min(0)]
            a["max"] = [float(v) for v in arr.max(0)]
        self.doc["accessors"].append(a)
        return len(self.doc["accessors"]) - 1

    def texture(self, src, name: str) -> int:
        data, mime, info = encode_texture(src)
        h = hashlib.sha256(data).hexdigest()
        if h in self._tex_cache:
            return self._tex_cache[h]
        if not self.doc["samplers"]:
            self.doc["samplers"].append({"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071})
        self.doc["images"].append({"name": name, "bufferView": self.view(data), "mimeType": mime})
        self.doc["textures"].append({"source": len(self.doc["images"]) - 1, "sampler": 0})
        idx = len(self.doc["textures"]) - 1
        self._tex_cache[h] = idx
        self.texture_log.append({"name": name, "bytes": len(data), "sha256": h, **info})
        return idx

    def to_bytes(self) -> bytes:
        doc = {k: v for k, v in self.doc.items() if v != []}
        doc["buffers"] = [{"byteLength": len(self.bin) + (-len(self.bin) % 4)}]
        if self.extensions:
            doc["extensionsUsed"] = sorted(self.extensions)
        js = json.dumps(doc, separators=(",", ":"), allow_nan=False).encode("utf-8")
        js += b" " * (-len(js) % 4)
        binary = bytes(self.bin) + b"\0" * (-len(self.bin) % 4)
        body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\0") + binary
        return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _foreign_texture_indices(node, path="") -> list[str]:
    """textureInfo dicts that carry an ``index`` from some other glTF document (callers must use ``image``)."""
    bad = []
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, dict) and k.endswith("Texture") and "index" in v and "image" not in v:
                bad.append(f"{path}.{k}" if path else k)
            bad += _foreign_texture_indices(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            bad += _foreign_texture_indices(v, f"{path}[{i}]")
    return bad


def build_material(b: GlbBuilder, name: str, spec: dict, lens_rule: bool = False) -> tuple[dict, dict]:
    """glTF material dict from a spec (see module doc). Returns (material, summary).
    ``lens_rule`` (front-sheet lens): KHR_materials_volume is removed and doubleSided forced off."""
    m = copy.deepcopy(spec.get("gltf") or {})
    bad = _foreign_texture_indices(m)
    if bad:
        raise ValueError(f"Material {name!r}: texture slots {bad} carry a foreign 'index'; pass {{'image': ...}}")
    overrides = []
    m["name"] = m.get("name") or name
    pbr = m.setdefault("pbrMetallicRoughness", {})
    if "base_color" in spec:
        bc = [float(v) for v in spec["base_color"]]
        pbr["baseColorFactor"] = (bc + [1.0])[:4] if len(bc) == 3 else bc[:4]
    if "metallic" in spec:
        pbr["metallicFactor"] = float(spec["metallic"])
    if "roughness" in spec:
        pbr["roughnessFactor"] = float(spec["roughness"])
    for key, slot, holder in (("base_color_texture", "baseColorTexture", pbr),
                              ("metallic_roughness_texture", "metallicRoughnessTexture", pbr),
                              ("normal_texture", "normalTexture", m)):
        if spec.get(key) is not None:
            holder[slot] = {"image": spec[key]}
    # resolve {"image": ...} placeholders anywhere in the material into embedded textures
    def resolve(node, path):
        if isinstance(node, dict):
            if "image" in node and "index" not in node:
                img = node.pop("image")
                node["index"] = b.texture(img, f"{name}:{path}")
            for k, v in list(node.items()):
                resolve(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                resolve(v, f"{path}[{i}]")
    resolve(m, "")
    ext = m.setdefault("extensions", {})
    if "transmission" in spec:
        ext["KHR_materials_transmission"] = {"transmissionFactor": float(spec["transmission"])}
    if "ior" in spec:
        ext["KHR_materials_ior"] = {"ior": float(spec["ior"])}
    if spec.get("double_sided") is not None:
        m["doubleSided"] = bool(spec["double_sided"])
    if spec.get("lens_appearance") is not None:
        # canonical optics: the runtime replaces this material by its LensAppearance transport (unblurred background,
        # density over the lens-local height v, angle-dependent reflectance); the glTF PBR fields stay a flat
        # transmissive fallback for other viewers (a 1x64 texture on lens uv v would read upside down there, since
        # the canonical v runs bottom 0 -> top 1)
        ext[LENS_APPEARANCE_EXTENSION] = {"schema_version": 1, "texcoord": 0,
                                          "appearance": copy.deepcopy(spec["lens_appearance"])}
        if pbr.pop("baseColorTexture", None) is not None:
            overrides.append("canonical_dropped_fallback_texture")
        m.pop("emissiveTexture", None)
        m.pop("emissiveFactor", None)
        ext.pop("KHR_materials_specular", None)
        m["doubleSided"] = False
    if lens_rule:
        # M0 rule: a double-sided transmissive lens compounds its tint (back faces enter the transmission
        # pre-pass); a volume thickness shifts the refracted sample onto the rim. The runtime's own
        # canonical optics use FrontSide + thickness 0.
        if ext.pop("KHR_materials_volume", None) is not None:
            overrides.append("removed_KHR_materials_volume")
        if m.get("doubleSided"):
            m["doubleSided"] = False
            overrides.append("forced_single_sided")
    if not ext:
        m.pop("extensions")
    for e in m.get("extensions", {}):
        b.extensions.add(e)
    t = m.get("extensions", {}).get("KHR_materials_transmission", {}).get("transmissionFactor", 0.0)
    summary = {"transmission": float(t), "double_sided": bool(m.get("doubleSided", False)),
               "canonical": LENS_APPEARANCE_EXTENSION in m.get("extensions", {}),
               "alpha_mode": m.get("alphaMode", "OPAQUE"), "textures": sorted(k for k, v in pbr.items() if isinstance(v, dict)),
               "overrides": overrides}
    return m, summary


# --------------------------------------------------------------------------- per-part attributes
def _per_corner(arr, F: np.ndarray, width: int) -> np.ndarray | None:
    if arr is None:
        return None
    a = np.asarray(arr)
    if a.ndim == 3:
        if a.shape[:2] != (len(F), 3):
            raise ValueError("Per-corner attribute must be (M,3,k)")
        return a[..., :width] if width else a
    return a[F]


def _colors_linear(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c)
    if c.dtype == np.uint8:
        lin = _srgb_to_linear(c[..., :3] / 255.0)
        alpha = c[..., 3:4] / 255.0 if c.shape[-1] == 4 else np.ones(c.shape[:-1] + (1,))
    else:
        lin = np.clip(c[..., :3].astype(float), 0, 1)
        alpha = np.clip(c[..., 3:4].astype(float), 0, 1) if c.shape[-1] == 4 else np.ones(c.shape[:-1] + (1,))
    return np.concatenate([lin, alpha], -1)


def _emit_primitive(b: GlbBuilder, P_m: np.ndarray, N: np.ndarray, UV: np.ndarray | None, COL: np.ndarray | None,
                    material: int) -> dict:
    """Weld identical (position, normal, uv, colour) corners into an indexed primitive."""
    cols = [P_m.reshape(-1, 3), N.reshape(-1, 3)]
    if UV is not None:
        cols.append(UV.reshape(-1, 2))
    if COL is not None:
        cols.append(COL.reshape(-1, 4))
    flat = np.concatenate(cols, 1).astype(np.float32)
    key = np.round(flat.astype(np.float64) * 1e7).astype(np.int64)   # sub-micron / 1e-7 units
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    # keep first-occurrence order for determinism and locality
    rank = np.empty(len(first), np.int64)
    ordr = np.argsort(first, kind="stable")
    rank[ordr] = np.arange(len(first))
    uniq = flat[first[ordr]]
    idx = rank[inv.ravel()].astype(np.uint32)
    pos = uniq[:, 0:3]
    nrm = uniq[:, 3:6]
    nrm = (nrm / np.linalg.norm(nrm, axis=1, keepdims=True)).astype(np.float32)
    attrs = {"POSITION": b.accessor(pos, "VEC3", _ARRAY_BUFFER, minmax=True),
             "NORMAL": b.accessor(nrm, "VEC3", _ARRAY_BUFFER)}
    c = 6
    if UV is not None:
        attrs["TEXCOORD_0"] = b.accessor(uniq[:, c:c + 2], "VEC2", _ARRAY_BUFFER)
        c += 2
    if COL is not None:
        attrs["COLOR_0"] = b.accessor(uniq[:, c:c + 4], "VEC4", _ARRAY_BUFFER)
    return {"attributes": attrs, "indices": b.accessor(idx, "SCALAR", _ELEMENT_ARRAY_BUFFER), "material": material}


def _validate_names(parts: dict) -> tuple[list[str], list[str]]:
    flags = []
    unknown = [n for n in parts if n not in NODE_ORDER]
    if unknown:
        raise ValueError(f"Unknown part names {unknown}; expected {NODE_ORDER}")
    if FRAME_NODE not in parts:
        raise ValueError("A 'frame' part is required")
    lenses = [n for n in parts if PART_ROLE[n] == "lens"]
    if sorted(lenses) not in (sorted(PAIR_LENS_NODES), list(SINGLE_LENS_NODES)):
        raise ValueError(f"Lenses must be lens_R + lens_L or lens_C alone, got {lenses}")
    for t in TEMPLE_NODES:
        if t not in parts:
            flags.append(f"missing_{t}")
    return [n for n in NODE_ORDER if n in parts], flags


# --------------------------------------------------------------------------- canonical lens sheet
def canonical_sheet(P_m: np.ndarray, N_c: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Per-corner arrays of one canonical ``front_sheet_v1`` primitive, as the runtime validates them
    (``validateCanonicalLensSurface``): TEXCOORD_0.y = the lens-local height v = (y - y_min) / (y_max - y_min) of the
    STORED float32 positions (bottom exactly 0, top exactly 1; u likewise on x), every vertex normal toward +Z
    (a normal below ``CANONICAL_MIN_NZ`` is lifted to it and renormalised) and every triangle +Z-wound above the
    runtime's area tolerance in float32 (a sliver below 16 x that tolerance is dropped)."""
    P32 = np.asarray(P_m, np.float32).astype(np.float64)                 # (M, 3, 3) as stored
    e1, e2 = P32[:, 1] - P32[:, 0], P32[:, 2] - P32[:, 0]
    cz = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    allp = P32.reshape(-1, 3)
    diag2 = float(np.sum((allp.max(0) - allp.min(0)) ** 2))
    ok = cz > max(diag2 * 1e-14, 1e-300) * 16.0
    P32, N = P32[ok], np.asarray(N_c, float)[ok].copy()
    N /= np.maximum(np.linalg.norm(N, axis=-1, keepdims=True), 1e-12)
    low = N[..., 2] < CANONICAL_MIN_NZ
    if low.any():
        xy = N[..., :2]
        scale = np.sqrt(max(1.0 - CANONICAL_MIN_NZ ** 2, 0.0)) / np.maximum(np.linalg.norm(xy, axis=-1), 1e-12)
        N[low] = np.c_[xy[low] * scale[low][:, None], np.full(int(low.sum()), CANONICAL_MIN_NZ)]
    Y, X = P32[..., 1], P32[..., 0]
    y0, y1, x0, x1 = Y.min(), Y.max(), X.min(), X.max()
    UV = np.stack([(X - x0) / max(x1 - x0, 1e-12), (Y - y0) / max(y1 - y0, 1e-12)], -1)
    return P32, N, UV, {"dropped_faces": int((~ok).sum()), "normals_lifted": int(low.sum()),
                        "v_rule": "(y - y_min) / (y_max - y_min) of this primitive's float32 positions"}


# --------------------------------------------------------------------------- main entry
def write_glb(parts: dict[str, dict], materials: dict[str, dict], path: str | Path, *,
              origin_mm=None, lens_profile: str = LENS_PROFILE, crease_deg: float = DEFAULT_CREASE_DEG,
              extras: dict | None = None) -> dict:
    """Write the AR GLB. Returns a receipt dict (bytes, sha256, triangles, origin, normals, flags)."""
    if lens_profile not in ("front_sheet", "solid"):
        raise ValueError("lens_profile must be 'front_sheet' or 'solid'")
    order, flags = _validate_names(parts)
    for n in order:
        V, F = np.asarray(parts[n]["V"], float), np.asarray(parts[n]["F"])
        if V.ndim != 2 or V.shape[1] != 3 or F.ndim != 2 or F.shape[1] != 3 or not len(F):
            raise ValueError(f"{n}: V must be (N,3) and F (M,3), non-empty")
        if not np.isfinite(V).all():
            raise ValueError(f"{n}: non-finite vertices")
        if F.min() < 0 or F.max() >= len(V):
            raise ValueError(f"{n}: face index out of range")
    if origin_mm is None:
        origin, origin_receipt = bridge_underside_mm(parts)
    else:
        origin = np.asarray(origin_mm, float).reshape(3)
        origin_receipt = {"method": "supplied", "origin_mm": origin.tolist()}

    b = GlbBuilder()
    mat_index, mat_summary = {}, {}

    def material(name: str, lens: bool = False) -> int:
        if name not in mat_index:
            if name not in materials:
                raise ValueError(f"Material {name!r} is not defined")
            m, s = build_material(b, name, materials[name], lens_rule=lens and lens_profile == "front_sheet")
            b.doc["materials"].append(m)
            mat_index[name] = len(b.doc["materials"]) - 1
            mat_summary[name] = s
        return mat_index[name]

    receipt_parts = {}
    for n in order:
        part = parts[n]
        role = PART_ROLE[n]
        V = np.asarray(part["V"], float)
        F = np.asarray(part["F"], np.int64)
        names = part["material"] if isinstance(part["material"], (list, tuple)) else [part["material"]]
        fm = np.asarray(part.get("face_material", np.zeros(len(F), int)), np.int64)
        if len(fm) != len(F) or fm.min() < 0 or fm.max() >= len(names):
            raise ValueError(f"{n}: face_material must index the material list")
        info: dict = {"role": role, "faces_in": int(len(F)), "vertices_in": int(len(V))}
        keep = np.ones(len(F), bool)
        N_c = None
        if role == "lens":
            for mn in names:
                material(mn, lens=True)
                if mat_summary[mn]["transmission"] <= 0 and not mat_summary[mn]["canonical"]:
                    raise ValueError(f"{n}: lens material {mn!r} needs KHR_materials_transmission > 0 or a canonical "
                                     "lens descriptor")
                if mat_summary[mn]["double_sided"]:
                    flags.append(f"{n}_lens_material_double_sided")
                flags += [f"{n}_lens_material_{o}" for o in mat_summary[mn]["overrides"] if f"{n}_lens_material_{o}" not in flags]
            fn_all, _ = _face_normals(V, F)
            if lens_profile == "front_sheet":
                keep, sheet = lens_front_sheet(V, F, part.get("front_mask"))
                info["front_sheet"] = sheet
                if keep.sum() < 0.2 * len(F) and sheet.get("patches", 1) > 1:
                    flags.append(f"{n}_front_sheet_small")
            fit, fit_ok = None, False
            if part.get("N") is not None:
                N_c = _per_corner(part["N"], F, 3).astype(float)
                info["normals"] = {"method": "supplied"}
            else:
                # fit on the +Z cap only (a solid's walls would bend the field at the rim)
                front = keep & (fn_all[:, 2] > (0.0 if lens_profile == "front_sheet" else 0.5))
                vid = np.unique(F[front if front.any() else keep])
                fit = fit_height_field(V[vid])
                info["normals"] = {"method": "height_field_fit", "degree": fit["degree"],
                                   "rms_mm": fit["rms_mm"], "p95_mm": fit["p95_mm"], "fit_vertices": int(len(vid))}
                if fit["normal"] is None or not np.isfinite(fit["rms_mm"]):
                    flags.append(f"{n}_lens_normal_fit_failed")
                    N_c = crease_normals(V, F, 89.0)
                    info["normals"]["fallback"] = "crease_89"
                else:
                    # A lens is a smooth optical surface: shade it with the fitted field even when the
                    # constructed vertices are lumpy (positions are kept; only shading normals change).
                    if fit["rms_mm"] > LENS_FIT_LUMPY_MM:
                        flags.append(f"{n}_lens_surface_lumpy")
                    fit_ok = True
                    nv = fit["normal"](V)
                    N_c = nv[F]
                    fsel = front if front.any() else keep
                    dev = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", nv[F[fsel]].mean(1) /
                                                                 np.linalg.norm(nv[F[fsel]].mean(1), axis=1, keepdims=True),
                                                                 fn_all[fsel]), -1, 1)))
                    info["normals"]["face_vs_fit_deg_p50_p95"] = [round(float(np.percentile(dev, 50)), 2),
                                                                  round(float(np.percentile(dev, 95)), 2)]
                    if lens_profile == "solid":
                        # back cap: mirrored fitted normals; walls and other faces: flat
                        back = fn_all[:, 2] < -0.5
                        N_c[back] = -nv[F[back]]           # outward normal of the offset back cap
                        side = ~(fn_all[:, 2] > 0.5) & ~back
                        N_c[side] = np.repeat(fn_all[side][:, None, :], 3, 1)
            ring = part.get("edge_ring")
            if ring and lens_profile == "front_sheet":
                uv_src = part.get("UV")
                if uv_src is not None and np.asarray(uv_src).ndim != 2:
                    flags.append(f"{n}_edge_ring_skipped_per_corner_uv")
                elif part.get("COLOR") is not None:
                    flags.append(f"{n}_edge_ring_skipped_vertex_colours")
                else:
                    material(ring["material"], lens=True)
                    if mat_summary[ring["material"]]["transmission"] <= 0 and not mat_summary[ring["material"]]["canonical"]:
                        raise ValueError(f"{n}: edge-ring material {ring['material']!r} needs KHR_materials_transmission > 0")
                    sp = split_edge_ring(V, F[keep], float(ring["width_mm"]), uv_src)
                    fm_sheet = fm[keep][sp["parent"]]
                    if ring["material"] not in names:
                        names = list(names) + [ring["material"]]
                    fm = np.where(sp["ring"], names.index(ring["material"]), fm_sheet)
                    V, F = sp["V"], sp["F"]
                    keep = np.ones(len(F), bool)
                    part = dict(part, UV=sp["UV"])
                    if fit_ok:
                        N_c = fit["normal"](V)[F]
                    else:                                   # supplied/failed normals: smooth the split sheet
                        N_c = crease_normals(V, F, 89.0)
                    info["edge_ring"] = sp["info"]
        else:
            for mn in names:
                material(mn)
                if mat_summary[mn]["transmission"] > 0:
                    raise ValueError(f"{n}: frame/temple material {mn!r} must be opaque (transmission 0); "
                                     "the runtime would treat the part as a lens")
                if mat_summary[mn]["alpha_mode"] != "OPAQUE":
                    flags.append(f"{n}_material_not_opaque")
            if part.get("N") is not None:
                N_c = _per_corner(part["N"], F, 3).astype(float)
                info["normals"] = {"method": "supplied"}
            else:
                cd = float(part.get("crease_deg", crease_deg))
                N_c = crease_normals(V, F, cd)
                info["normals"] = {"method": "crease", "crease_deg": cd}
        N_c = N_c / np.maximum(np.linalg.norm(N_c, axis=-1, keepdims=True), 1e-12)
        UV_c = _per_corner(part.get("UV"), F, 2)
        COL_c = _per_corner(part.get("COLOR"), F, 0)
        if COL_c is not None:
            COL_c = _colors_linear(COL_c)
        P_m = ((V - origin) / 1000.0)[F]
        prims = []
        canonical = role == "lens" and any(mat_summary[mn]["canonical"] for mn in names)
        if canonical:
            if lens_profile != "front_sheet" or not all(mat_summary[mn]["canonical"] for mn in names):
                raise ValueError(f"{n}: canonical lens optics need the front-sheet profile and a descriptor on every "
                                 "lens material (the runtime rejects mixed canonical/legacy optics)")
            info["canonical"] = {}
        for mi, mn in enumerate(names):
            sel = keep & (fm == mi)
            if not sel.any():
                continue
            if canonical:
                Pc, Nc, UVc, cinfo = canonical_sheet(P_m[sel], N_c[sel])
                info["canonical"][mn] = cinfo
                if cinfo["dropped_faces"]:
                    flags.append(f"{n}_canonical_dropped_faces")
                if cinfo["normals_lifted"]:
                    flags.append(f"{n}_canonical_normals_lifted")
                prims.append(_emit_primitive(b, Pc, Nc, UVc, None, material(mn)))
                continue
            prims.append(_emit_primitive(b, P_m[sel], N_c[sel], None if UV_c is None else UV_c[sel],
                                         None if COL_c is None else COL_c[sel], material(mn)))
        if not prims:
            raise ValueError(f"{n}: no faces left to export")
        mesh_doc = {"name": n, "primitives": prims}
        if canonical:
            # GLTFLoader copies MESH extras onto every primitive's Mesh (node extras only reach a single-primitive
            # node), and the runtime validates partRole/lensSurfaceProfile on each optical Mesh
            mesh_doc["extras"] = {"partRole": "lens", "lensSurfaceProfile": CANONICAL_SURFACE_PROFILE,
                                  "lensUVConvention": "lens_local_bottom_0_top_1"}
        b.doc["meshes"].append(mesh_doc)
        b.doc["nodes"].append({"name": n, "mesh": len(b.doc["meshes"]) - 1, "extras": {"partRole": role}})
        b.doc["scenes"][0]["nodes"].append(len(b.doc["nodes"]) - 1)
        info.update({"faces_out": int(keep.sum()), "materials": list(names),
                     "bbox_m": [((V[F[keep]].reshape(-1, 3) - origin) / 1000).min(0).round(5).tolist(),
                                ((V[F[keep]].reshape(-1, 3) - origin) / 1000).max(0).round(5).tolist()]})
        receipt_parts[n] = info
    b.doc["extras"] = {"bsa": {"generator": GENERATOR, "lens_profile": lens_profile, "origin_mm_model": origin.tolist(),
                               "units": "metres", "front": "+Z", "up": "+Y", **(extras or {})}}
    data = b.to_bytes()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "triangles": int(sum(p["faces_out"] for p in receipt_parts.values())),
            "nodes": order, "lens_profile": lens_profile, "origin": origin_receipt, "parts": receipt_parts,
            "materials": mat_summary, "textures": b.texture_log, "flags": flags}


# --------------------------------------------------------------------------- synthetic fixture (M0 test 2)
def extrude_polygon(poly, z_front, thickness, spacing_mm: float = 0.5) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Closed solid from a shapely polygon (mm, x right / y up): front cap on ``z_front(x, y)``, back cap
    ``thickness(x, y)`` behind, walls on every ring. Constrained Delaunay caps (boundary points only,
    so the caps are sliver-rich - deliberately, like a CDT without Steiner points). Returns V, F and a
    per-face region (0 front cap, 1 back cap, 2 wall)."""
    import shapely
    from shapely.geometry import Polygon
    poly = shapely.segmentize(shapely.geometry.polygon.orient(poly, 1.0), spacing_mm)
    rings = [np.asarray(poly.exterior.coords)[:-1]] + [np.asarray(r.coords)[:-1] for r in poly.interiors]
    ring_pts = np.vstack(rings)
    key = {tuple(np.round(p, 9)): i for i, p in enumerate(ring_pts)}
    tris = shapely.constrained_delaunay_triangles(Polygon(poly.exterior.coords, [r.coords for r in poly.interiors]))
    T = []
    for t in tris.geoms:
        c = np.asarray(t.exterior.coords)[:3]
        idx = [key[tuple(np.round(p, 9))] for p in c]
        a, b, d = ring_pts[idx]
        if (b[0] - a[0]) * (d[1] - a[1]) - (b[1] - a[1]) * (d[0] - a[0]) < 0:
            idx = [idx[0], idx[2], idx[1]]
        T.append(idx)
    T = np.asarray(T, np.int64)
    n = len(ring_pts)
    zf = z_front(ring_pts[:, 0], ring_pts[:, 1])
    th = thickness(ring_pts[:, 0], ring_pts[:, 1])
    V = np.vstack([np.c_[ring_pts, zf], np.c_[ring_pts, zf - th]])
    faces, region = [T, T[:, ::-1] + n], [np.zeros(len(T), np.int8), np.ones(len(T), np.int8)]
    base = 0
    for r in rings:
        k = len(r)
        i = np.arange(k) + base
        j = (np.arange(k) + 1) % k + base
        # rings are oriented (exterior CCW, holes CW seen from +Z): outward wall = (i, i', j) order below
        faces.append(np.c_[i, i + n, j])
        faces.append(np.c_[j, i + n, j + n])
        region.append(np.full(2 * k, 2, np.int8))
        base += k
    return V, np.vstack(faces), np.concatenate(region)


def box_tube(x0: float, y0: float, z0: float, z1: float, w: float, h: float, step: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    """Closed rectangular tube along -Z (a synthetic temple), subdivided every ``step`` mm."""
    zs = np.linspace(z0, z1, max(2, int(round(abs(z1 - z0) / step)) + 1))
    sq = np.array([[x0 - w / 2, y0 - h / 2], [x0 + w / 2, y0 - h / 2], [x0 + w / 2, y0 + h / 2], [x0 - w / 2, y0 + h / 2]])
    V = np.vstack([np.c_[sq, np.full(4, z)] for z in zs])
    F = []
    for s in range(len(zs) - 1):
        a, b = 4 * s, 4 * (s + 1)
        for k in range(4):
            k2 = (k + 1) % 4
            F += [[a + k, a + k2, b + k2], [a + k, b + k2, b + k]]
    F = np.asarray(F, np.int64)
    last = 4 * (len(zs) - 1)
    F = np.vstack([F, [[0, 2, 1], [0, 3, 2]], [[last, last + 1, last + 2], [last, last + 2, last + 3]]])
    # orient every face outward (the tube is convex in cross-section)
    c = V.mean(0)
    fc = V[F].mean(1)
    fn, _ = _face_normals(V, F)
    out = fc - c
    out[:-4, 2] = 0.0                       # side faces: radial in x/y
    flip = np.einsum("ij,ij->i", fn, out) < 0
    F[flip] = F[flip][:, ::-1]
    return V, F


def synthetic_parts(tuck_mm: float = 0.6, seed: int = 0) -> tuple[dict, dict]:
    """A simple frame plate with two lens holes, lenses tucked ``tuck_mm`` under the rim at mid-rim
    depth (1.4 mm solids), two box temples. MODEL frame mm. Returns (parts, materials)."""
    from shapely.geometry import Point, Polygon, box
    from shapely import affinity
    R_wrap = 200.0
    zf = lambda x, y: -(x ** 2) / (2 * R_wrap)                     # front cap of the plate
    outer = box(-70, -24, 70, 24).buffer(-4, join_style=1).buffer(4, join_style=1)
    notch = Polygon([(-10, -25), (10, -25), (3, -6), (-3, -6)])      # nose notch: bridge underside y = -6
    holes = [affinity.scale(Point(cx, 2).buffer(1.0, 64), 25, 18) for cx in (32.0, -32.0)]
    frame_poly = outer.difference(notch)
    for h in holes:
        frame_poly = frame_poly.difference(h)
    fV, fF, freg = extrude_polygon(frame_poly, zf, lambda x, y: np.full_like(x, 4.0), spacing_mm=1.0)
    # frame texture: planar front projection of a deterministic mottled pattern
    rng = np.random.default_rng(seed)
    tex = rng.normal(size=(64, 192))
    from scipy.ndimage import gaussian_filter, zoom
    tex = zoom(gaussian_filter(tex, 2.0), 8, order=1)
    tex = (tex - tex.min()) / (np.ptp(tex) + 1e-9)
    img = np.stack([40 + 90 * tex, 30 + 50 * tex, 25 + 30 * tex], -1).astype(np.uint8)
    fUV = np.c_[(fV[:, 0] + 70) / 140, (24 - fV[:, 1]) / 48]
    parts = {"frame": {"V": fV, "F": fF, "UV": fUV, "material": "frame"}}
    for side, h in zip(("R", "L"), holes):                            # +X = viewer's right = lens_R
        lens_poly = h.buffer(tuck_mm, join_style=1)
        cx = float(h.centroid.x)
        # mid-rim lens: 1.3 mm behind the plate front, plus a base-4-like bulge toward +Z at its centre
        zl = lambda x, y, cx=cx: zf(x, y) - 1.3 + 0.8 * np.clip(1 - ((x - cx) / 25.6) ** 2 - ((y - 2) / 18.6) ** 2, 0, 1)
        lV, lF, _ = extrude_polygon(lens_poly, zl, lambda x, y: np.full_like(x, 1.4), spacing_mm=0.5)
        y0, y1 = lV[:, 1].min(), lV[:, 1].max()
        x0, x1 = lV[:, 0].min(), lV[:, 0].max()
        lUV = np.c_[(lV[:, 0] - x0) / (x1 - x0), (y1 - lV[:, 1]) / (y1 - y0)]
        parts[f"lens_{side}"] = {"V": lV, "F": lF, "UV": lUV, "material": "lens"}
    for side, sx in (("R", 1.0), ("L", -1.0)):
        tV, tF = box_tube(sx * 68.5, 8.0, zf(68.5, 8.0) - 4.0, -148.0, 3.0, 5.0)   # ~135 mm arm
        col = np.tile(np.array([[70, 45, 35]], np.uint8), (len(tV), 1))
        parts[f"temple_{side}"] = {"V": tV, "F": tF, "COLOR": col, "material": "temple"}
    materials = {
        "frame": {"base_color_texture": img, "metallic": 0.0, "roughness": 0.35},
        "temple": {"base_color": [1, 1, 1, 1], "metallic": 0.0, "roughness": 0.4},
        "lens": {"base_color": [1, 1, 1, 1], "base_color_texture": gradient_texture([0.08, 0.05, 0.03], [0.5, 0.42, 0.3]),
                 "metallic": 0.0, "roughness": 0.05, "transmission": 1.0, "ior": 1.5},
    }
    return parts, materials


# --------------------------------------------------------------------------- S9 stage
S9 = "s9_export"
UPSTREAM = ("s2_front", "s5_temples", "s6_assembly", "s7_texture", "s8_lens")


def _find_vec3(d, *needles):
    """First 3-vector under a key containing all needles (recursive), e.g. the S6 bridge underside."""
    if isinstance(d, dict):
        for k, v in d.items():
            if all(n in str(k).lower() for n in needles) and isinstance(v, (list, tuple)) and len(v) == 3 \
                    and all(isinstance(x, (int, float)) for x in v):
                return [float(x) for x in v]
        for v in d.values():
            r = _find_vec3(v, *needles)
            if r is not None:
                return r
    return None


def temple_arrays(a5: dict, s5: dict, side: str, a6: dict | None = None
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, str] | None:
    """The S5 geometry the model delivers for ``temple_<side>``: the whole node (arm + donors) when the arm was
    accepted; when S5 rejected the ARM, still its front-piece donors (``donor_<side>_faces``: pads, bridge half,
    hinge blocks, rimless hardware, which S6 tucked the lens under) - dropping the node would delete them too.
    With the S6 arrays ``a6``, S6's final donor anchoring (``assemble.donor_anchoring``: donor components moved into
    contact or dropped, tested on the delivered geometry) is applied. Returns (V, F, UV or None, "full" |
    "donors_only") or None."""
    if f"temple_{side}_V" not in a5:
        return None
    V, F = np.asarray(a5[f"temple_{side}_V"]), np.asarray(a5[f"temple_{side}_F"])
    UV = a5.get(f"temple_{side}_UV")
    if a6 is not None and f"donor_keep_{side}" in a6 and len(a6[f"donor_keep_{side}"]) == len(F) \
            and len(a6[f"donor_offset_{side}"]) == len(V):
        keep = np.asarray(a6[f"donor_keep_{side}"], bool)
        V = (np.asarray(V, np.float64) + np.asarray(a6[f"donor_offset_{side}"], np.float64)).astype(np.asarray(V).dtype)
        if not keep.all():
            rng = a5.get(f"donor_{side}_faces")
            if rng is not None:                               # the donor face range shrinks by the dropped faces
                d0, d1 = int(rng[0]), int(rng[1])
                a5 = dict(a5)
                a5[f"donor_{side}_faces"] = np.array([d0 - int((~keep[:d0]).sum()), d1 - int((~keep[:d1]).sum())],
                                                     np.int64)
            dt = F.dtype
            F = F[keep]
            used = np.unique(F.ravel())
            remap = np.full(len(V), -1, np.int64)
            remap[used] = np.arange(len(used))
            V, F = V[used], remap[F].astype(dt)
            UV = None if UV is None else np.asarray(UV)[used]
    acc = (s5.get(side) or s5.get(f"temple_{side}") or {}).get("accepted", True)
    if acc:
        return V, F, UV, "full"
    rng = a5.get(f"donor_{side}_faces")
    if rng is None or int(rng[1]) <= int(rng[0]):
        return None
    Fd = F[int(rng[0]):int(rng[1])]
    used = np.unique(Fd.ravel())
    remap = np.full(len(V), -1, np.int64)
    remap[used] = np.arange(len(used))
    return V[used], remap[Fd].astype(np.int32), None if UV is None else np.asarray(UV)[used], "donors_only"


def gather_inputs(product: str, run: str) -> tuple[dict, dict, list | None, list[str]]:
    """Build (parts, materials, origin_mm, notes) from the S2/S5/S6/S7/S8 artifacts (DESIGN.md).

    S7's material plan is read as ``materials: name -> {"part": frame|temple_R|temple_L,
    "region": [frame_region ids] | null, "texture": file in s7/, "uv": arrays key or "frame_uv_px",
    "factors": {base_color, metallic, roughness}}``; anything it does not cover gets a flat material
    and a note. S8's ``gltf_material`` is the lens material; a ``gradient`` block becomes the 1x64
    baseColorTexture on lens uv v. This adapter is provisional until S5-S8 artifacts exist."""
    from .core import stage_dir
    notes: list[str] = []
    s2, _ = stage_dir(run, product, "s2_front").load()
    s5, a5 = stage_dir(run, product, "s5_temples").load()
    s6, a6 = stage_dir(run, product, "s6_assembly").load()
    s7d = stage_dir(run, product, "s7_texture")
    s7, a7 = s7d.load()
    s8, _ = stage_dir(run, product, "s8_lens").load()
    parts: dict[str, dict] = {"frame": {"V": a6["frame_V"], "F": a6["frame_F"]}}
    region = a6.get("frame_region")
    sides = [l.get("side") for l in s2.get("lenses", [])]
    i = 1
    while f"lens{i}_V" in a6:
        side = sides[i - 1] if i - 1 < len(sides) and sides[i - 1] in ("R", "L", "C") else None
        if side is None:
            side = "C" if (i == 1 and f"lens{i + 1}_V" not in a6) else ("R" if a6[f"lens{i}_V"][:, 0].mean() > 0 else "L")
            notes.append(f"lens{i}: side inferred as {side}")
        parts[f"lens_{side}"] = {"V": a6[f"lens{i}_V"], "F": a6[f"lens{i}_F"], "material": "lens"}
        if f"lens{i}_uv" in a6:
            parts[f"lens_{side}"]["UV"] = a6[f"lens{i}_uv"]
        i += 1
    for side in ("R", "L"):
        ta = temple_arrays(a5, s5, side, a6)
        if ta is None:
            notes.append(f"temple_{side} not exported (missing, or rejected in S5 without donors)")
            continue
        TV, TF, TUV, how = ta
        parts[f"temple_{side}"] = {"V": TV, "F": TF}
        if TUV is not None:
            parts[f"temple_{side}"]["UV"] = TUV
        if how != "full":
            parts[f"temple_{side}"]["donors_only"] = True
            notes.append(f"temple_{side}: arm rejected in S5; its front-piece donors exported alone")
    materials: dict[str, dict] = {}
    plan = s7.get("materials", {}) or {}
    assigned = {n: np.full(len(p["F"]), -1) for n, p in parts.items() if PART_ROLE[n] != "lens"}
    names: dict[str, list] = {n: [] for n in assigned}
    for mname, m in plan.items():
        part = m.get("part")
        if part not in assigned:
            notes.append(f"S7 material {mname!r}: unknown part {part!r}")
            continue
        f = m.get("factors") or {}
        spec = {"metallic": f.get("metallic", 0.0), "roughness": f.get("roughness", 0.45)}
        if f.get("base_color") is not None:
            spec["base_color"] = f["base_color"]
        tex = m.get("texture")
        if tex and (s7d.root / tex).exists():
            spec["base_color_texture"] = s7d.root / tex
        mr = m.get("metallic_roughness_texture")        # S7 material classes: metal / dielectric texels on one part
        if mr and (s7d.root / mr).exists():
            spec["metallic_roughness_texture"] = s7d.root / mr
        materials[mname] = spec
        sel = np.ones(len(parts[part]["F"]), bool)
        if m.get("region") is not None and part == "frame" and region is not None:
            sel = np.isin(region, m["region"])
        sel &= assigned[part] < 0
        names[part].append(mname)
        assigned[part][sel] = len(names[part]) - 1
        uv = m.get("uv")
        if uv == "frame_uv_px" and "frame_uv_px" in a6 and tex:
            with Image.open(s7d.root / tex) as im:
                w, h = im.size
            parts[part]["UV"] = a6["frame_uv_px"] / np.array([w, h], float)
        elif isinstance(uv, str) and uv in a7 and not parts[part].get("donors_only"):
            parts[part]["UV"] = a7[uv]              # (a donors-only temple keeps its S5 UV subset: S7 copies S5's UVs)
    for n in assigned:
        if (assigned[n] < 0).any():
            materials.setdefault("unplanned_fill", {"base_color": [0.18, 0.18, 0.18, 1.0], "metallic": 0.0, "roughness": 0.45})
            names[n].append("unplanned_fill")
            assigned[n][assigned[n] < 0] = len(names[n]) - 1
            notes.append(f"{n}: faces not covered by the S7 plan got a flat fill")
        parts[n]["material"] = names[n]
        parts[n]["face_material"] = assigned[n]
    gm = copy.deepcopy(s8.get("gltf_material") or {})
    ext = gm.get("extensions", {})
    spec = {"gltf": gm, "transmission": ext.get("KHR_materials_transmission", {}).get("transmissionFactor", 1.0),
            "ior": ext.get("KHR_materials_ior", {}).get("ior", 1.5), "double_sided": False}
    grad = s8.get("gradient")
    tex_info = gm.get("pbrMetallicRoughness", {}).get("baseColorTexture")
    if grad and (not isinstance(tex_info, dict) or "index" in tex_info or "image" not in tex_info):
        gm.get("pbrMetallicRoughness", {}).pop("baseColorTexture", None)
        spec["base_color_texture"] = gradient_texture(grad["top_linear_rgb"], grad["bottom_linear_rgb"], grad.get("profile"))
        spec["base_color"] = [1.0, 1.0, 1.0, 1.0]
    elif "baseColorFactor" not in gm.get("pbrMetallicRoughness", {}) and s8.get("tint_linear_rgb"):
        spec["base_color"] = list(s8["tint_linear_rgb"])[:3] + [1.0]
    if s8.get("lens_appearance") is not None:
        # canonical optics (the runtime's LensAppearance transport): the descriptor carries the look; the PBR fields
        # become a flat transmissive fallback (tint = the measured transmission)
        spec["lens_appearance"] = s8["lens_appearance"]
        spec.pop("base_color_texture", None)
        spec["base_color"] = list(s8.get("tint_linear_rgb") or [1.0, 1.0, 1.0])[:3] + [1.0]
    else:
        notes.append("S8 has no lens_appearance descriptor: legacy KHR_materials_transmission lens (blurred in the runtime)")
    materials["lens"] = spec
    ring = s8.get("edge_ring")
    if ring and float(ring.get("width_mm") or 0) > 0:
        # clear lens: a frosted band along the sheet outline, a second lens primitive (same triangles as the sheet,
        # so no seam or silhouette change) with its own canonical descriptor (lens optics may not mix canonical and
        # legacy materials in the runtime)
        materials["lens_edge_ring"] = {"base_color": list(ring.get("base_color") or [0.9, 0.9, 0.9, 1.0]),
                                       "metallic": 0.0, "roughness": float(ring.get("roughness", 0.5)),
                                       "transmission": float(ring["transmission"]), "ior": 1.5, "double_sided": False}
        if spec.get("lens_appearance") is not None:
            if ring.get("lens_appearance") is None:
                raise ValueError("S8 edge ring has no lens_appearance descriptor while the lens is canonical")
            materials["lens_edge_ring"]["lens_appearance"] = ring["lens_appearance"]
        for n in parts:
            if PART_ROLE[n] == "lens":
                parts[n]["edge_ring"] = {"width_mm": float(ring["width_mm"]), "material": "lens_edge_ring"}
    origin = _find_vec3(s6, "bridge")
    if origin is None:
        notes.append("S6 has no bridge underside point; origin derived from the geometry")
    return parts, materials, origin, notes


def lens_nodes(result: dict) -> list[str]:
    """The lens nodes an S9 result exported (lens_R + lens_L, or lens_C)."""
    return [n for n in ((result.get("export") or {}).get("nodes") or []) if PART_ROLE.get(n) == "lens"]


def m1_criterion_1(result: dict) -> bool:
    """M1 criterion 1 for one S9 result: contract ok AND runtime_compatible in the AR harness AND every exported
    lens node detected as optical by the runtime (optical meshes >= lens nodes; a pair with one lens found fails)."""
    ac = result.get("archeck") or {}
    need = max(1, len(lens_nodes(result)))
    return bool((result.get("contract") or {}).get("ok") and ac.get("status") == "runtime_compatible"
                and (ac.get("optical_meshes_detected") or 0) >= need)


def attach_archeck(result: dict, harness: dict, name: str, product: str, run: str, sheet_dir: Path | None = None) -> dict:
    """Merge the row ``name`` of an ``archeck.run`` result into an S9 result dict (in place) and recompute
    ``m1_criterion_1``. Used by ``export_product`` (one model per harness call) and by ``bsa.pipeline``
    (all products in one harness call). With ``sheet_dir`` it also writes ``sheet.png`` there."""
    from . import archeck
    from .core import PRODUCTS
    m = (harness.get("models") or {}).get(name) or {"status": "not_run", "runtime_compatible": False}
    result["archeck"] = {"status": m.get("status"), "optical_meshes_detected": m.get("optical_meshes_detected"),
                         "lens_mesh_names": m.get("lens_mesh_names", []),
                         "synthetic_fit_ready": m.get("synthetic_fit_ready"),
                         "continuity_failure": m.get("continuity_failure"), "error": m.get("error"),
                         "renders": m.get("renders", []), "harness_status": harness.get("harness_status"),
                         "harness_out_dir": harness.get("out_dir"), "case": name,
                         "model_sha256": m.get("model_sha256"),
                         "views": [v.get("id") for v in json.loads(Path(harness["manifest_path"]).read_text()).get("ar_views", [])]
                         if harness.get("manifest_path") and Path(harness["manifest_path"]).exists() else None}
    flags = [f for f in result.get("flags", []) if f != "ar_check_failed"]
    if not (m.get("runtime_compatible") and (m.get("optical_meshes_detected") or 0) > 0):
        flags.append("ar_check_failed")
    result["flags"] = flags
    if sheet_dir is not None:
        p = PRODUCTS.get(product)
        photos = [str(p.photo_path(v)) for v in ("front", "angled")] if p else []
        result["sheet"] = archeck.photo_render_sheet(
            [(f"{product}\nphotos | AR renders", photos, m.get("renders", []))], Path(sheet_dir) / "sheet.png",
            title=f"BSA {run} {product}: export in the actual TryOnRenderer")
    result["m1_criterion_1"] = m1_criterion_1(result)
    return result


def export_product(product: str, run: str, parts: dict, materials: dict, *, origin_mm=None, notes=None,
                   ar: bool = True) -> dict:
    """Write + contract-check + AR-check one product's GLB into its s9_export stage dir, with a sheet.
    ``ar=False`` leaves the AR check to the caller (``bsa.pipeline`` batches all products in one harness
    call and merges each row with ``attach_archeck``); ``m1_criterion_1`` is then False until merged."""
    from . import archeck, contract
    from .core import stage_dir
    sd = stage_dir(run, product, S9)
    glb = sd.root / "model.glb"
    # no run name in the asset: identical exports of different runs are byte-identical (provenance is in result.json)
    receipt = write_glb(parts, materials, glb, origin_mm=origin_mm, extras={"product": product})
    chk = contract.check(glb)
    result = {"product": product, "run": run, "status": "exported", "glb": str(glb), "export": receipt,
              "contract": chk, "notes": list(notes or []), "flags": list(receipt["flags"])}
    if not chk["ok"]:
        result["flags"].append("contract_failed:" + ",".join(chk["failures"]))
    if ar:
        name = f"{product}-bsa-{run}"
        a = archeck.run({name: glb}, sd.root / "ar", description=f"BSA {run} export of {product}")
        attach_archeck(result, a, name, product, run, sheet_dir=sd.root)
    result["m1_criterion_1"] = m1_criterion_1(result)
    sd.save(result)
    return result


def run(product: str, run: str = "m1", force: bool = False, ar: bool = True) -> dict:
    """S9 stage entry (DESIGN.md): reads S2/S5/S6/S7/S8, writes s9_export/{model.glb, result.json,
    sheet.png, ar/}. With an upstream artifact missing nothing is written: {"status": "blocked"}."""
    from .core import run_dir, stage_dir
    if (run_dir(run, product) / S9 / "result.json").exists() and not force:
        return stage_dir(run, product, S9).load()[0]
    missing = [s for s in UPSTREAM if not (run_dir(run, product) / s / "result.json").exists()]
    if missing:
        return {"product": product, "run": run, "status": "blocked", "missing_upstream": missing}
    parts, materials, origin, notes = gather_inputs(product, run)
    return export_product(product, run, parts, materials, origin_mm=origin, notes=notes, ar=ar)
