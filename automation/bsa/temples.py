"""S5 temples: donor temples cut from the S1 canonical generator (DESIGN.md S5).

Pipeline per product (all in the MODEL frame, mm; +X = viewer's right in the front photo):

1. Endpiece back face. For each side, the lateral cross-section of the generator (vertices with
   |x| >= the temple's inner x minus 15 mm) is profiled in 0.5 mm slabs along z. A reference band well
   inside the temple (45-75 % of the model depth behind the front) gives the temple's own height h_ref
   and width w_ref. Walking forward from that band, the first slab whose height exceeds
   1.5 h_ref + 2 mm or whose width exceeds 2 w_ref + 3 mm is front piece: its z is the endpiece back
   face z_b. (The literal "20 mm slab" of the front-piece rule does not locate a back face: the temple
   is continuous with the endpiece, so the slab always ends at exactly 20 mm; on the shields the
   endpiece sits 34-37 mm behind the front. The S4 thickness field is not used: S4 does not exist yet.)
2. Split plane (``choose_split``): within 10 mm behind min(z_b(R), z_b(L)) - 1 mm, the plane with the SIMPLEST
   lateral cross-section (smallest perimeter over both sides; the front-most within 5 % of the minimum), so the
   split never runs through a relief feature at the temple root (Oakley's 'O' icon, a hinge knuckle). The lens is
   part of the front piece's lateral cross-section, so the plane is behind it by construction. (S3's lens-face
   labels were tried as an extra clearance and rejected: through the front camera they also catch the hinge and
   temple tips seen through the lens, e.g. VB's hinge at 19 mm and Miu's tips at 141 mm behind the front.)
3. Keep the largest connected component entirely behind the plane on each side (x > 0 = temple_R, the
   viewer's right; x < 0 = temple_L); connectivity uses positions welded at 1 micron so UV seams do not
   split it. Crossings = the separate places where that component meets the plane (its full-resolution
   boundary loops on the plane; loops sharing a vertex count once). 1 = a clean temple cut, 2 = temple plus
   a hinge part. ``lateral_sections_at_plane`` (every piece of that side the plane slices, fragments
   included) is reported alongside.
4. Quadric decimation to ~12k faces (boundary preserved), non-manifold repair and consistent
   orientation, then the generator UVs are transferred from the full-resolution donor: each vertex takes
   the UV at its closest source point; a face straddling a UV seam samples a single island (each corner
   takes the nearest source vertex of its centroid's island). ``uv.colour_check`` compares the base colour
   through the new UVs with the donor's own colour at each face centre.
5. Rigid refinement per temple through the frozen S3 cameras on its near side photo (left.jpg is shot
   from +X, so it refines temple_R; right.jpg refines temple_L) plus the back photo: hinge opening about a
   vertical axis and lift about a lateral axis through the cut-loop centroid, plus a translation, all
   bounded (6 deg, 4 deg, 1.5 mm) with a small quadratic prior toward the donor pose. Loss = sum over the
   two views of 1 - IoU on the pixels the fixed front piece does not cover. The back view is in the loss
   because the opening is nearly invisible from the side: a side-only fit moved it by 0.9 deg for a 1e-6
   loss gain and cost 0.14 back-view IoU on VB.
6. Loft the cut end straight forward (+z) 1 mm into the fixed donor block in front of the split and cap it
   (fan), cap any other hole, so each arm is a closed 2-manifold (the S9 contract requires it).
6b. Donor geometry (``attach_donors`` -> ``bsa.donor.extract``): everything of the generator in front of the
   split that the S6 plate does not model - the endpiece / hinge block between the plate back and the split (with
   the temple-root relief), pads and pad arms, a metal bridge's back, and ALL rimless hardware (S6 keeps only a
   hidden core there) - cut cleanly on the plate back (0.3 mm into the plate), the photo footprint and the other
   fit views' silhouettes, closed, with the generator UVs; appended to ``temple_<s>`` (x > 0 / x < 0) as further
   closed components, textured exactly like the arm (``donor_<s>_faces`` = their face range). Reads S2 and S4.
7. Checks: crossings <= 2, gap (max distance the loft bridges, including any in-plane shift of the cut
   end caused by the refinement) <= 3 mm, |length - side-photo length| / side-photo length <= 8 %,
   not folded (inward angle of the hinge->tip direction in the top view <= 30 deg, and the back-view
   IoU of the refined assembly no worse than the donor's by more than 0.02), closed.

Length convention: ``length_mm`` = reach along -z from the endpiece back face z_b to the rear-most point
of the temple. The side-photo reading takes the matte's extreme pixel along the image direction of -z and
back-projects it (exact ray of the S3 camera) onto the plane x = x_tip of the model temple it belongs to:
the near temple, unless the far temple's own model tip is > 1 mm nearer that pixel (a camera a few degrees
off 90 deg shows the FAR tip as the extreme one). A temple left without a reading of its own is compared
with the other temple's reading (flag ``<side>:length_from_symmetric_reading``). The model is read the same
way from its render (``model_length_read_mm``), so the reading method itself is checked. Never an
Hs/Hfront image-height ratio.

Arrays beyond DESIGN.md: ``join_<s>`` (K,3) mm, the lofted cut-end loop on the join plane (1 mm in front of the
split, inside the donor block); ``donor_<s>_faces`` [start, end) face range of the donor components in
``temple_<s>_F``.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage, optimize
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from reconstruction.camera import Camera, project

from . import cameras, generator, raster
from .core import PRODUCTS, NormFrame, load_photo, project_mm, stage_dir

STAGE = "s5_temples"
SIDES = ("R", "L")
SIGN = {"R": 1.0, "L": -1.0}
NEAR_VIEW = {"R": "left", "L": "right"}   # left.jpg: yaw +90, from +X, so temple_R (x > 0) is nearest

WELD_MM = 1e-3
REF_BAND = (0.45, 0.75)          # temple reference band, as a share of the model depth behind the front
LATERAL_MARGIN_MM = 15.0         # lateral band: |x| >= (inner |x| of the temple reference band) - 15 mm
FULL_LOOP_TOL_MM = 0.75         # full-resolution boundary loops within this of the plane are cut loops
SLAB_STEP_MM = 0.25
SLAB_HALF_MM = 0.5
H_FACTOR, H_PAD_MM = 1.5, 2.0    # front piece where the lateral height > 1.5 h_ref + 2 mm ...
W_FACTOR, W_PAD_MM = 2.0, 3.0    # ... or the lateral width > 2 w_ref + 3 mm
CUT_BEHIND_MM = 1.0              # DESIGN: z_cut = front piece back face - 1 mm (the front-most split allowed)
SPLIT_SEARCH_MM = 10.0           # the split may move this far further back to the simplest cross-section
SPLIT_STEP_MM = 0.5
SPLIT_TOL = 0.05
LOFT_INTO_DONOR_MM = 1.0
CUT_LOOP_MEDIAN_MM = 0.5         # a cut loop lies ON the split plane (median vertex within this of it)         # the refined arm's cut end is lofted this far forward, into the fixed donor block

TIP_ASSIGN_MARGIN_MM = 1.0       # a side-view tip reading goes to the far temple only when its tip is > 1 mm nearer
MAX_CROSSINGS = 2
MAX_GAP_MM = 3.0
MAX_LENGTH_MISMATCH = 0.08
FOLD_MAX_DEG = 30.0
BACK_IOU_DROP = 0.02

MIN_LOFT_MM = 0.02
JOIN_OVERLAP_MM = 0.0            # loft this far past z_b into the front piece (0 = DESIGN: end exactly on z_b)
TARGET_FACES = 12_000
BOUNDARY_WEIGHT = 1000.0

REFINE_BOUNDS = {"open_deg": 6.0, "lift_deg": 4.0, "tx_mm": 1.5, "ty_mm": 1.5, "tz_mm": 1.5}
REFINE_PRIOR = 0.004             # loss units at a parameter's bound (quadratic)
REFINE_TARGET_SAMPLES = 640      # ROI width in samples during the fit
REFINE_MAXFEV = 400
SHEET_TILE_W = 560


# ----------------------------------------------------------------------------- mesh utilities
def weld(V: np.ndarray, tol: float = WELD_MM) -> tuple[np.ndarray, np.ndarray]:
    """(unique positions, inverse index) after snapping positions to a ``tol`` grid (deterministic)."""
    key = np.round(np.asarray(V, np.float64) / tol).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return np.asarray(V, np.float64)[first], inv.ravel()


def face_components(FW: np.ndarray, n_vertices: int) -> np.ndarray:
    """Connected-component label per face (faces sharing a vertex index are connected)."""
    nf = len(FW)
    if nf == 0:
        return np.zeros(0, np.int64)
    rows = np.repeat(np.arange(nf), 3)
    cols = nf + FW.ravel()
    n = nf + n_vertices
    A = coo_matrix((np.ones(len(rows), np.int8), (rows, cols)), shape=(n, n))
    _, lab = connected_components(A, directed=False)
    # relabel by first occurrence so labels do not depend on scipy internals
    _, first, inv = np.unique(lab[:nf], return_index=True, return_inverse=True)
    order = np.argsort(np.argsort(first))
    return order[inv]


def edge_topology(F: np.ndarray) -> dict:
    """Directed-edge bookkeeping on a welded face array."""
    d = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    und = np.sort(d, axis=1)
    _, uinv, ucount = np.unique(und, axis=0, return_inverse=True, return_counts=True)
    _, dcount = np.unique(d, axis=0, return_counts=True)
    return {"boundary": int((ucount == 1).sum()), "nonmanifold": int((ucount > 2).sum()),
            "misoriented": int((dcount > 1).sum())}


def boundary_loops(F: np.ndarray) -> list[np.ndarray]:
    """Closed boundary loops as vertex sequences, following each face's winding (a boundary edge a->b of a
    face). A vertex met twice splits the walk into simple loops, so each loop can be fan-capped."""
    d = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    und = np.sort(d, axis=1)
    _, uinv, ucount = np.unique(und, axis=0, return_inverse=True, return_counts=True)
    bd = d[ucount[uinv.ravel()] == 1]
    if len(bd) == 0:
        return []
    order = np.lexsort((bd[:, 1], bd[:, 0]))
    bd = bd[order]
    nxt: dict[int, list[int]] = {}
    for a, b in bd.tolist():
        nxt.setdefault(a, []).append(b)
    loops = []
    for start in sorted(nxt):
        while nxt.get(start):
            path = [start]
            pos = {start: 0}
            cur = start
            while True:
                outs = nxt.get(cur)
                if not outs:
                    break                                   # open chain (should not happen on a boundary)
                b = outs.pop(0)
                if b in pos:                                # closed a simple loop
                    k = pos[b]
                    loop = path[k:]
                    if len(loop) >= 3:
                        loops.append(np.array(loop, np.int64))
                    for v in loop:
                        pos.pop(v, None)
                    path = path[:k + 1]
                    for i, v in enumerate(path):
                        pos[v] = i
                    cur = b
                    if b == start and len(path) == 1:
                        break
                    continue
                pos[b] = len(path)
                path.append(b)
                cur = b
    return loops


def orient_consistently(F: np.ndarray) -> tuple[np.ndarray, int]:
    """Flip faces so every manifold edge is used in opposite directions (BFS per component). Returns
    (F, number of flipped faces). Non-manifold edges are not traversed."""
    F = F.copy()
    nf = len(F)
    d = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    fid = np.tile(np.arange(nf), 3)
    und = np.sort(d, axis=1)
    _, uinv, ucount = np.unique(und, axis=0, return_inverse=True, return_counts=True)
    uinv = uinv.ravel()
    man = ucount[uinv] == 2
    ek = uinv[man]
    ef = fid[man]
    o = np.argsort(ek, kind="stable")
    ek, ef = ek[o], ef[o]
    pairs = ef.reshape(-1, 2)                                 # the two faces of each manifold edge
    adj: list[list[int]] = [[] for _ in range(nf)]
    for a, b in pairs.tolist():
        adj[a].append(b)
        adj[b].append(a)
    seen = np.zeros(nf, bool)
    flipped = 0

    def shares_same_direction(fa, fb):
        ea = {(F[fa, i], F[fa, (i + 1) % 3]) for i in range(3)}
        return any((F[fb, i], F[fb, (i + 1) % 3]) in ea for i in range(3))

    for s in range(nf):
        if seen[s]:
            continue
        seen[s] = True
        queue = [s]
        while queue:
            f = queue.pop()
            for g in adj[f]:
                if seen[g]:
                    continue
                if shares_same_direction(f, g):
                    F[g] = F[g, ::-1]
                    flipped += 1
                seen[g] = True
                queue.append(g)
    return F, flipped


def signed_volume(V: np.ndarray, F: np.ndarray) -> float:
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def orient_outward(V: np.ndarray, F: np.ndarray, CUV: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray | None, int]:
    """Flip every connected component whose signed volume is negative (closed, consistently oriented input),
    so all normals point outward. Returns (F, CUV, number of components flipped)."""
    F = np.array(F, copy=True)
    CUV = None if CUV is None else np.array(CUV, copy=True)
    lab = face_components(F, len(V))
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    vol = np.bincount(lab, weights=np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0)
    flipped = 0
    for k in np.nonzero(vol < 0)[0]:
        sel = lab == k
        F[sel] = F[sel][:, ::-1]
        if CUV is not None:
            CUV[sel] = CUV[sel][:, ::-1]
        flipped += 1
    return F, CUV, flipped


def decimate(V: np.ndarray, F: np.ndarray, target: int = TARGET_FACES) -> tuple[np.ndarray, np.ndarray, dict]:
    """Quadric decimation (boundary preserved), then degenerate / duplicate / non-manifold-edge removal
    and consistent orientation. Deterministic for identical input."""
    m = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(np.asarray(V, np.float64)),
                                  o3d.utility.Vector3iVector(np.asarray(F, np.int32)))
    if len(F) > target:
        m = m.simplify_quadric_decimation(target_number_of_triangles=int(target), boundary_weight=BOUNDARY_WEIGHT)
    m.remove_degenerate_triangles()
    m.remove_duplicated_triangles()
    m.remove_non_manifold_edges()
    m.remove_unreferenced_vertices()
    Vd = np.asarray(m.vertices, np.float64)
    Fd = np.asarray(m.triangles, np.int64)
    Fd, flipped = orient_consistently(Fd)
    return Vd, Fd, {"faces_in": int(len(F)), "faces_out": int(len(Fd)), "vertices_out": int(len(Vd)),
                    "orientation_flips": int(flipped), **edge_topology(Fd)}


# ----------------------------------------------------------------------------- UV transfer
def uv_islands(F_orig: np.ndarray, n_vertices: int) -> np.ndarray:
    """UV island per source face: faces sharing an ORIGINAL (unwelded) vertex share texture coordinates."""
    return face_components(F_orig, n_vertices)


def transfer_uv(src_V: np.ndarray, src_F: np.ndarray, src_UV: np.ndarray, V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, dict]:
    """Per-corner UVs (M,3,2) for mesh (V, F) from a textured source (src_V, src_F with per-vertex src_UV).

    Every vertex takes the UV of its closest source surface point. A face whose three corners landed on the
    UV island of its centroid's closest source face keeps those vertex UVs (shared, seam-free). A face that
    straddles a UV seam samples ONE island: each corner takes the UV of the nearest source vertex of the
    centroid's island (no extrapolation: an affine extension of a 0.1 mm source triangle over a 1 mm face
    lands outside the island and samples the atlas background)."""
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(np.ascontiguousarray(src_V, np.float32)),
                        o3d.core.Tensor(np.ascontiguousarray(src_F, np.uint32)))
    island = uv_islands(src_F, len(src_V))
    v_island_src = np.full(len(src_V), -1, np.int64)
    for k in range(3):
        v_island_src[src_F[:, k]] = island

    def closest(P):
        r = scene.compute_closest_points(o3d.core.Tensor(np.ascontiguousarray(P, np.float32)))
        return r["primitive_ids"].numpy().astype(np.int64), r["primitive_uvs"].numpy().astype(np.float64), r["points"].numpy()

    pid, bary, pts = closest(V)
    T = src_UV[src_F[pid]].astype(np.float64)
    vuv = (1 - bary[:, 0] - bary[:, 1])[:, None] * T[:, 0] + bary[:, 0, None] * T[:, 1] + bary[:, 1, None] * T[:, 2]
    v_island = island[pid]
    C = V[F].mean(axis=1)
    cid, cb, _ = closest(C)
    c_island = island[cid]
    same = (v_island[F] == c_island[:, None]).all(axis=1)
    CUV = vuv[F].copy()
    bad = np.nonzero(~same)[0]
    fallback = 0
    if len(bad):
        tree = cKDTree(src_V)
        corners = V[F[bad]].reshape(-1, 3)
        want = np.repeat(c_island[bad], 3)
        k = min(64, len(src_V))
        _, nn = tree.query(corners, k=k)
        nn = nn.reshape(len(corners), -1)
        ok = v_island_src[nn] == want[:, None]
        first = np.argmax(ok, axis=1)
        has = ok[np.arange(len(corners)), first]
        uv = src_UV[nn[np.arange(len(corners)), first]].astype(np.float64)
        miss = np.nonzero(~has)[0]
        if len(miss):                                  # far from the island: flat colour of the centroid point
            Tc = src_UV[src_F[cid[bad]]].astype(np.float64)
            b = cb[bad]
            cuv = (1 - b[:, 0] - b[:, 1])[:, None] * Tc[:, 0] + b[:, 0, None] * Tc[:, 1] + b[:, 1, None] * Tc[:, 2]
            uv[miss] = np.repeat(cuv, 3, axis=0)[miss]
            fallback = int(len(miss))
        CUV[bad] = uv.reshape(-1, 3, 2)
    dist = np.linalg.norm(pts - V, axis=1)
    return CUV, {"seam_faces": int(len(bad)), "seam_share": float(len(bad) / max(len(F), 1)),
                 "seam_corner_fallbacks": fallback, "islands": int(island.max() + 1) if len(island) else 0,
                 "vertex_to_source_mm_p95": float(np.percentile(dist, 95)) if len(dist) else 0.0,
                 "vertex_to_source_mm_max": float(dist.max()) if len(dist) else 0.0}


def split_by_uv(V: np.ndarray, F: np.ndarray, CUV: np.ndarray, decimals: int = 6) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-corner UVs -> per-vertex UVs by duplicating vertices whose corners disagree (glTF layout)."""
    corner_v = F.ravel()
    corner_uv = np.round(CUV.reshape(-1, 2), decimals)
    key = np.column_stack([corner_v.astype(np.float64), corner_uv])
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    V2 = V[corner_v[first]]
    UV2 = CUV.reshape(-1, 2)[first]
    return V2, inv.reshape(-1, 3).astype(np.int32), UV2.astype(np.float32)


# ----------------------------------------------------------------------------- endpiece / cut
def endpiece_back_face(V: np.ndarray, front_z: float, sign: float) -> dict:
    """z of the endpiece back face on one side (see the module docstring, step 1)."""
    side = V[sign * V[:, 0] > 0]
    depth = front_z - float(side[:, 2].min())
    dz = front_z - side[:, 2]
    ref = (dz > REF_BAND[0] * depth) & (dz < REF_BAND[1] * depth)
    if ref.sum() < 10:
        return {"z": None, "reason": "no temple reference band"}
    x_lat = float(np.abs(side[ref, 0]).min()) - LATERAL_MARGIN_MM
    lat = side[np.abs(side[:, 0]) >= x_lat]
    order = np.argsort(lat[:, 2], kind="stable")
    zs = lat[order, 2]

    def stats(z):
        i0, i1 = np.searchsorted(zs, z - SLAB_HALF_MM), np.searchsorted(zs, z + SLAB_HALF_MM)
        if i1 - i0 < 3:
            return None
        q = lat[order[i0:i1]]
        return float(np.ptp(q[:, 1])), float(np.ptp(q[:, 0]))

    grid = np.arange(front_z - REF_BAND[1] * depth, front_z - REF_BAND[0] * depth, SLAB_STEP_MM)
    st = [s for s in (stats(z) for z in grid) if s is not None]
    if not st:
        return {"z": None, "reason": "empty reference band"}
    h_ref = max(s[0] for s in st)
    w_ref = max(s[1] for s in st)
    h_th, w_th = H_FACTOR * h_ref + H_PAD_MM, W_FACTOR * w_ref + W_PAD_MM
    n = int(math.ceil(REF_BAND[0] * depth / SLAB_STEP_MM))
    for k in range(n + 1):
        z = front_z - REF_BAND[0] * depth + k * SLAB_STEP_MM
        s = stats(z)
        if s is not None and (s[0] > h_th or s[1] > w_th):
            return {"z": float(z), "dz": float(front_z - z), "h_ref_mm": h_ref, "w_ref_mm": w_ref,
                    "h_threshold_mm": h_th, "w_threshold_mm": w_th, "h_at_mm": s[0], "w_at_mm": s[1], "x_lat_mm": x_lat}
    return {"z": None, "reason": "no front-piece slab found", "x_lat_mm": x_lat}


def section_perimeter(V: np.ndarray, F: np.ndarray, z: float, x_lat: float) -> float:
    """Length (mm) of the plane z = const's cross-section of the mesh within the lateral bands |x| >= x_lat."""
    d = V[:, 2] - z
    d = np.where(np.abs(d) < 1e-9, 1e-9, d)                  # a vertex on the plane counts as in front of it
    df = d[F]
    sel = (df.min(axis=1) < 0) & (df.max(axis=1) > 0)
    Fs = F[sel]
    if not len(Fs):
        return 0.0
    cx = V[Fs, 0].mean(axis=1)
    Fs = Fs[np.abs(cx) >= x_lat]
    if not len(Fs):
        return 0.0
    P = V[Fs]
    dd = d[Fs]
    pts = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        da, db = dd[:, a], dd[:, b]
        cr = (da < 0) != (db < 0)
        t = np.where(cr, da / np.where(cr, da - db, 1.0), 0.0)
        pts.append((cr, P[:, a] + t[:, None] * (P[:, b] - P[:, a])))
    crs = np.stack([c for c, _ in pts], 1)
    Q = np.stack([q for _, q in pts], 1)
    two = crs.sum(axis=1) == 2
    idx = np.argsort(~crs[two], axis=1, kind="stable")[:, :2]
    Qt = Q[two]
    A = Qt[np.arange(len(Qt)), idx[:, 0]]
    B = Qt[np.arange(len(Qt)), idx[:, 1]]
    return float(np.linalg.norm(A - B, axis=1).sum())


def choose_split(V: np.ndarray, F: np.ndarray, z_front: float, x_lat: float) -> tuple[float, dict]:
    """The S5 split plane (arm | fixed donor block): the plane with the SIMPLEST lateral cross-section (smallest
    perimeter over both sides) within ``SPLIT_SEARCH_MM`` behind ``z_front`` = endpiece back face - 1 mm; the
    front-most plane within ``SPLIT_TOL`` of that minimum. A relief feature at the temple root (a logo, a hinge
    knuckle) adds perimeter, so the split avoids cutting through it; everything in front of the split comes from
    the generator unrefined (S5 donor block), so the feature stays whole."""
    zs = np.arange(0.0, SPLIT_SEARCH_MM + 1e-9, SPLIT_STEP_MM)
    per = np.array([section_perimeter(V, F, z_front - dz, x_lat) for dz in zs])
    ok = per > 0
    if not ok.any():
        return float(z_front), {"rule": "no lateral section found", "behind_endpiece_mm": CUT_BEHIND_MM}
    m = float(per[ok].min())
    k = int(np.nonzero(ok & (per <= (1.0 + SPLIT_TOL) * m))[0][0])
    return float(z_front - zs[k]), {"searched_mm": SPLIT_SEARCH_MM, "step_mm": SPLIT_STEP_MM, "x_lat_mm": x_lat,
                                    "perimeter_at_endpiece_mm": round(float(per[0]), 2),
                                    "perimeter_min_mm": round(m, 2), "chosen_behind_mm": float(zs[k]),
                                    "chosen_perimeter_mm": round(float(per[k]), 2),
                                    "profile_mm": [[float(z), round(float(p), 1)] for z, p in zip(zs, per)]}


def lateral_sections(zmin: np.ndarray, zmax: np.ndarray, cx: np.ndarray, FW: np.ndarray, n_welded: int, cut_z: float,
              sign: float, x_lat: float) -> int:
    """Number of separate cross-sections the plane z = cut_z cuts on one side's lateral band: connected
    groups of faces straddling the plane (per-face z range ``zmin``/``zmax``, centroid x ``cx``)."""
    sel = (zmin < cut_z) & (zmax >= cut_z) & (sign * cx > 0) & (np.abs(cx) >= x_lat)
    if not sel.any():
        return 0
    lab = face_components(FW[sel], n_welded)
    return int(lab.max() + 1)


# ----------------------------------------------------------------------------- rigid pose
def rigid(params: np.ndarray, sign: float, pivot: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(R, t) of P' = R (P - pivot) + pivot + t. params = (open_deg, lift_deg, tx, ty, tz).

    open > 0 swings the temple tip outward (away from x = 0) about the vertical axis through the pivot;
    lift > 0 raises the tip about the lateral (x) axis through the pivot."""
    op, li = math.radians(params[0]), math.radians(params[1])
    a = -sign * op                                    # rotation about +Y
    cy, sy = math.cos(a), math.sin(a)
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    cl, sl = math.cos(li), math.sin(li)              # about +X: (0, 0, -L) -> (0, L sin(lift), -L cos(lift))
    Rx = np.array([[1, 0, 0], [0, cl, -sl], [0, sl, cl]])
    R = Rx @ Ry
    return R, np.asarray(params[2:5], float)


def apply_rigid(V: np.ndarray, params: np.ndarray, sign: float, pivot: np.ndarray) -> np.ndarray:
    R, t = rigid(params, sign, pivot)
    return (np.asarray(V, float) - pivot) @ R.T + pivot + t


# ----------------------------------------------------------------------------- photo evidence
class ViewData:
    """One photo: camera, matte, static (front piece) render, fitting grid."""

    def __init__(self, view: str, cam: Camera, fg: np.ndarray, frame: NormFrame, static_scene: raster.RasterScene,
                 temple_meshes: list[np.ndarray], target_samples: int = REFINE_TARGET_SAMPLES):
        self.view, self.cam, self.fg, self.frame = view, cam, fg, frame
        self.static_scene = static_scene
        H, W = fg.shape
        ys, xs = np.nonzero(fg)
        pts = [project(static_scene.hull, cam)] + [project_mm(v, cam, frame) for v in temple_meshes]
        P = np.vstack(pts)
        x0, x1 = min(xs.min(), P[:, 0].min()), max(xs.max() + 1, P[:, 0].max() + 1)
        y0, y1 = min(ys.min(), P[:, 1].min()), max(ys.max() + 1, P[:, 1].max() + 1)
        m = 0.05 * (x1 - x0)
        self.roi = (int(max(0, math.floor(x0 - m))), int(max(0, math.floor(y0 - m))),
                    int(min(W, math.ceil(x1 + m))), int(min(H, math.ceil(y1 + m))))
        self.stride = max(1, int(round((self.roi[2] - self.roi[0]) / target_samples)))
        self.ref = raster.downsample_mask(fg, self.stride, self.roi)
        self.static = static_scene.render(cam, fg.shape, self.stride, self.roi)["mask"]

    def render(self, V: np.ndarray, F: np.ndarray, stride: int | None = None) -> np.ndarray:
        s = self.stride if stride is None else stride
        return raster.RasterScene(V, F, self.frame).render(self.cam, self.fg.shape, s, self.roi)["mask"]

    def temple_iou(self, masks: list[np.ndarray], static: np.ndarray | None = None, ref: np.ndarray | None = None) -> float:
        """Soft IoU of (union of masks) vs the matte on the samples the front piece does not cover."""
        static = self.static if static is None else static
        ref = self.ref if ref is None else ref
        m = np.zeros_like(static)
        for k in masks:
            m |= k
        E = ~static
        r = ref[E]
        mm = m[E]
        inter = float(r[mm].sum())
        union = float(mm.sum()) + float(r[~mm].sum())
        return inter / max(union, 1e-9)


def refine_temple(terms: list[tuple[ViewData, np.ndarray]], V: np.ndarray, F: np.ndarray, sign: float,
                  pivot: np.ndarray) -> dict:
    """Bounded rigid refinement of one temple. ``terms`` = [(view, other temple's mask on that view's grid)];
    loss = sum over views of (1 - temple-region IoU) + a quadratic prior toward the donor pose."""
    names = list(REFINE_BOUNDS)
    b = np.array([REFINE_BOUNDS[k] for k in names])
    cache: dict[tuple, tuple[float, list[float]]] = {}

    def evaluate(x):
        key = tuple(np.round(x, 6))
        if key not in cache:
            Vt = apply_rigid(V, x, sign, pivot)
            ious = [vd.temple_iou([vd.render(Vt, F), other]) for vd, other in terms]
            val = float(sum(1.0 - i for i in ious)) + REFINE_PRIOR * float(((x / b) ** 2).sum())
            cache[key] = (val, ious)
        return cache[key]

    x0 = np.zeros(len(names))
    l0, i0 = evaluate(x0)
    res = optimize.minimize(lambda x: evaluate(x)[0], x0, method="Powell", bounds=[(-v, v) for v in b],
                            options={"maxfev": REFINE_MAXFEV, "xtol": 0.02, "ftol": 1e-6})
    x = np.clip(res.x, -b, b)
    l1, i1 = evaluate(x)
    if l1 > l0:                                      # never accept a worse pose
        x, l1, i1 = x0, l0, i0
    return {"params": {k: float(v) for k, v in zip(names, x)}, "x": x, "loss_donor": l0, "loss_refined": l1,
            "fit_iou_donor": {vd.view: v for (vd, _), v in zip(terms, i0)},
            "fit_iou_refined": {vd.view: v for (vd, _), v in zip(terms, i1)},
            "evaluations": len(cache), "at_bound": [k for k, v, bb in zip(names, x, b) if abs(v) >= 0.98 * bb]}


def image_direction(cam: Camera, frame: NormFrame, tip_mm: np.ndarray) -> np.ndarray | None:
    """Unit image direction of model -z at ``tip_mm`` (the direction a longer temple grows in the photo)."""
    p0 = project_mm(np.asarray(tip_mm, float)[None], cam, frame)[0]
    p1 = project_mm((np.asarray(tip_mm, float) + np.array([0.0, 0.0, -10.0]))[None], cam, frame)[0]
    d = p1 - p0
    n = float(np.linalg.norm(d))
    return d / n if n > 1e-6 else None


def extreme_pixel(mask: np.ndarray, d: np.ndarray, x0: int = 0, y0: int = 0) -> tuple[float, float] | None:
    """Native pixel of ``mask`` (offset by the crop origin) furthest along the image direction d."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    u, v = xs.astype(float) + x0, ys.astype(float) + y0
    i = int(np.argmax(u * d[0] + v * d[1]))
    return float(u[i]), float(v[i])


def backproject_to_x(cam: Camera, frame: NormFrame, uv: tuple[float, float], x_mm: float) -> np.ndarray | None:
    """Model point (mm) where the exact camera ray through pixel uv meets the plane x = x_mm."""
    O, D = raster.camera_rays(cam, np.array([uv[0]]), np.array([uv[1]]), 1.0)
    P0, P1 = frame.to_mm(O[0][None])[0], frame.to_mm((O[0] + D[0])[None])[0]
    dx = P1[0] - P0[0]
    if abs(dx) < 1e-9:
        return None
    return P0 + (x_mm - P0[0]) / dx * (P1 - P0)


def length_readings(views: dict, native_masks: dict, Vref: dict, z_b: dict, frame: NormFrame) -> list[dict]:
    """One side-photo length reading per side view. The photo's extreme matte pixel along the image direction
    of -z belongs to the near temple unless the far temple's own model tip pixel is more than
    max(3 px, 1 mm) nearer to it (a camera a few degrees off 90 deg can make the FAR tip the extreme one);
    it is back-projected onto that temple's tip plane x = x_tip. The model is read the same way from that
    temple's own render."""
    out = []
    for vname in ("left", "right"):
        vd = views[vname]
        nm = native_masks[vname]
        x0, y0, x1, y1 = vd.roi
        tips, pix = {}, {}
        for q, Vq in Vref.items():
            tips[q] = Vq[int(np.argmin(Vq[:, 2]))]
        near_q = min(Vref, key=lambda q: -SIGN[q] * {"left": 1, "right": -1}[vname])   # the temple nearest the camera
        d = image_direction(vd.cam, frame, tips[near_q])
        if d is None:
            continue
        for q in Vref:
            pix[q] = extreme_pixel(nm["refined"][q], d, x0, y0)
        full = np.zeros(vd.fg.shape, bool)
        rm = nm["static"].copy()
        for q in Vref:
            rm |= nm["refined"][q]
        full[y0:y1, x0:x1] = rm
        near = matte_near(vd.fg, full, int(0.03 * (x1 - x0)))
        pp = extreme_pixel(near, d)
        cands = [q for q in Vref if pix[q] is not None]
        if pp is None or not cands:
            continue
        dist = {c: math.hypot(pix[c][0] - pp[0], pix[c][1] - pp[1]) for c in cands}
        ppm = cameras.px_per_mm_at(vd.cam, frame, tips[near_q])
        q = near_q if near_q in dist else cands[0]
        for c in cands:      # the far temple takes the reading only when its tip is clearly nearer (> 1 mm)
            if c != q and dist[c] < dist[q] - max(3.0, TIP_ASSIGN_MARGIN_MM * ppm):
                q = c
        P = backproject_to_x(vd.cam, frame, pp, float(tips[q][0]))
        M = backproject_to_x(vd.cam, frame, pix[q], float(tips[q][0]))
        if P is None or M is None:
            continue
        out.append({"view": vname, "temple": q, "near_temple": near_q, "tip_distance_px": dist, "photo_pixel": list(pp), "model_pixel": list(pix[q]),
                    "other_tip_pixel": {c: list(pix[c]) for c in cands if c != q},
                    "photo_length_mm": float(z_b[q] - P[2]), "model_read_mm": float(z_b[q] - M[2]),
                    "length_3d_mm": float(z_b[q] - tips[q][2]), "tip_x_mm": float(tips[q][0])})
    return out


def matte_near(fg: np.ndarray, render_mask_native: np.ndarray, dilate_px: int) -> np.ndarray:
    """Matte components that touch the (dilated) render: drops far-away junk before reading extremes."""
    lab, n = ndimage.label(fg)
    if n == 0:
        return fg
    k = max(1, dilate_px)
    near = cv2.dilate(render_mask_native.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))).astype(bool)
    keep = np.unique(lab[near & fg])
    keep = keep[keep > 0]
    return np.isin(lab, keep)


# ----------------------------------------------------------------------------- closing the temple
def close_temple(V: np.ndarray, F: np.ndarray, loops_cut: list[np.ndarray], loops_hole: list[np.ndarray],
                 z_join: float, CUV: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray, list[dict]]:
    """Loft each cut loop straight forward to z_join and fan-cap it; fan-cap every other hole in place.

    Returns (V, F, CUV, face_kind, loop_info); face_kind: 0 donor, 1 loft wall, 2 cut cap, 3 hole cap.
    New faces use each boundary edge in the opposite direction, so the result is closed and consistently
    oriented when the input is. With per-corner donor UVs ``CUV`` the wall faces copy the UVs of the donor
    face that owns their boundary edge (seam-safe); a cap is one texel (the loop vertex nearest its centre)."""
    V0 = np.asarray(V, float)
    blocks = [V0]
    n = len(V0)
    newF, kind, info, newUV = [], [], [], []
    edge_uv = {}
    if CUV is not None:
        for k in range(3):
            a, b = F[:, k], F[:, (k + 1) % 3]
            for fa, fb, ua, ub in zip(a.tolist(), b.tolist(), CUV[:, k].tolist(), CUV[:, (k + 1) % 3].tolist()):
                edge_uv[(fa, fb)] = (ua, ub)

    vertex_uv = {}
    if CUV is not None:
        for k in range(3):
            for v_, uv_ in zip(F[:, k].tolist(), CUV[:, k].tolist()):
                vertex_uv.setdefault(v_, uv_)
    for loop in loops_cut:
        P = V0[loop]
        Pl = P.copy()
        Pl[:, 2] = np.maximum(z_join, P[:, 2] + MIN_LOFT_MM)     # never a zero-length wall (it would weld shut)
        ids = np.arange(n, n + len(loop))
        blocks.append(Pl)
        n += len(loop)
        a, b = loop, np.roll(loop, -1)
        ap, bp = ids, np.roll(ids, -1)
        newF.append(np.column_stack([b, a, ap]))
        newF.append(np.column_stack([b, ap, bp]))
        kind += [1] * (2 * len(loop))
        if CUV is not None:
            uva = np.array([edge_uv[(int(x), int(y))][0] for x, y in zip(a, b)])
            uvb = np.array([edge_uv[(int(x), int(y))][1] for x, y in zip(a, b)])
            newUV.append(np.stack([uvb, uva, uva], axis=1))
            newUV.append(np.stack([uvb, uva, uvb], axis=1))
        c = Pl.mean(axis=0)
        blocks.append(c[None])
        ci = n
        n += 1
        newF.append(np.column_stack([np.full(len(loop), ci), bp, ap]))
        kind += [2] * len(loop)
        if CUV is not None:
            near = int(loop[int(np.argmin(np.linalg.norm(P - P.mean(axis=0), axis=1)))])
            newUV.append(np.tile(np.asarray(vertex_uv[near], float), (len(loop), 3, 1)))
        info.append({"vertices": int(len(loop)), "copy_start": int(ids[0]), "loft_mm_max": float((z_join - P[:, 2]).max()),
                     "loft_mm_min": float((z_join - P[:, 2]).min())})
    for loop in loops_hole:
        P = V0[loop]
        blocks.append(P.mean(axis=0)[None])
        ci = n
        n += 1
        newF.append(np.column_stack([np.full(len(loop), ci), np.roll(loop, -1), loop]))
        kind += [3] * len(loop)
        if CUV is not None:
            near = int(loop[int(np.argmin(np.linalg.norm(P - P.mean(axis=0), axis=1)))])
            newUV.append(np.tile(np.asarray(vertex_uv[near], float), (len(loop), 3, 1)))
    Vout = np.vstack(blocks)
    Fout = np.vstack([F] + newF) if newF else np.asarray(F)
    kinds = np.concatenate([np.zeros(len(F), np.int8), np.array(kind, np.int8)])
    UVout = None
    if CUV is not None:
        UVout = np.concatenate([CUV] + newUV, axis=0) if newUV else CUV
    return Vout, Fout.astype(np.int64), UVout, kinds, info


# ----------------------------------------------------------------------------- stage
def _load_mattes(product: str, run: str) -> dict[str, np.ndarray]:
    a = np.load(stage_dir(run, product, "s0_intake").root / "arrays.npz")
    return {v: a[f"fg_{v}"] for v in ("front", "back", "left", "right")}


def extract_donors(gen: generator.Generator) -> dict:
    """Steps 1-3: endpiece back faces, cut plane, per-side donor components (full resolution)."""
    V = gen.V.astype(np.float64)
    F = gen.F.astype(np.int64)
    fz = float(gen.result["front_z_mm"])
    ends = {s: endpiece_back_face(V, fz, SIGN[s]) for s in SIDES}
    flags = []
    for s in SIDES:
        if ends[s]["z"] is None:
            ends[s]["z"] = fz - float(gen.result["front_depth_mm"])
            ends[s]["fallback"] = "S1 front_depth_mm"
            flags.append(f"{s}:endpiece_rule_failed")
    z_b = min(ends[s]["z"] for s in SIDES)
    x_lat = min(ends[s].get("x_lat_mm", 40.0) for s in SIDES)
    cut, split = choose_split(V, F, z_b - CUT_BEHIND_MM, x_lat)
    cut_rule = (f"the simplest lateral cross-section (smallest perimeter, within {100 * SPLIT_TOL:.0f} % of the minimum, "
                f"front-most) within {SPLIT_SEARCH_MM:g} mm behind (min over sides of the endpiece back face - 1 mm)")
    Wpos, winv = weld(V)
    FW = winv[F]
    zf = V[:, 2][F]
    zmin, zmax = zf.min(axis=1), zf.max(axis=1)
    cx = V[:, 0][F].mean(axis=1)
    fb = np.nonzero(zmax < cut)[0]
    lab = face_components(FW[fb], len(Wpos))
    sizes = np.bincount(lab)
    C = V[F[fb]].mean(axis=1)
    donors = {}
    for s in SIDES:
        sg = SIGN[s]
        comp_x = np.bincount(lab, weights=C[:, 0]) / np.maximum(sizes, 1)
        cand = [c for c in range(len(sizes)) if sg * comp_x[c] > 0]
        if not cand:
            donors[s] = None
            continue
        best = max(cand, key=lambda c: (sizes[c], -c))
        faces = fb[lab == best]
        others = [int(sizes[c]) for c in cand if c != best]
        donors[s] = {"faces": faces, "fragments": {"count": len(others), "faces": int(sum(others)),
                                                    "largest": int(max(others)) if others else 0},
                     "lateral_sections": lateral_sections(zmin, zmax, cx, FW, len(Wpos), cut, sg, ends[s].get("x_lat_mm", 40.0))}
    return {"V": V, "F": F, "Wpos": Wpos, "winv": winv, "front_z": fz, "ends": ends,
            "z_b": {s: float(ends[s]["z"]) for s in SIDES}, "cut_z": float(cut), "cut_rule": cut_rule,
            "split": split, "donors": donors, "flags": flags}


def prepare_temple(ex: dict, s: str, UV: np.ndarray | None, target: int = TARGET_FACES,
                   tex: np.ndarray | None = None) -> dict:
    """Step 4 for one side: welded donor -> decimated, repaired, UV-transferred (donor pose)."""
    V, F, winv, Wpos = ex["V"], ex["F"], ex["winv"], ex["Wpos"]
    faces = ex["donors"][s]["faces"]
    Fo = F[faces]                                  # original (unwelded) indices
    FWc = winv[Fo]
    used = np.unique(FWc)
    remap = np.full(len(Wpos), -1, np.int64)
    remap[used] = np.arange(len(used))
    Vw, Fw = Wpos[used], remap[FWc]
    topo_full = edge_topology(Fw)
    lc, lh = classify_loops(Vw, Fw, ex["cut_z"], FULL_LOOP_TOL_MM)
    Vd, Fd, dinfo = decimate(Vw, Fw, target)
    out = {"V": Vd, "F": Fd, "decimation": dinfo, "full_topology": topo_full, "full_faces": int(len(Fw)),
           "crossings": count_crossings(lc), "cut_loops_simple": len(lc), "full_holes": len(lh),
           "cut_loop_sizes_mm": [float(np.ptp(Vw[lp, 0]) + np.ptp(Vw[lp, 1])) / 2 for lp in lc]}
    if UV is not None:
        uo = np.unique(Fo)
        rm = np.full(len(V), -1, np.int64)
        rm[uo] = np.arange(len(uo))
        CUV, uinfo = transfer_uv(V[uo], rm[Fo], UV[uo].astype(np.float64), Vd, Fd)
        out["CUV"] = CUV
        out["uv"] = uinfo
        if tex is not None:
            out["uv"]["colour_check"] = uv_colour_check(V[uo], rm[Fo], UV[uo].astype(np.float64), Vd, Fd, CUV, tex)
    return out


def static_faces(gen: generator.Generator, temple_meshes: list[tuple[np.ndarray, np.ndarray]], cut_z: float,
                 tol_mm: float = 0.6) -> np.ndarray:
    """Decimated generator faces that stay fixed (front piece, pads): every face except those behind the cut
    plane whose centroid lies within ``tol_mm`` of a donor temple surface."""
    Cd = gen.Vd[gen.Fd].mean(axis=1).astype(np.float64)
    is_temple = np.zeros(len(gen.Fd), bool)
    cand = np.nonzero(Cd[:, 2] < cut_z)[0]
    for V, F in temple_meshes:
        sc = o3d.t.geometry.RaycastingScene()
        sc.add_triangles(o3d.core.Tensor(np.ascontiguousarray(V, np.float32)), o3d.core.Tensor(np.ascontiguousarray(F, np.uint32)))
        if len(cand):
            d = sc.compute_distance(o3d.core.Tensor(np.ascontiguousarray(Cd[cand], np.float32))).numpy()
            is_temple[cand[d <= tol_mm]] = True
    return gen.Fd[~is_temple]


def uv_colour_check(src_V, src_F, src_UV, V, F, CUV, tex) -> dict:
    """Base colour at each face centroid through the transferred UVs vs the donor's own colour at the
    centroid's closest source point (0..255 RGB distance)."""
    sc = o3d.t.geometry.RaycastingScene()
    sc.add_triangles(o3d.core.Tensor(np.ascontiguousarray(src_V, np.float32)), o3d.core.Tensor(np.ascontiguousarray(src_F, np.uint32)))
    C = V[F].mean(axis=1)
    r = sc.compute_closest_points(o3d.core.Tensor(np.ascontiguousarray(C, np.float32)))
    pid = r["primitive_ids"].numpy().astype(np.int64)
    b = r["primitive_uvs"].numpy().astype(np.float64)
    ref = generator.sample_texture(tex, generator.interpolate_uv(src_UV, src_F, pid, b))
    got = generator.sample_texture(tex, CUV.mean(axis=1))
    d = np.linalg.norm(ref.astype(np.float64) - got, axis=1)
    return {"rgb_error_mean": float(d.mean()), "rgb_error_p95": float(np.percentile(d, 95)),
            "share_error_gt_40": float((d > 40).mean())}


def count_crossings(loops: list[np.ndarray]) -> int:
    """Separate cross-sections among the cut loops: simple loops that share a vertex (a pinched boundary)
    are one crossing."""
    parent = list(range(len(loops)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[int, int] = {}
    for i, lp in enumerate(loops):
        for v in lp.tolist():
            if v in owner:
                a, b = find(i), find(owner[v])
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[v] = i
    return len({find(i) for i in range(len(loops))})


def classify_loops(V: np.ndarray, F: np.ndarray, cut_z: float, tol: float) -> tuple[list, list]:
    loops = boundary_loops(F)
    cut, holes = [], []
    for lp in loops:
        z = V[lp, 2]
        on_plane = abs(float(np.median(z)) - cut_z) <= min(tol, CUT_LOOP_MEDIAN_MM)   # a hole near the plane is not a cut
        (cut if (abs(float(z.max()) - cut_z) <= tol and float(z.min()) >= cut_z - 4 * tol and on_plane) else holes).append(lp)
    return cut, holes


def run(product: str, run: str = "m1", force: bool = False, log=print) -> dict:
    sd = stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return json.loads(sd.result_path.read_text())
    t0 = time.perf_counter()
    timings = {}
    gen = generator.load(product, run)
    frame, cams, s3 = cameras.load_cameras(product, run)
    mattes = _load_mattes(product, run)
    s0 = json.loads((stage_dir(run, product, "s0_intake").result_path).read_text())

    t = time.perf_counter()
    ex = extract_donors(gen)
    timings["extract"] = time.perf_counter() - t
    flags = list(ex["flags"])
    cut_z = ex["cut_z"]
    log(f"[{product}] z_b R {ex['z_b']['R']:.2f} L {ex['z_b']['L']:.2f}  cut_z {cut_z:.2f} ({ex['front_z'] - cut_z:.2f} mm behind the front)")

    t = time.perf_counter()
    temples = {}
    tex = gen.texture("basecolor")
    for s in SIDES:
        if ex["donors"][s] is None:
            continue
        temples[s] = prepare_temple(ex, s, gen.UV, tex=tex)
        log(f"[{product}] {s}: donor {temples[s]['full_faces']} faces -> {temples[s]['decimation']['faces_out']}"
            f"  topo {temples[s]['decimation']}")
    timings["prepare"] = time.perf_counter() - t

    # static front piece for rendering: decimated generator faces that are not donor temple faces
    t = time.perf_counter()
    static_F = static_faces(gen, [(tp["V"], tp["F"]) for tp in temples.values()], cut_z)
    static_scene = raster.get_scene(gen.Vd, static_F, frame)

    pivots = {}
    for s, tp in temples.items():
        tol = max(0.5, 2.5 * _median_edge(tp["V"], tp["F"]))
        lc, lh = classify_loops(tp["V"], tp["F"], cut_z, tol)
        tp["loops_cut"], tp["loops_hole"] = lc, lh
        if lc:
            P = np.vstack([tp["V"][lp] for lp in lc])
            pivots[s] = np.array([P[:, 0].mean(), P[:, 1].mean(), cut_z])
        else:
            P = tp["V"][tp["V"][:, 2] > cut_z - 2.0]
            pivots[s] = np.array([P[:, 0].mean(), P[:, 1].mean(), cut_z])
            flags.append(f"{s}:no_cut_loop")

    views = {}
    for v in ("left", "right", "back"):
        views[v] = ViewData(v, cams[v], mattes[v], frame, static_scene, [tp["V"] for tp in temples.values()])

    # refinement: R on left.jpg (other temple at the donor pose), then L on right.jpg (R refined)
    params = {s: np.zeros(5) for s in temples}
    refine = {}
    for s in SIDES:
        if s not in temples:
            continue
        terms = []
        for vname in (NEAR_VIEW[s], "back"):
            vd = views[vname]
            other = np.zeros_like(vd.static)
            for q in temples:
                if q != s:
                    other |= vd.render(apply_rigid(temples[q]["V"], params[q], SIGN[q], pivots[q]), temples[q]["F"])
            terms.append((vd, other))
        rf = refine_temple(terms, temples[s]["V"], temples[s]["F"], SIGN[s], pivots[s])
        params[s] = rf["x"]
        refine[s] = rf
        log(f"[{product}] {s} refine on {NEAR_VIEW[s]}+back: {rf['params']}  loss {rf['loss_donor']:.4f} -> {rf['loss_refined']:.4f}"
            f" ({rf['evaluations']} evals)")
    timings["refine"] = time.perf_counter() - t

    # measurement at native resolution
    t = time.perf_counter()
    Vref = {s: apply_rigid(temples[s]["V"], params[s], SIGN[s], pivots[s]) for s in temples}
    per_side, arrays = {}, {}
    native_masks = {}
    for vname in ("left", "right", "back"):
        vd = views[vname]
        st1 = static_scene.render(vd.cam, vd.fg.shape, 1, vd.roi)["mask"]
        ref1 = vd.fg[vd.roi[1]:vd.roi[3], vd.roi[0]:vd.roi[2]].astype(np.float32)
        don = [vd.render(temples[s]["V"], temples[s]["F"], 1) for s in temples]
        rfd = [vd.render(Vref[s], temples[s]["F"], 1) for s in temples]
        native_masks[vname] = {"static": st1, "donor": dict(zip(temples, don)), "refined": dict(zip(temples, rfd)),
                               "iou_donor": vd.temple_iou(don, st1, ref1), "iou_refined": vd.temple_iou(rfd, st1, ref1)}
    readings = length_readings(views, native_masks, Vref, ex["z_b"], frame)
    for s in SIDES:
        if s not in temples:
            per_side[s] = {"accepted": False, "reason": "no donor component", "faces": 0}
            flags.append(f"{s}:no_donor")
            continue
        tp = temples[s]
        sg = SIGN[s]
        vname = NEAR_VIEW[s]
        vd = views[vname]
        Vt = Vref[s]
        z_b = ex["z_b"][s]
        z_join = cut_z + LOFT_INTO_DONOR_MM + JOIN_OVERLAP_MM     # 1 mm into the fixed donor block in front
        # length: model 3D vs the side-photo reading assigned to this temple (its near view first)
        tip = Vt[int(np.argmin(Vt[:, 2]))]
        len_3d = z_b - float(tip[2])
        nm = native_masks[vname]
        mine = sorted([r for r in readings if r["temple"] == s], key=lambda r: (r["view"] != vname, r["view"]))
        symmetric = False
        if not mine:          # both photo extremes belong to the other temple: use its reading (same product)
            mine = sorted(readings, key=lambda r: (r["view"] != vname, r["view"]))
            symmetric = bool(mine)
            if symmetric:
                flags.append(f"{s}:length_from_symmetric_reading")
        rd = mine[0] if mine else None
        photo_len = rd["photo_length_mm"] if rd else None
        model_read = rd["model_read_mm"] if rd else None
        mismatch = abs(len_3d - photo_len) / photo_len if photo_len else None
        border = bool(rd) and "border_contact" in s0["views"][rd["view"]].get("flags", [])
        # gap: loft length + in-plane shift of the cut end
        Vd0 = tp["V"]
        loops = tp["loops_cut"]
        if loops:
            idx = np.concatenate(loops)
            shift_xy = np.linalg.norm(Vt[idx, :2] - Vd0[idx, :2], axis=1)
            loft = np.maximum(z_join - Vt[idx, 2], 0.0)
            gap = float(np.sqrt(shift_xy ** 2 + loft ** 2).max())
        else:
            gap = float("inf")
        # fold: top-view inward angle of hinge -> tip, and the back view
        hinge = apply_rigid(pivots[s][None], params[s], sg, pivots[s])[0]
        inward = math.degrees(math.atan2(sg * (hinge[0] - tip[0]), hinge[2] - tip[2]))
        back = native_masks["back"]
        back_drop = back["iou_donor"] - back["iou_refined"]
        folded = inward > FOLD_MAX_DEG or (back_drop > BACK_IOU_DROP and params[s][0] < 0)
        # close (loft + caps), outward orientation, per-vertex UVs split at seams
        Vc, Fc, CUVc, kinds, loop_info = close_temple(Vt, tp["F"], tp["loops_cut"], tp["loops_hole"], z_join,
                                                      tp.get("CUV"))
        Fc, CUVc, flipped_components = orient_outward(Vc, Fc, CUVc)
        vol = signed_volume(Vc, Fc)
        topo = edge_topology(Fc)
        closed = topo["boundary"] == 0 and topo["nonmanifold"] == 0 and topo["misoriented"] == 0
        if CUVc is not None:
            Vo, Fo, UVo = split_by_uv(Vc, Fc, CUVc)
        else:
            Vo, Fo, UVo = Vc, Fc.astype(np.int32), None
        reasons = []
        if tp["crossings"] > MAX_CROSSINGS:
            reasons.append(f"crossings {tp['crossings']} > {MAX_CROSSINGS}")
        if tp["crossings"] == 0:
            reasons.append("the donor does not reach the cut plane")
        if gap > MAX_GAP_MM:
            reasons.append(f"gap {gap:.2f} mm > {MAX_GAP_MM}")
        if mismatch is None:
            reasons.append("no side-photo length reading")
        elif mismatch > MAX_LENGTH_MISMATCH:
            reasons.append(f"length mismatch {100 * mismatch:.1f} % > {100 * MAX_LENGTH_MISMATCH:.0f} %")
        if folded:
            reasons.append(f"folded (inward {inward:.1f} deg, back IoU drop {back_drop:.3f})")
        if not closed:
            reasons.append(f"not closed {topo}")
        accepted = not reasons
        if border:
            flags.append(f"{s}:side_photo_border_contact")
        per_side[s] = {
            "accepted": accepted, "reason": "; ".join(reasons) if reasons else "ok",
            "faces": int(len(Fo)), "vertices": int(len(Vo)),
            "length_mm": len_3d, "side_photo_length_mm": photo_len, "model_length_read_mm": model_read,
            "length_mismatch": mismatch, "length_view": rd["view"] if rd else None,
            "length_reading_temple": rd["temple"] if rd else None, "length_from_symmetric_reading": symmetric,
            "photo_tip_pixel": rd["photo_pixel"] if rd else None, "model_tip_pixel": rd["model_pixel"] if rd else None,
            "photo_border_contact": border,
            "gap_mm": gap, "crossings": tp["crossings"], "cut_loop_sizes_mm": tp["cut_loop_sizes_mm"],
            "lateral_sections_at_plane": ex["donors"][s]["lateral_sections"], "fragments_dropped": ex["donors"][s]["fragments"],
            "endpiece": ex["ends"][s], "hinge_mm": hinge.tolist(), "pivot_donor_mm": pivots[s].tolist(),
            "refine": {k: v for k, v in refine[s].items() if k != "x"},
            "near_view_iou": {"donor": nm["iou_donor"], "refined": nm["iou_refined"]},
            "back_view_iou": {"donor": back["iou_donor"], "refined": back["iou_refined"]},
            "inward_deg": inward, "folded": bool(folded),
            "decimation": tp["decimation"], "donor_full_faces": tp["full_faces"], "donor_full_topology": tp["full_topology"],
            "uv": tp.get("uv"), "closed": closed, "topology": topo, "volume_mm3": vol, "loft": loop_info,
            "components_flipped_outward": flipped_components,
            "cut_loops": len(tp["loops_cut"]), "holes_capped": len(tp["loops_hole"]),
            "tip_mm": tip.tolist(),
        }
        arrays[f"temple_{s}_V"] = Vo.astype(np.float32)
        arrays[f"temple_{s}_F"] = Fo.astype(np.int32)
        if UVo is not None:
            arrays[f"temple_{s}_UV"] = UVo.astype(np.float32)
        arrays[f"hinge_{s}"] = hinge.astype(np.float64)
        if loop_info:                                  # the largest cut loop's copy on the join plane
            li = max(loop_info, key=lambda d: (d["vertices"], -d["copy_start"]))
            arrays[f"join_{s}"] = Vc[li["copy_start"]:li["copy_start"] + li["vertices"]].astype(np.float64)
        if not accepted:
            flags.append(f"{s}:rejected")
        log(f"[{product}] {s}: length {len_3d:.1f} mm, photo {photo_len if photo_len is None else round(photo_len, 1)} mm,"
            f" read {model_read if model_read is None else round(model_read, 1)} mm, gap {gap:.2f} mm, crossings"
            f" {tp['crossings']}, inward {inward:.1f} deg, closed {closed} -> {per_side[s]['reason']}")
    timings["measure_close"] = time.perf_counter() - t

    # ---- donor geometry in front of the split (endpiece/hinge blocks, pads, hardware): bsa.donor
    t = time.perf_counter()
    donor_info = attach_donors(product, run, gen, ex, temples, arrays, flags, log)
    timings["donor"] = time.perf_counter() - t

    result = {
        "stage": STAGE, "product": product, "run": run,
        "cut_z_mm": cut_z, "cut_rule": ex["cut_rule"], "front_z_mm": ex["front_z"],
        "cut_depth_behind_front_mm": ex["front_z"] - cut_z,
        "endpiece_back_face_z_mm": ex["z_b"],
        "R": per_side["R"], "L": per_side["L"],
        "split": ex["split"], "donor": donor_info,
        "length_readings": readings,
        "views": {v: {"iou_temple_region_donor": native_masks[v]["iou_donor"],
                      "iou_temple_region_refined": native_masks[v]["iou_refined"],
                      "roi_xyxy": list(views[v].roi), "fit_stride": views[v].stride} for v in views},
        "conventions": {
            "sides": "temple_R: x > 0 (viewer's right in the front photo), refined on left.jpg (yaw +90, from +X); temple_L: x < 0, right.jpg",
            "length_mm": "reach along -z from the endpiece back face z_b to the rear-most temple point (model mm)",
            "side_photo_length_mm": "same reach read from the S0 matte's extreme pixel along the image direction of -z, back-projected through the frozen S3 camera onto the plane x = x_tip",
            "gap_mm": "max over cut-loop vertices of the distance the loft bridges to the endpiece back face (z) combined with the in-plane shift of the refined cut end",
            "hinge": "hinge_<s> = refinement pivot (cut-loop centroid at the cut plane) after refinement; rotation axes +Y (open) and +X (lift) through it",
            "arrays": "temple_<s>_V float32 mm, temple_<s>_F int32, temple_<s>_UV float32 glTF uv (v down; vertices split at UV seams)",
            "watertight": "each temple is closed (loft + fan caps); check with bsa.contract.topology"},
        "policy": {"ref_band": REF_BAND, "lateral_margin_mm": LATERAL_MARGIN_MM, "h_rule": [H_FACTOR, H_PAD_MM],
                   "w_rule": [W_FACTOR, W_PAD_MM], "cut_behind_mm": CUT_BEHIND_MM,
                   "join_overlap_mm": JOIN_OVERLAP_MM, "max_crossings": MAX_CROSSINGS, "max_gap_mm": MAX_GAP_MM, "max_length_mismatch": MAX_LENGTH_MISMATCH,
                   "fold_max_deg": FOLD_MAX_DEG, "back_iou_drop": BACK_IOU_DROP, "target_faces": TARGET_FACES,
                   "refine_bounds": REFINE_BOUNDS, "refine_prior": REFINE_PRIOR},
        "inputs": {"s1_sha256": gen.result.get("source_sha256"), "photos": {v: s0["views"][v].get("photo_sha256") for v in ("left", "right", "back")}},
        "flags": sorted(set(flags)),
    }
    t = time.perf_counter()
    try:
        make_sheet(product, run, gen, cams, mattes, result, arrays, native_masks, views, temples, static_scene)
    except Exception as e:  # noqa: BLE001 - the sheet must not lose the stage result
        result["flags"].append(f"sheet_failed: {e}")
        log(f"[{product}] sheet failed: {e!r}")
    timings["sheet"] = time.perf_counter() - t
    timings["total"] = time.perf_counter() - t0
    result["timings_s"] = {k: round(v, 2) for k, v in timings.items()}
    sd.save(result, arrays)
    return result


def attach_donors(product: str, run: str, gen, ex: dict, temples_: dict, arrays: dict, flags: list, log=print) -> dict:
    """Append the S5 donor geometry of each side (``bsa.donor.extract``) to ``temple_<s>_V/F/UV`` as further closed
    components (same generator UVs and texture as the arm). Needs S2 (outline, hardware, rim class), S3 (cameras and
    their weights: a down-weighted camera does not carve) and S4 (plate back); the generator basecolor separates its
    lens from its hardware on a rimless front (``donor.hardware_over_lens``). On failure the arms are delivered alone
    and the stage is flagged."""
    from . import depth, donor
    try:
        df = depth.load_depth(product, run)
        src = depth.source_view(product, run)
        s2, a2 = stage_dir(run, product, "s2_front").load()
        _, a4 = stage_dir(run, product, "s4_depth").load()
    except (FileNotFoundError, KeyError) as e:
        flags.append("donor_inputs_missing")
        return {"error": repr(e)}
    polys = depth.lens_polys(a2)
    types = [a2[f"lens{i}_type"] for i in range(1, len(polys) + 1)]
    ppm = cameras.px_per_mm_at(src.camera, gen.frame, cameras.front_piece_centre(gen))
    hw, hw_info = donor.hardware_mask(a2["frame_mask"], polys, types, ppm=ppm)
    hulls = donor.hull_views(product, run, src, gen, gen.frame)
    join = [temples_[s]["V"][np.concatenate(temples_[s]["loops_cut"])] for s in SIDES
            if s in temples_ and temples_[s].get("loops_cut")]
    res = donor.extract(ex["V"], ex["F"], gen.UV, ex["Wpos"], ex["winv"], df, src, gen.frame, a2["fg_sym"], polys, a4,
                        ex["cut_z"], ppm, hardware=hw, hulls=hulls,
                        join_points=np.vstack(join) if join else None, log=log,
                        anchors={s: (np.asarray(arrays[f"temple_{s}_V"], float), np.asarray(arrays[f"temple_{s}_F"], np.int64))
                                 for s in SIDES if f"temple_{s}_V" in arrays},
                        texture=gen.texture("basecolor"), rimless=s2.get("rim_class") == "rimless")
    info = {"hardware": {k: v for k, v in hw_info.items() if k != "detail"}, **res["info"]}
    if res["info"].get("projection_failures"):
        # the donor rules fell back where a projection crossed a camera's near plane: the donor is unverified
        flags.append("donor_projection_failed")
    for s in SIDES:
        part = res.get(s)
        if part is None or f"temple_{s}_V" not in arrays:
            continue
        if not part.get("closed", False):
            flags.append(f"{s}:donor_not_closed")
            continue
        V0, F0 = arrays[f"temple_{s}_V"], arrays[f"temple_{s}_F"]
        arrays[f"temple_{s}_V"] = np.vstack([V0, part["V"].astype(np.float32)])
        arrays[f"temple_{s}_F"] = np.vstack([F0, (part["F"] + len(V0)).astype(np.int32)])
        if f"temple_{s}_UV" in arrays and part.get("UV") is not None:
            arrays[f"temple_{s}_UV"] = np.vstack([arrays[f"temple_{s}_UV"], part["UV"].astype(np.float32)])
        arrays[f"donor_{s}_faces"] = np.array([len(F0), len(F0) + len(part["F"])], np.int64)   # face range in temple_<s>
        log(f"[{product}] {s}: donor {part['faces']} faces in {part['components']} components appended")
    return info


def _median_edge(V: np.ndarray, F: np.ndarray) -> float:
    e = np.linalg.norm(V[F[:, 1]] - V[F[:, 0]], axis=1)
    return float(np.median(e)) if len(e) else 0.0


# ----------------------------------------------------------------------------- sheet
def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def textured_render(parts: list[tuple], cam: Camera, frame: NormFrame, shape: tuple[int, int], light_dir=None) -> np.ndarray:
    """Base colour x Lambert render of several (V, F, UV|None, texture|None, rgb) parts, nearest hit wins."""
    img = np.full(shape + (3,), 255, np.uint8)
    depth = np.full(shape, np.inf, np.float32)
    toward = raster.camera_basis(cam)[2]
    for V, F, UV, tex, rgb in parts:
        r = raster.render(V, F, cam, frame, shape, want_bary=True, want_normal=True)
        m = r["mask"] & (r["depth"] < depth)
        if not m.any():
            continue
        lam = np.abs(r["normal"][m] @ toward)
        shade = (0.45 + 0.55 * lam)[:, None]
        if UV is not None and tex is not None:
            uv = generator.interpolate_uv(UV, F, r["face_id"][m], r["bary"][m])
            col = generator.sample_texture(tex, uv)
        else:
            col = np.tile(np.asarray(rgb, np.float32), (int(m.sum()), 1))
        img[m] = np.clip(col * shade, 0, 255).astype(np.uint8)
        depth[m] = r["depth"][m]
    return img


def _overlay(photo, fg, roi, static, donor, refined, focus, width):
    """red = photo only, green = render only (front piece + refined temples); blue = refined focus temple
    contour, yellow = donor focus temple contour."""
    x0, y0, x1, y1 = roi
    rgb = photo[y0:y1, x0:x1].astype(np.float32)
    ref = fg[y0:y1, x0:x1]
    m = static.copy()
    for k in refined.values():
        m |= k
    po, ro = ref & ~m, m & ~ref
    rgb[po] = rgb[po] * 0.35 + np.array([235, 30, 30]) * 0.65
    rgb[ro] = rgb[ro] * 0.35 + np.array([30, 200, 30]) * 0.65
    rgb = rgb.astype(np.uint8)
    k = width / rgb.shape[1]
    small = cv2.resize(rgb, (width, max(1, int(round(rgb.shape[0] * k)))), interpolation=cv2.INTER_AREA)

    def edge(mask):
        mk = cv2.resize(mask.astype(np.uint8) * 255, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA) >= 128
        return mk & ~ndimage.binary_erosion(mk)

    if focus in donor:
        small[edge(donor[focus])] = (255, 200, 0)
    if focus in refined:
        small[edge(refined[focus])] = (20, 60, 255)
    return small, k


def make_sheet(product, run, gen, cams, mattes, result, arrays, native_masks, views, temples, static_scene):
    prod = PRODUCTS[product]
    frame = gen.frame
    tex = gen.texture("basecolor")
    font, small = _font(20), _font(16)
    W = SHEET_TILE_W
    tiles = []
    # row 1: near side views + back view overlays
    for s, vname in (("R", "left"), ("L", "right"), (None, "back")):
        vd = views[vname]
        nm = native_masks[vname]
        photo = load_photo(prod, vname)
        focus = s if s else "R"
        img, k = _overlay(photo, mattes[vname], vd.roi, nm["static"], nm["donor"], nm["refined"], focus, W)
        im = Image.fromarray(img)
        d = ImageDraw.Draw(im)
        for rd in result.get("length_readings", []):
            if rd["view"] != vname:
                continue
            for key, col in (("photo_pixel", (235, 0, 0)), ("model_pixel", (0, 160, 0))):
                u, v = rd[key]
                px, py = (u - vd.roi[0] + 0.5) * k - 0.5, (v - vd.roi[1] + 0.5) * k - 0.5
                d.line([(px - 9, py), (px + 9, py)], fill=col, width=3)
                d.line([(px, py - 9), (px, py + 9)], fill=col, width=3)
            d.text((px - 30, py - 30), f"tip {rd['temple']}", fill=(0, 0, 0), font=small)
        lines = []
        if s:
            r = result[s]
            lines.append(f"{vname}.jpg -> temple_{s}   IoU(temple region) {r['near_view_iou']['donor']:.3f} -> {r['near_view_iou']['refined']:.3f}")
            pl = r["side_photo_length_mm"]
            mm = r["length_mismatch"]
            src = f"{r['length_view']}, tip {r['length_reading_temple']}"
            lines.append(f"length {r['length_mm']:.1f} mm  photo {pl:.1f} mm ({src})  mismatch {100 * mm:.1f} %"
                         f"  model read {r['model_length_read_mm']:.1f}" if pl is not None else "no side-photo length reading")
            p = r["refine"]["params"]
            lines.append(f"open {p['open_deg']:+.2f} deg  lift {p['lift_deg']:+.2f} deg  t ({p['tx_mm']:+.2f},{p['ty_mm']:+.2f},{p['tz_mm']:+.2f}) mm")
            lines.append(f"gap {r['gap_mm']:.2f} mm  crossings {r['crossings']}  inward {r['inward_deg']:.1f} deg  faces {r['faces']}")
            lines.append(("ACCEPTED" if r["accepted"] else "REJECTED: " + r["reason"])[:80])
        else:
            lines.append(f"back.jpg   IoU(temple region) donor {nm['iou_donor']:.3f} -> refined {nm['iou_refined']:.3f}")
            lines.append("fold check: refined temples vs the back matte (yellow donor R, blue refined R)")
        head = Image.new("RGB", (W, 26 + 22 * len(lines)), (255, 255, 255))
        dh = ImageDraw.Draw(head)
        for i, ln in enumerate(lines):
            col = (0, 0, 0)
            if ln.startswith("REJECTED"):
                col = (200, 0, 0)
            elif ln.startswith("ACCEPTED"):
                col = (0, 130, 0)
            dh.text((6, 4 + 22 * i), ln, fill=col, font=small)
        tiles.append(np.vstack([np.asarray(head), np.asarray(im)]))
    # row 2: 3D renders of the delivered temples with the generator front piece (grey)
    ext = frame.extent
    front_parts = [(gen.Vd, static_scene.F, None, None, (175, 175, 175))]
    tparts = []
    for s in SIDES:
        if f"temple_{s}_V" in arrays:
            tparts.append((arrays[f"temple_{s}_V"].astype(np.float64), arrays[f"temple_{s}_F"],
                           arrays.get(f"temple_{s}_UV"), tex, (120, 120, 200)))
    shape = (int(W * 0.8), W)
    renders = []
    for title, yaw, pitch, zoom, centre in (("top (pitch 90), +Z down", 0, 90, 1.0, None),
                                            ("side from +X (yaw 90)", 90, 5, 1.0, None),
                                            ("oblique yaw 35 pitch 15", 35, 15, 1.0, None)):
        ppm = 0.92 * W / (1.15 * ext) * zoom
        cam = raster.view_camera(frame, yaw, pitch, ppm, shape, center_mm=centre)
        renders.append((title, textured_render(front_parts + tparts, cam, frame, shape)))
    # join close-up at temple_R: yaw 60 looking at the hinge
    if "R" in result and result["R"].get("hinge_mm"):
        h = np.asarray(result["R"]["hinge_mm"])
        ppm = W / 60.0
        cam = raster.view_camera(frame, 70, 15, ppm, shape, center_mm=h + np.array([0, 0, -10.0]))
        renders.append(("join close-up temple_R (yaw 70)", textured_render(front_parts + tparts, cam, frame, shape)))
    rtiles = []
    for title, img in renders:
        im = Image.fromarray(img)
        ImageDraw.Draw(im).text((6, 4), title, fill=(0, 0, 0), font=small)
        rtiles.append(np.asarray(im))
    # assemble
    top_h = max(t.shape[0] for t in tiles)
    row1 = np.full((top_h, len(tiles) * (W + 8), 3), 255, np.uint8)
    for i, t in enumerate(tiles):
        row1[:t.shape[0], i * (W + 8):i * (W + 8) + W] = t
    row2 = np.full((shape[0], len(rtiles) * (W + 8), 3), 255, np.uint8)
    for i, t in enumerate(rtiles):
        row2[:, i * (W + 8):i * (W + 8) + W] = t
    Wtot = max(row1.shape[1], row2.shape[1])
    sheet = np.full((50 + row1.shape[0] + 10 + row2.shape[0], Wtot, 3), 255, np.uint8)
    sheet[50:50 + row1.shape[0], :row1.shape[1]] = row1
    sheet[60 + row1.shape[0]:, :row2.shape[1]] = row2
    im = Image.fromarray(sheet)
    d = ImageDraw.Draw(im)
    d.text((8, 4), f"S5 temples - {product}   cut z {result['cut_z_mm']:.2f} mm ({result['cut_depth_behind_front_mm']:.1f} mm behind the front;"
                   f" {result['cut_rule']})   flags: {', '.join(result['flags']) or 'none'}", fill=(0, 0, 0), font=font)
    d.text((8, 28), "overlays: red = photo only, green = render only (generator front piece + refined temples), blue = refined temple contour,"
                    " yellow = donor contour; crosses: red photo tip, green model tip", fill=(60, 60, 60), font=small)
    im.save(stage_dir(run, product, STAGE).root / "sheet.png")


def contact_sheet(run: str = "m1", products=None, width: int = 2400) -> str:
    """All products' S5 sheets stacked (scaled to ``width``)."""
    products = list(products or PRODUCTS)
    ims = []
    for p in products:
        f = stage_dir(run, p, STAGE).root / "sheet.png"
        if f.exists():
            im = Image.open(f).convert("RGB")
            k = width / im.width
            ims.append(im.resize((width, int(im.height * k)), Image.LANCZOS))
    if not ims:
        return ""
    out = Image.new("RGB", (width, sum(i.height for i in ims)), (255, 255, 255))
    y = 0
    for i in ims:
        out.paste(i, (0, y))
        y += i.height
    path = Path(stage_dir(run, products[0], STAGE).root).parents[1] / "s5_contact.png"
    out.save(path)
    return str(path)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="BSA S5 donor temples")
    ap.add_argument("--product", default=None)
    ap.add_argument("--run", default="m1")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--contact", action="store_true")
    a = ap.parse_args(argv)
    prods = [a.product] if a.product else list(PRODUCTS)
    for p in prods:
        r = run(p, a.run, a.force)
        for s in SIDES:
            q = r[s]
            print(f"{p} {s}: accepted {q['accepted']} ({q['reason']}) length {q.get('length_mm')} photo {q.get('side_photo_length_mm')}"
                  f" gap {q.get('gap_mm')}")
    if a.contact:
        print(contact_sheet(a.run, prods))


if __name__ == "__main__":
    main()
