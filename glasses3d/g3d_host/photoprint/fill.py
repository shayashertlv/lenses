"""WP5 step 5: fill (WP5_DESIGN §6.5): text mask, mirror fill, PyPatchMatch fill and the fill-radius knockout.

Ported from the scratch `g3d-wp5/cov/p4_fill.py` (holes knocked into single-view observed strips, PatchMatch with a
``global_mask`` of non-donors, nearest / mirror / Astra candidates, texture statistics) and the AP6 pitfall; PORT_REVIEW
section 10.

- **Text mask** (D6): texels within 1.5 mm of the GLB's text decals (the fit mesh's skipped primitives with metallic
  < 0.5; q001: ``Left_inner_brand_print``, ``Right_tip_TOM_FORD``), plus photo strokes (painted texels within 5 mm of a
  decal that are bright and achromatic, luminance > 150 and saturation < 60, dilated 0.3 mm), plus **see-through
  text** (fix round 1): translucent acetate shows print on a part's far face through its near face (photo 06 shows
  the +x temple's inner lettering, dark on amber, through its outer face; the bright-stroke rule cannot see it). For
  every painted texel the view ray of its painting view, refracted into the acetate (n = 1.49), is cast up to 8 mm;
  a hit on a text decal, or on a text decal mirrored x -> -x (the partner part's print at the same place: q001 carries
  one inner print, FT1119 has one per temple), marks the texel, dilated 1.0 mm (measured offsets of the photo's
  lettering from the cast position 0.3-0.5 mm). Counts per view and the mirrored-decal share are reported, and a
  warning tells Astra the model has no decal for print the photo shows. Text is never mirrored, and is filled where a
  decal exists unless ``keep_photo_text``. Metal on print that the model lacks (the gold medallion photo 03 shows on
  the -x temple tip) is removed before this step, as a per-view hole (``texels.foreign_blobs``, fix round 2): its
  bezel passed the per-pixel acetate test and left an oval outline in the fix-round-1 bake.
- **Mirror fill**: only texels of surfaces NO photo sees (fix round 1: no view at all sees them at donor angles past
  the whole scene, whatever its edge gate or pixel tests; highlight holes and unpainted texels of a seen surface go to
  PatchMatch) and outside the text mask take the colour of the mirrored point (x -> -x, closest point on the partner part: the selected part whose
  mirrored area centroid is nearest, within 5 mm) when that point is a painted photo texel; a 3-texel band on the
  mirrored side of every mirrored area's boundary is then re-synthesised by PatchMatch. The mirrored share is reported.
- **PatchMatch** (PyPatchMatch ``inpaint``, patch RADIUS in texels): per part, on a canvas that re-packs only that
  part's charts (other parts never donate), one call per (part, facing class) hole group, with donors = the part's
  painted texels of the same facing class and colour family (k-means k <= 3 in Lab over the part's painted texels; the
  families present in the hole's 3-texel ring with >= 10 % share), relaxed to the whole part when fewer than
  max(2000, 2 x hole) donors remain. ``global_mask`` = every canvas texel that is neither donor nor hole: it NEVER
  contains the hole (a hole inside the global mask is silently not filled, AP6; asserted), every call is seeded
  (``patchmatch.set_random_seed``) so runs are byte-identical, and each call is checked: the hole changed and nothing
  outside it did.
- **Radius per part** from {0.3, 0.5, 0.9} mm by knockout: 2, 4, 8 and 16 mm bands across the part's best-painted strip
  chart and an 8 x 5 mm block are knocked out of painted texels and refilled; the radius with the smallest worst
  median |log energy| (0.25-1 mm, 1-4 mm) among those meeting the A4 limits (median |log energy| <= 0.15 in both bands,
  mean-colour dE00 <= 2.0, seam ratio <= 1.2) is used (else the best score, with a warning). These selection numbers
  are the tool's own; the A4 proof is graded by ``tests/instruments/print_metrics.py``.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage

from .. import raster
from .. import texture_utils as T

RADII_MM = (0.3, 0.5, 0.9)
TEXT_DILATE_MM = 1.5
STROKE_NEAR_MM = 5.0
STROKE_LUM = 150.0
STROKE_SAT = 60.0
STROKE_DILATE_MM = 0.3
SEE_THROUGH_MM = 8.0
SEE_THROUGH_DILATE_MM = 1.0
ACETATE_N = 1.49
MIRROR_RING = 3
PARTNER_TOL_MM = 5.0
FAMILY_K = 3
FAMILY_MIN_SHARE = 0.10
RING = 3
KNOCK_BANDS_MM = (2.0, 4.0, 8.0, 16.0)
KNOCK_BLOCK_MM = (8.0, 5.0)
KNOCK_MARGIN_MM = 12.0
A4_LOG_ENERGY = 0.15
A4_DE00 = 2.0
A4_SEAM = 1.2
GUTTER = 8


# ----------------------------------------------------------------------------- part canvases
class Canvas:
    """A part's charts re-packed into their own image (shelf packing, 8-texel gutters): ``to_canvas`` / ``to_atlas``
    move per-texel data; canvas pixels outside the part's texels are never donors or holes."""

    def __init__(self, layout, tex, part_name: str):
        from glasses3d.g3d.printuv import pack as PK
        self.part_name = part_name
        self.charts = [c for c in layout.charts if c["part"] == part_name]
        boxes = [(np.zeros(2), c["w"] + 2, c["h"] + 2) for c in self.charts]
        r = PK.best_width(boxes, atlas_max=1 << 15, gutter=GUTTER)
        off, self.W, self.H = r
        self.off = off
        self.map_ii = []
        self.map_jj = []
        rows = []
        for k, c in enumerate(self.charts):
            sub = tex.index[max(c["y0"] - 1, 0):c["y0"] + c["h"] + 1, max(c["x0"] - 1, 0):c["x0"] + c["w"] + 1]
            ys, xs = np.nonzero(sub >= 0)
            t = sub[ys, xs]
            ok = tex.chart[t] == c["chart"]
            ys, xs, t = ys[ok], xs[ok], t[ok]
            self.map_ii.append(ys + int(off[k][1]))
            self.map_jj.append(xs + int(off[k][0]))
            rows.append(t)
        self.ii = np.concatenate(self.map_ii) if rows else np.zeros(0, np.int64)
        self.jj = np.concatenate(self.map_jj) if rows else np.zeros(0, np.int64)
        self.rows = np.concatenate(rows) if rows else np.zeros(0, np.int64)

    def image(self, values, fill=0, dtype=None) -> np.ndarray:
        values = np.asarray(values)
        out = np.full((self.H, self.W) + values.shape[1:], fill, dtype=dtype or values.dtype)
        out[self.ii, self.jj] = values[self.rows]
        return out

    def gather(self, img: np.ndarray) -> np.ndarray:
        """Canvas values at the part's texels, in ``self.rows`` order."""
        return img[self.ii, self.jj]


# ----------------------------------------------------------------------------- PatchMatch
def patchmatch_call(img: np.ndarray, hole: np.ndarray, donors: np.ndarray, radius: int, seed: int) -> dict:
    """One seeded PyPatchMatch call; asserts the global mask never contains the hole and records the checks."""
    import patchmatch
    img = np.ascontiguousarray(np.asarray(img, np.uint8))
    hole = np.asarray(hole, bool)
    donors = np.asarray(donors, bool) & ~hole
    gmask = ~(donors | hole)
    assert not (gmask & hole).any(), "global_mask contains the hole (PatchMatch would silently not fill it)"
    work = img.copy()
    work[hole] = 0
    patchmatch.set_random_seed(int(seed) & 0x7FFFFFFF)
    out = patchmatch.inpaint(work, hole.astype(np.uint8), global_mask=gmask.astype(np.uint8),
                             patch_size=int(max(1, radius)))
    outside = int((out[~hole] != work[~hole]).any(-1).sum())
    changed = int((out[hole] != work[hole]).any(-1).sum())
    return {"out": out, "outside_changed": outside, "hole_changed": changed, "hole_px": int(hole.sum()),
            "donor_px": int(donors.sum()), "radius_px": int(radius)}


def radius_px(r_mm: float, tx: float) -> int:
    return int(max(1, round(r_mm * tx)))


# ----------------------------------------------------------------------------- colour families
def kmeans_lab(lab: np.ndarray, k: int = FAMILY_K, seed: int = 0, iters: int = 25) -> tuple[np.ndarray, np.ndarray]:
    """Seeded k-means (k-means++ init) on Lab; (centres, labels)."""
    lab = np.asarray(lab, float)
    k = int(min(k, max(1, len(lab))))
    rng = np.random.default_rng(seed)
    sub = lab[rng.choice(len(lab), size=min(len(lab), 20000), replace=False)] if len(lab) > 20000 else lab
    c = [sub[rng.integers(len(sub))]]
    for _ in range(1, k):
        d2 = np.min(((sub[:, None] - np.asarray(c)[None]) ** 2).sum(-1), 1)
        if d2.sum() <= 0:
            break
        c.append(sub[rng.choice(len(sub), p=d2 / d2.sum())])
    C = np.asarray(c)
    for _ in range(iters):
        lb = np.argmin(((sub[:, None] - C[None]) ** 2).sum(-1), 1)
        newC = np.array([sub[lb == j].mean(0) if (lb == j).any() else C[j] for j in range(len(C))])
        if np.allclose(newC, C):
            break
        C = newC
    return C, np.argmin(((lab[:, None] - C[None]) ** 2).sum(-1), 1)


# ----------------------------------------------------------------------------- text mask
def _refracted(N: np.ndarray, to_cam: np.ndarray, n: float = ACETATE_N) -> np.ndarray:
    """Unit direction of a view ray after refraction into the material (outward normals ``N``, ``to_cam`` = unit
    direction toward the camera)."""
    i = -np.asarray(to_cam, float)[None]
    ci = np.clip((N * np.asarray(to_cam, float)[None]).sum(1), 0.0, 1.0)
    eta = 1.0 / n
    k = 1.0 - eta ** 2 * (1.0 - ci ** 2)
    t = eta * i + (eta * ci - np.sqrt(np.maximum(k, 0.0)))[:, None] * N
    return t / (np.linalg.norm(t, axis=1, keepdims=True) + 1e-12)


def text_mask(job, tex, decals: dict, painted: np.ndarray, col: np.ndarray, tx: float, *, label=None,
              views: list[str] | None = None, see_through: bool = True, mirror_decals: bool = True) -> dict:
    """{'mask', 'decal_px', 'stroke_px', 'see_through_px', 'decals', ...}: texels within 1.5 mm of a text decal, nearby
    photo strokes, and see-through text (``label`` (n,) painting view index into ``views``)."""
    import open3d as o3d
    n = len(tex)
    keep = [i for i, m in enumerate(decals.get("metallic", [])) if m < 0.5]
    out = {"mask": np.zeros(n, bool), "decal_px": 0, "stroke_px": 0, "see_through_px": 0,
           "decals": [decals["names"][i] for i in keep]}
    if not keep:
        return out
    sel = np.isin(decals["prim"], keep)
    sc = o3d.t.geometry.RaycastingScene()
    sc.add_triangles(o3d.core.Tensor(decals["V"].astype(np.float32)),
                     o3d.core.Tensor(decals["F"][sel].astype(np.uint32)))
    d = sc.compute_distance(o3d.core.Tensor(tex.P.astype(np.float32))).numpy()
    m = d <= TEXT_DILATE_MM
    f = np.asarray(col, float)
    stroke = painted & (d <= STROKE_NEAR_MM) & (f.mean(-1) > STROKE_LUM) & ((f.max(-1) - f.min(-1)) < STROKE_SAT)
    if stroke.any():
        img = ndimage.binary_dilation(tex.image(stroke, False, bool), iterations=max(1, int(round(STROKE_DILATE_MM * tx))))
        stroke = img[tex.ii, tex.jj]
    through = np.zeros(n, bool)
    info = {"see_through_by_view": {}, "see_through_hits_on_mirrored_decal": 0, "see_through_hits": 0}
    if see_through and label is not None and views:
        Vd = decals["V"].astype(np.float32)
        Fd = decals["F"][sel].astype(np.uint32)
        st = o3d.t.geometry.RaycastingScene()
        st.add_triangles(o3d.core.Tensor(Vd), o3d.core.Tensor(Fd))
        if mirror_decals:
            st.add_triangles(o3d.core.Tensor((Vd * np.array([-1.0, 1.0, 1.0], np.float32)).astype(np.float32)),
                             o3d.core.Tensor(Fd[:, ::-1].copy()))
        label = np.asarray(label)
        hit = np.zeros(n, bool)
        for vi, v in enumerate(views):
            rows = np.nonzero(painted & (label == vi))[0]
            if not len(rows):
                continue
            to_cam = np.asarray(raster.camera_basis(job.views[v].camera)[2], float)
            Nn = tex.N[rows]
            dirs = _refracted(Nn, to_cam)
            rays = np.concatenate([tex.P[rows] - 0.01 * Nn, dirs], 1).astype(np.float32)
            a = st.cast_rays(o3d.core.Tensor(rays))
            t = a["t_hit"].numpy()
            h = np.isfinite(t) & (t <= SEE_THROUGH_MM)
            gid = a["geometry_ids"].numpy()
            hit[rows[h]] = True
            info["see_through_by_view"][v] = int(h.sum())
            info["see_through_hits_on_mirrored_decal"] += int((h & (gid == 1)).sum()) if mirror_decals else 0
        info["see_through_hits"] = int(hit.sum())
        if hit.any():
            img = ndimage.binary_dilation(tex.image(hit, False, bool),
                                          iterations=max(1, int(round(SEE_THROUGH_DILATE_MM * tx))))
            through = img[tex.ii, tex.jj] & (tex.chart >= 0)
    out.update({"mask": m | stroke | through, "decal_px": int(m.sum()), "stroke_px": int((stroke & ~m).sum()),
                "see_through_px": int((through & ~m & ~stroke).sum()), "see_through_mask": through,
                "distance": d, **info})
    return out


# ----------------------------------------------------------------------------- mirror fill
def mirror_partners(job, part_ids: list[int]) -> dict[int, int]:
    """part -> partner part (mirrored area centroid within 5 mm, opposite x side)."""
    V, F, part = job.mesh.V, job.mesh.F, job.mesh.part
    area = job.face_area
    cen = {}
    for p in part_ids:
        m = part == p
        cen[p] = (V[F[m]].mean(1) * area[m, None]).sum(0) / max(area[m].sum(), 1e-12)
    out = {}
    for p in part_ids:
        best, bd = None, PARTNER_TOL_MM
        for q in part_ids:
            if q == p or np.sign(cen[q][0]) == np.sign(cen[p][0]):
                continue
            d = float(np.linalg.norm(cen[q] - cen[p] * np.array([-1.0, 1.0, 1.0])))
            if d < bd:
                best, bd = q, d
        if best is not None:
            out[p] = best
    return out


def mirror_lookup(job, layout, tex, cand: np.ndarray, partners: dict[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """For the candidate texels: the atlas texel row of the mirrored point on the partner part (closest point), and
    whether it was found (-1 / False otherwise)."""
    import open3d as o3d
    src = -np.ones(len(tex), np.int64)
    found = np.zeros(len(tex), bool)
    lrow = layout.face_index(len(job.mesh.F))
    for p, q in partners.items():
        sel = cand & (tex.part == p)
        if not sel.any():
            continue
        fq = np.nonzero(job.mesh.part == q)[0]
        sc = o3d.t.geometry.RaycastingScene()
        sc.add_triangles(o3d.core.Tensor(job.mesh.V.astype(np.float32)), o3d.core.Tensor(job.mesh.F[fq].astype(np.uint32)))
        Q = (tex.P[sel] * np.array([-1.0, 1.0, 1.0])).astype(np.float32)
        ans = sc.compute_closest_points(o3d.core.Tensor(Q))
        face = fq[ans["primitive_ids"].numpy().astype(np.int64)]
        b2 = ans["primitive_uvs"].numpy().astype(np.float64)
        bary = np.stack([1 - b2[:, 0] - b2[:, 1], b2[:, 0], b2[:, 1]], 1)
        dist = np.linalg.norm(ans["points"].numpy() - Q, axis=1)
        r = lrow[face]
        okr = (r >= 0) & (dist <= 0.5)
        uv = np.einsum("kj,kjd->kd", bary[okr], layout.uv[r[okr]])
        jj = np.clip(np.floor(uv[:, 0]).astype(np.int64), 0, tex.shape[1] - 1)
        ii = np.clip(np.floor(uv[:, 1]).astype(np.int64), 0, tex.shape[0] - 1)
        t = tex.index[ii, jj]
        idx = np.nonzero(sel)[0][okr]
        good = t >= 0
        src[idx[good]] = t[good]
        found[idx[good]] = True
    return src, found


# ----------------------------------------------------------------------------- knockout statistics (tool side)
def _mg(x, m, s):
    if s <= 0:
        return x
    mf = m.astype(float)
    return ndimage.gaussian_filter(x * mf, s) / np.maximum(ndimage.gaussian_filter(mf, s), 1e-6)


def knock_stats(filled: np.ndarray, truth: np.ndarray, hole: np.ndarray, support: np.ndarray, tx: float) -> dict:
    """The tool's own selection statistics of one knocked-out hole (the A4 proof uses the instrument instead)."""
    lf, lt = filled.astype(float).mean(-1), truth.astype(float).mean(-1)
    Hc = ndimage.binary_erosion(hole, iterations=1)
    if Hc.sum() < 20:
        Hc = hole
    out = {}
    for name, a, b in (("e1", 0.25, 1.0), ("e2", 1.0, 4.0)):
        bf = _mg(lf, support, a * tx) - _mg(lf, support, b * tx)
        bt = _mg(lt, support, a * tx) - _mg(lt, support, b * tx)
        out[name] = float(abs(math.log(max(bf[Hc].std(), 1e-6) / max(bt[Hc].std(), 1e-6))))
    la, lb = T.lab_of(filled[Hc].astype(float)).mean(0), T.lab_of(truth[Hc].astype(float)).mean(0)
    from skimage.color import deltaE_ciede2000
    out["de_mean"] = float(deltaE_ciede2000(la[None], lb[None])[0])
    ring = (ndimage.binary_dilation(hole, iterations=1) & ~ndimage.binary_erosion(hole, iterations=1)) & support
    gf = np.hypot(*np.gradient(_mg(lf, support, 1.0)))
    gt = np.hypot(*np.gradient(_mg(lt, support, 1.0)))
    out["seam"] = float(gf[ring].mean() / max(gt[ring].mean(), 1e-6)) if ring.any() else 1.0
    return out


def single_label(canvas_painted: np.ndarray, canvas_label: np.ndarray, box) -> np.ndarray:
    """The painted texels of the label that paints most of ``box`` (one photo: a knockout's truth and donors never
    straddle a label seam, which would put a photo-to-photo step into the truth)."""
    y0, y1, x0, x1 = box
    sub = canvas_label[y0:y1, x0:x1][canvas_painted[y0:y1, x0:x1]]
    sub = sub[sub >= 0]
    if not len(sub):
        return canvas_painted
    lab = int(np.bincount(sub).argmax())
    return canvas_painted & (canvas_label == lab)


def knockout_holes(canvas_painted: np.ndarray, strip_box, tx: float) -> list[tuple[str, np.ndarray]]:
    """Bands across the strip (along canvas x = s) of 2-16 mm and an 8 x 5 mm block, inside painted texels eroded 4
    texels, centred on the painted area's s median."""
    y0, y1, x0, x1 = strip_box
    R = np.zeros_like(canvas_painted)
    R[y0:y1, x0:x1] = canvas_painted[y0:y1, x0:x1]
    lab, n = ndimage.label(R)
    if n == 0:
        return []
    big = 1 + int(np.argmax(ndimage.sum(R, lab, range(1, n + 1))))
    R = lab == big
    Re = ndimage.binary_erosion(R, iterations=4)
    ys, xs = np.nonzero(Re)
    if not len(xs):
        return []
    cx = float(np.median(xs))
    out = []
    for ell in KNOCK_BANDS_MM:
        w = ell * tx
        hm = np.zeros_like(R)
        hm[:, int(cx - w / 2):int(cx + w / 2) + 1] = True
        hm &= Re
        if hm.sum() > 50:
            out.append((f"band{ell:g}mm", hm))
    cy = float(np.median(ys))
    bw, bh = KNOCK_BLOCK_MM[0] * tx, KNOCK_BLOCK_MM[1] * tx
    hm = np.zeros_like(R)
    hm[int(cy - bh / 2):int(cy + bh / 2) + 1, int(cx - bw / 2):int(cx + bw / 2) + 1] = True
    hm &= Re
    if hm.sum() > 50:
        out.append(("block8x5mm", hm))
    return out


# ----------------------------------------------------------------------------- tiled PatchMatch (thin bands)
TILE = 320
TILE_MARGIN = 48


def band_tiles(hole: np.ndarray, tile: int = TILE, margin: int = TILE_MARGIN) -> list[tuple]:
    """(core box, crop box) of every tile of an image that contains hole pixels; crops extend ``margin`` beyond the
    core (donor context), cores tile the image without overlap."""
    H, W = hole.shape
    out = []
    for y0 in range(0, H, tile):
        for x0 in range(0, W, tile):
            y1, x1 = min(y0 + tile, H), min(x0 + tile, W)
            if not hole[y0:y1, x0:x1].any():
                continue
            out.append(((y0, y1, x0, x1), (max(y0 - margin, 0), min(y1 + margin, H), max(x0 - margin, 0),
                                           min(x1 + margin, W))))
    return out


def tile_job(img, hole, donors, core, crop, radius, seed):
    """PatchMatch on one crop; only the core's hole pixels are kept. Returns (core, filled core, checks)."""
    cy0, cy1, cx0, cx1 = crop
    r = patchmatch_call(img[cy0:cy1, cx0:cx1], hole[cy0:cy1, cx0:cx1], donors[cy0:cy1, cx0:cx1], radius, seed)
    y0, y1, x0, x1 = core
    sub = r["out"][y0 - cy0:y1 - cy0, x0 - cx0:x1 - cx0]
    return core, sub, {"outside_changed": r["outside_changed"], "hole_changed": r["hole_changed"],
                       "hole_px": r["hole_px"], "donor_px": r["donor_px"]}
