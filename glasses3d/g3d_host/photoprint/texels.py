"""WP5 step 2: texels, per-view samples and tiers (WP5_DESIGN §6.2), host side.

- ``atlas_texels``: every atlas texel covered by a layout triangle (``g3d.printuv`` layout, texel (i, j) centre at
  (j + 0.5, i + 0.5)) gets its face, barycentrics, point and outward face normal on S, its chart, part and region.
- ``OcclusionScene``: the WHOLE scene a view sees, S' of the fit mesh plus the GLB's decal primitives the fit mesh
  skips (q001: the inner brand print, the TOM FORD lettering, the tip medallion), so lenses, eyewire, hinges and
  inlays occlude print (0.5 mm first-surface tolerance).
- ``view_samples``: a view counts at a texel only if it sees the point past the whole scene (``visible_points``,
  cos >= 0.5, first surface within 0.5 mm), the pixel is inside the matte eroded 1 px and on the PER-PIXEL acetate
  class (``View.valid``: dark or saturated-and-not-bright; 40-65 % of frame pixels are the print's dark achromatic
  phase, AP1), not clipped (any channel >= 250, dilated 1 px: a hole for that view, §6.3) and more than 0.5 mm from an
  occluder contact (a rendered pixel of a part that is not print, e.g. eyewire, lens, hinge, decal). Colours are
  sampled bicubically (cubic B-spline, prefiltered once per view) at pi_v(S'); the weight is cos^2 x local px/mm.
  **Foreign blobs (fix round 2):** metal the photo shows ON print but the model lacks (FT1119: the gold TOM FORD
  medallion on the -x temple tip in photo 03; q001 carries a medallion only on the +x tip) failed the acetate test only
  inside: its bright interior became a hole while its darker bezel ring passed as acetate and was painted, baking an
  oval outline onto the shipped temple. Every connected component of the photo's non-acetate pixels inside the matte
  (eroded 1 px) that is bright (mean luminance >= ``FOREIGN_LUM_MIN``), compact (inscribed half-width >=
  ``FOREIGN_MIN_HALF_MM``, not a thin highlight streak), large (>= ``FOREIGN_MIN_MM2``) and lies mostly on rendered
  print (>= ``FOREIGN_ON_PRINT_MIN`` of its pixels: the model has no part there) is a HOLE for that view, dilated
  ``FOREIGN_DILATE_MM`` so the bezel and anti-aliased edge go too; each is reported (``ViewSamples.foreign``, ``kind``
  'gold' when its mean R - B >= ``FOREIGN_GOLD_RB``, else 'bright achromatic (glare or silver)') so Astra hears of
  print-side metal the model lacks.
  ``seen_graze`` (fix round 2): the point is the first surface, faces the camera at cos > ``GRAZE_MIN_COS`` and projects
  inside the matte (eroded 1 px): a photo shows it, perhaps obliquely (mirror fill reports where it lands on such
  surfaces). The donor visibility is the same call's cos > ``DONOR_MIN_COS`` subset (identical first-surface test).
- ``tiers``: per region, the anchor = the admitted view (edge gate per (view, part), D2) with the largest weight sum
  among views that see >= 5 % of the region's texels (§5.1); tier 1 = a registered set (>= 2 views, from step 0),
  tier 2 = the anchor alone (single-source mode: each region painted from its anchor photo only, never mixing
  unregistered views), tier 3 = unseen (filled).

Sides are the x sign; nothing here reads part names except to select the parts the caller listed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .. import glb as glbmod
from .. import raster
from .. import texture_utils as T
from .inputs import ppm_local
from .pose import points

DONOR_MIN_COS = 0.5
VIS_TOL_MM = 0.5
OCCLUDER_MARGIN_MM = 0.5
CLIP_LEVEL = 250
ANCHOR_MIN_SHARE = 0.05
ANCHOR_MIN_TEXELS = 50
PARTNER_MIN_SHARE = 0.10
GRAZE_MIN_COS = 0.1
FOREIGN_MIN_MM2 = 2.0
FOREIGN_MIN_HALF_MM = 0.5
FOREIGN_LUM_MIN = 150.0
FOREIGN_ON_PRINT_MIN = 0.5
FOREIGN_DILATE_MM = 0.75
FOREIGN_GOLD_RB = 30.0          # mean R - B of a 'gold' blob (metal); below it: bright achromatic (glare or silver)


@dataclass(eq=False)
class Texels:
    shape: tuple[int, int]
    ii: np.ndarray
    jj: np.ndarray
    face: np.ndarray
    bary: np.ndarray
    P: np.ndarray
    N: np.ndarray
    chart: np.ndarray
    part: np.ndarray            # mesh part id
    region: np.ndarray          # region id (-1: none)
    row: np.ndarray             # (n,) row of the layout
    index: np.ndarray           # (H, W) texel row or -1

    def __len__(self) -> int:
        return len(self.face)

    def image(self, values, fill=0, dtype=None) -> np.ndarray:
        """Scatter per-texel values into an atlas image."""
        values = np.asarray(values)
        shp = self.shape + values.shape[1:]
        out = np.full(shp, fill, dtype=dtype or values.dtype)
        out[self.ii, self.jj] = values
        return out


def atlas_texels(job, layout, regions=None) -> Texels:
    """Rasterise the layout's triangles (texel centres, later triangles win on shared edges)."""
    H, W = int(layout.height), int(layout.width)
    rid, bary = T.rasterize_triangles(layout.uv, (H, W))
    ii, jj = np.nonzero(rid >= 0)
    row = rid[ii, jj].astype(np.int64)
    face = layout.faces[row]
    b = bary[ii, jj].astype(np.float64)
    P, N = points(job.mesh.V, job.mesh.F, face, b)
    reg = np.full(len(face), -1, np.int64)
    if regions is not None:
        lut = np.full(len(job.mesh.F), -1, np.int64)
        for r in regions:
            lut[r.faces] = r.rid
        reg = lut[face]
    index = np.full((H, W), -1, np.int64)
    index[ii, jj] = np.arange(len(face))
    return Texels(shape=(H, W), ii=ii, jj=jj, face=face, bary=b, P=P, N=N, chart=layout.chart[row],
                  part=job.mesh.part[face], region=reg, row=row, index=index)


# ----------------------------------------------------------------------------- occluders
def decal_geometry(glb_path, skipped: list[str]) -> dict:
    """The GLB primitives the fit mesh skipped (decals), in model mm: {'V', 'F', 'names', 'metallic', 'material'} per
    primitive concatenated (``prim`` = per-face primitive index)."""
    g = glbmod.load(glb_path)
    Vs, Fs, prim, names, metal, mats = [], [], [], [], [], []
    off = 0
    want = set(skipped)
    for p in g.primitives:
        if p.node_name not in want or p.mode != 4 or not len(p.triangles):
            continue
        m = g.material(p.material_index) or {}
        pbr = m.get("pbrMetallicRoughness") or {}
        Vs.append(p.positions * 1000.0)
        Fs.append(p.triangles + off)
        prim.append(np.full(len(p.triangles), len(names)))
        off += len(p.positions)
        names.append(p.node_name)
        metal.append(float(pbr.get("metallicFactor", 1.0)))          # glTF default 1.0
        mats.append(m.get("name"))
    if not Vs:
        return {"V": np.zeros((0, 3)), "F": np.zeros((0, 3), np.int64), "prim": np.zeros(0, np.int64), "names": [],
                "metallic": [], "material": []}
    return {"V": np.vstack(Vs), "F": np.vstack(Fs).astype(np.int64), "prim": np.concatenate(prim), "names": names,
            "metallic": metal, "material": mats}


class OcclusionScene:
    """Per view: a ``raster.RasterScene`` of S' (the view's fit-mesh vertices) plus the decals (fixed geometry), and
    per face whether it is print (one of the listed part ids)."""

    def __init__(self, job, print_part_ids, decals: dict | None = None):
        self.job = job
        self.decals = decals or {"V": np.zeros((0, 3)), "F": np.zeros((0, 3), np.int64)}
        nf = len(job.mesh.F)
        self.is_print = np.zeros(nf + len(self.decals["F"]) + 1, bool)   # trailing False for face id -1
        self.is_print[:nf] = np.isin(job.mesh.part, list(print_part_ids))
        self._cache: dict = {}

    def scene(self, V: np.ndarray) -> raster.RasterScene:
        key = raster.mesh_key(V, self.job.mesh.F, self.job.frame)
        sc = self._cache.get(key)
        if sc is None:
            if len(self._cache) > 4:
                self._cache.clear()
            if len(self.decals["F"]):
                VV = np.vstack([V, self.decals["V"]])
                FF = np.vstack([self.job.mesh.F, self.decals["F"] + len(V)])
            else:
                VV, FF = V, self.job.mesh.F
            sc = raster.RasterScene(VV, FF, self.job.frame)
            self._cache[key] = sc
        return sc


# ----------------------------------------------------------------------------- per-view samples
@dataclass(eq=False)
class ViewSamples:
    view: str
    ok: np.ndarray              # (n,) usable donor sample
    col: np.ndarray             # (n, 3) float sRGB (0 where not ok)
    cos: np.ndarray
    ppm: np.ndarray
    w: np.ndarray               # cos^2 x px/mm where ok, else 0
    uv: np.ndarray
    seen: np.ndarray            # visible at donor angles past the whole scene (before the pixel tests)
    reasons: dict = field(default_factory=dict)
    seen_graze: np.ndarray | None = None     # first surface, cos > GRAZE_MIN_COS, inside the matte (fix round 2)
    foreign: list = field(default_factory=list)   # foreign blobs (metal on print the model lacks), fix round 2


_SPLINES: dict = {}


def _spline(view) -> list[np.ndarray]:
    key = id(view.image)
    sp = _SPLINES.get(key)
    if sp is None:
        if len(_SPLINES) > 8:
            _SPLINES.clear()
        img = np.asarray(view.image, np.float64)
        sp = [ndimage.spline_filter(img[..., c], order=3, mode="nearest") for c in range(3)]
        _SPLINES[key] = sp
    return sp


def sample_bicubic(view, uv: np.ndarray) -> np.ndarray:
    """Cubic B-spline (interpolating) colours of a view's photo at native pixel coordinates (integer = centre)."""
    sp = _spline(view)
    coords = np.stack([uv[:, 1], uv[:, 0]])
    return np.stack([ndimage.map_coordinates(c, coords, order=3, mode="nearest", prefilter=False) for c in sp], -1)


def clipped_mask(img: np.ndarray, level: int = CLIP_LEVEL) -> np.ndarray:
    return ndimage.binary_dilation((np.asarray(img) >= level).any(-1), iterations=1)


def occluder_distance(job, occ: OcclusionScene, V: np.ndarray, cam, shape, *, face_id=None) -> np.ndarray:
    """Distance (px) of every pixel to the nearest rendered pixel of a non-print face of the whole scene."""
    if face_id is None:
        face_id = occ.scene(V).render(cam, shape, 1)["face_id"]
    fid = face_id
    other = (fid >= 0) & ~occ.is_print[fid]
    if not other.any():
        return np.full(shape, np.inf)
    return ndimage.distance_transform_edt(~other)


def foreign_blobs(image: np.ndarray, matte: np.ndarray, print_px: np.ndarray, ppm: float, *, part_px=None,
                  names=None) -> dict:
    """{'mask' (H, W) bool hole (dilated), 'blobs': [...]}: bright, compact, large non-acetate components of the photo
    lying mostly on rendered print (see the module docstring). ``print_px`` (H, W) = pixels whose first hit is print;
    ``part_px`` (H, W) part id per pixel (-1 none) and ``names`` only label the report."""
    from .signals import acetate_mask
    img = np.asarray(image, np.float64)
    inside = ndimage.binary_erosion(np.asarray(matte, bool), iterations=1)
    non = inside & ~acetate_mask(img)
    lab, n = ndimage.label(non, structure=np.ones((3, 3), bool))
    mask = np.zeros(non.shape, bool)
    blobs = []
    if n == 0:
        return {"mask": mask, "blobs": blobs}
    idx = np.arange(1, n + 1)
    area = ndimage.sum(non, lab, idx)
    lum = img.mean(-1)
    big = idx[area >= FOREIGN_MIN_MM2 * ppm * ppm]
    if not len(big):
        return {"mask": mask, "blobs": blobs}
    on_print = ndimage.mean(print_px.astype(float), lab, big)
    mlum = ndimage.mean(lum, lab, big)
    dt = ndimage.distance_transform_edt(non)
    half = ndimage.maximum(dt, lab, big)
    keep = (on_print >= FOREIGN_ON_PRINT_MIN) & (mlum >= FOREIGN_LUM_MIN) & (half >= FOREIGN_MIN_HALF_MM * ppm)
    sel = big[keep]
    if not len(sel):
        return {"mask": mask, "blobs": blobs}
    core = np.isin(lab, sel)
    mask = ndimage.binary_dilation(core, iterations=max(1, int(round(FOREIGN_DILATE_MM * ppm))))
    for c in sel:
        m = lab == c
        ys, xs = np.nonzero(m)
        row = {"area_mm2": round(float(m.sum()) / ppm ** 2, 2), "bbox_px": [int(ys.min()), int(ys.max()), int(xs.min()),
                                                                          int(xs.max())],
               "centre_px": [round(float(xs.mean()), 1), round(float(ys.mean()), 1)],
               "mean_rgb": [int(round(x)) for x in img[m].mean(0)],
               "on_print_share": round(float(print_px[m].mean()), 3),
               "half_width_mm": round(float(dt[m].max()) / ppm, 2)}
        col = img[m].mean(0)
        row["kind"] = "gold" if col[0] - col[2] >= FOREIGN_GOLD_RB else "bright achromatic (glare or silver)"
        if part_px is not None and names is not None:
            pp = part_px[m]
            pp = pp[pp >= 0]
            if len(pp):
                row["part"] = names[int(np.bincount(pp).argmax())]
        blobs.append(row)
    return {"mask": mask, "blobs": blobs}


def view_samples(job, tex: Texels, view: str, occ: OcclusionScene, *, cam=None, V: np.ndarray | None = None,
                 min_cos: float = DONOR_MIN_COS, margin_mm: float = OCCLUDER_MARGIN_MM,
                 subset: np.ndarray | None = None) -> ViewSamples:
    """The donor samples of one view at every texel (``subset``: only those rows; the others are not ok)."""
    v = job.views[view]
    cam = cam or v.camera
    V = job.mesh.V if V is None else V
    n = len(tex)
    idx = np.arange(n) if subset is None else np.nonzero(subset)[0]
    P, N = points(V, job.mesh.F, tex.face[idx], tex.bary[idx])
    H, W = v.shape
    vis_g, uv = T.visible_points(P, N, cam, job.frame, occ.scene(V), (H, W), min_cos=min(min_cos, GRAZE_MIN_COS),
                                 tol_mm=VIS_TOL_MM)
    cos = N @ raster.camera_basis(cam)[2]
    vis = vis_g & (cos > min_cos)                  # the same first-surface test, donor angles only
    inb = (uv[:, 0] > 0.5) & (uv[:, 0] < W - 1.5) & (uv[:, 1] > 0.5) & (uv[:, 1] < H - 1.5)
    ui = np.clip(np.round(uv[:, 0]).astype(int), 0, W - 1)
    vi = np.clip(np.round(uv[:, 1]).astype(int), 0, H - 1)
    valid = v.valid[vi, ui]
    clip = clipped_mask(v.image)[vi, ui]
    ppm = ppm_local(cam, job.frame, P)
    rr = occ.scene(V).render(cam, (H, W), 1)
    fid = rr["face_id"]
    dist = occluder_distance(job, occ, V, cam, (H, W), face_id=fid)
    near = dist[vi, ui] <= margin_mm * ppm
    nf_ = len(job.mesh.F)
    part_px = np.where((fid >= 0) & (fid < nf_), job.mesh.part[np.clip(fid, 0, nf_ - 1)], -1)
    fb = foreign_blobs(v.image, v.matte, (fid >= 0) & occ.is_print[fid], job.ppm(view, cam), part_px=part_px,
                       names=job.mesh.names)
    foreign = fb["mask"][vi, ui]
    seen = vis & inb
    graze = vis_g & inb & ndimage.binary_erosion(np.asarray(v.matte, bool), iterations=1)[vi, ui]
    ok = seen & valid & ~clip & ~near & ~foreign
    col = np.zeros((len(idx), 3))
    if ok.any():
        col[ok] = sample_bicubic(v, uv[ok])
    out_ok = np.zeros(n, bool)
    out_ok[idx] = ok
    out_col = np.zeros((n, 3))
    out_col[idx] = np.clip(col, 0, 255)
    out_cos = np.zeros(n)
    out_cos[idx] = cos
    out_ppm = np.zeros(n)
    out_ppm[idx] = ppm
    out_uv = np.full((n, 2), np.nan)
    out_uv[idx] = uv
    out_seen = np.zeros(n, bool)
    out_seen[idx] = seen
    out_graze = np.zeros(n, bool)
    out_graze[idx] = graze
    w = np.where(out_ok, np.clip(out_cos, 0, 1) ** 2 * out_ppm, 0.0)
    reasons = {"texels": int(len(idx)), "seen_donor_angle": int(seen.sum()), "ok": int(ok.sum()),
               "not_valid_pixel": int((seen & ~valid).sum()), "clipped": int((seen & valid & clip).sum()),
               "near_occluder": int((seen & valid & ~clip & near).sum()),
               "foreign_blob": int((seen & valid & ~clip & ~near & foreign).sum()),
               "seen_graze": int(graze.sum())}
    return ViewSamples(view=view, ok=out_ok, col=out_col, cos=out_cos, ppm=out_ppm, w=w, uv=out_uv, seen=out_seen,
                       reasons=reasons, seen_graze=out_graze, foreign=fb["blobs"])


# ----------------------------------------------------------------------------- anchors and tiers
def region_anchors(tex: Texels, regions, samples: dict[str, ViewSamples], admitted: dict, *,
                   min_share: float = ANCHOR_MIN_SHARE) -> dict:
    """Per region id: {'anchor', 'texels', 'views': {view: {'seen', 'weight', 'admitted'}}, 'partners_seen'} where
    the anchor = the admitted view with the largest weight sum among views seeing >= max(50, 5 %) of its texels;
    'others' = admitted views (not the anchor) seeing >= 10 % of the texels (they overlap, unregistered)."""
    out = {}
    for r in regions:
        sel = tex.region == r.rid
        nt = int(sel.sum())
        rows = {}
        for n, s in samples.items():
            rows[n] = {"seen": int(s.ok[sel].sum()), "weight": float(s.w[sel].sum()),
                       "admitted": bool(admitted.get((n, r.part_name), False))}
        need = max(ANCHOR_MIN_TEXELS, int(min_share * nt))
        cands = [n for n in samples if rows[n]["admitted"] and rows[n]["seen"] >= need]
        anchor = max(cands, key=lambda n: (rows[n]["weight"], -list(samples).index(n))) if cands else None
        others = [n for n in samples if n != anchor and rows[n]["admitted"]
                  and rows[n]["seen"] >= max(ANCHOR_MIN_TEXELS, int(PARTNER_MIN_SHARE * nt))]
        out[r.rid] = {"region": r.name, "anchor": anchor, "texels": nt, "views": rows, "others": others}
    return out
