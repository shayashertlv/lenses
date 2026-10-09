"""WP6 fitted comparison views (plan/WP6_DESIGN.md §6): every compared model (candidate, fitted slab, delivered) is
scored at its OWN bounded camera refit and per-photo hinge angle, inside one temple ROI fixed per comparison.

    Job               a product's photos (sha256-checked reference data), glossy_floor mattes, base cameras (the
                      glossy WP3b cameras), the delivered fit mesh split into non-temple parts and the two temples
                      (by geometry: the parts reaching >= 60 mm back, side = sign of x; never by name), the hinge
    Model             the non-temple parts + two temples: delivered, or built by the WP6 core from a spec; each temple
                      opens by theta about its vertical hinge axis
    render_model      front and temple scenes rendered separately and merged by depth: mask, part tag (0 = not a
                      temple, +1 / -1 = temple side), hit points / normals, station d of temple hits
    render_mask       render_model's mask, bit for bit, without the depth merge and with each camera's rays reused
    render_tags       render_model's mask, tag and station d, bit for bit, merged only inside the temples' box
                      (both for the joint fit's objective; tests/test_temple_fit_parallel.py)
    refit_camera      staged bounded Powell from the base camera inside yaw +-3, pitch +-2, roll +-1 deg, scale +-3 %,
                      shift +-10 px, perspective +-0.1; kept only if the front-part contour degrades <= 0.1 mm
    fit_theta         per photo and temple side in [-4, +12] deg: 1 deg grid, then bounded Brent, on the temple ROI
    rear_family       06: camera refit per symmetric opening alpha with pitch in [5, 16] deg and perspective in
                      [0.03, 0.4]; the family = every alpha with loss <= 1.2 x the minimum; frame-fit guard per alpha
    temple_roi        dilate20(union of every model's temple pixels) U dilate10(matte minus dilate6(non-temple))
    roi_metrics       IoU and symmetric contour distance (mm at the hinge-zone px/mm, truncated at 60 px); computed
                      on the ROI box with a k-d tree, bit for bit the full-image distance-transform formula
    envelopes         top / rear: per row the outermost run's outer and inner edge and width, photo vs model, rows
                      split by the render hit's station into hinge zone (d < 50) and arm band (60-130)
    front_bound       04: temple pixels of the hinge zone outside the photo silhouette by more than 0.2 mm
    side_outline      01 / 05: the near temple's top / bottom silhouette offset per column (mm)
    compare_temple_views(job, models, views="all") -> report (+ overlays and a six-view sheet when out_dir is given)

Cameras: yaw 0 looks from +Z, +90 from +X (cameras.py); in the rear view the +X temple is on the image left.
"""
from __future__ import annotations

import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage, optimize

from ..g3d.temple import build as TB
from . import cameras as C
from . import fit_views as FV
from . import fitmesh, raster
from . import matte as M
from . import relief_rules as RR
from .camera import Camera, NormFrame

G3D = Path(__file__).resolve().parents[1]
WP6 = G3D / "data" / "wp6"
VIEW_ORDER = ("01", "02", "03", "04", "05", "06")

PRODUCTS = {
    "prada": {"views": {"01": ("reference-01.webp", "side"), "02": ("reference-02.webp", "angled"),
                        "03": ("reference-03.png", "top"), "04": ("reference-04.png", "front"),
                        "05": ("reference-05.png", "side"), "06": ("reference-06.png", "rear")},
              "hinge": {"z_mm": -10.6, "axis_xz_mm": [64.6, -10.1]},
              "skip": FV.PRADA_SKIP, "front_parts": FV.PRADA_FRONT_PARTS,
              "cameras": [WP6 / "wp3b_glossy" / "cameras.json", G3D / "data" / "wp3b" / "prada" / "cameras.json"]},
}


def rot_about_hinge(P: np.ndarray, side: int, theta: float, axis_xz) -> np.ndarray:
    """Open a temple by theta (deg, + = tip outward on both sides) about its vertical hinge axis."""
    if not theta:
        return P
    t = math.radians(theta) * side
    c, s = math.cos(t), math.sin(t)
    x0, z0 = side * axis_xz[0], axis_xz[1]
    dx, dz = P[:, 0] - x0, P[:, 2] - z0
    return np.stack([x0 + c * dx - s * dz, P[:, 1], z0 + s * dx + c * dz], axis=1)


# --------------------------------------------------------------------------------------------- job
class Job:
    def __init__(self, product: str = "prada", cameras_path: str | Path | None = None, matte_mode: str = "glossy_floor"):
        # An explicitly requested basis must not silently fall back to a fixture.
        if cameras_path is not None and not Path(cameras_path).is_file():
            raise FileNotFoundError(f"explicit camera file does not exist: {cameras_path!s}")
        cfg = PRODUCTS[product]
        self.product = product
        self.cfg = cfg
        files = FV.reference_files(product)
        self.glb = files[FV.MODEL]
        self.views = {k: v for k, v in cfg["views"].items()}
        self.photo_paths = {k: files[v[0]] for k, v in self.views.items()}
        self.hinge = dict(cfg["hinge"])
        self.matte_mode = matte_mode
        cams = None
        for p in ([Path(cameras_path)] if cameras_path is not None else cfg["cameras"]):
            if Path(p).exists():
                cams = json.loads(Path(p).read_text(encoding="utf-8"))
                self.cameras_path = Path(p)
                break
        if cams is None:
            raise FileNotFoundError(f"no cameras for {product}")
        self.frame = NormFrame.from_dict(cams["model"]["frame"])
        self.base = {}
        self.view_name = {}
        for k, (name, _) in self.views.items():
            rec = cams["images"][name]
            self.base[k] = Camera.from_dict(rec["camera"])
            self.view_name[k] = rec["view"]
        mesh = fitmesh.load_fit_mesh(self.glb, skip=cfg["skip"], frame=self.frame)
        self.mesh = mesh
        self.pivot = mesh.front_pivot(cfg["front_parts"])
        self.pivot_mm = self.frame.to_mm(self.pivot[None])[0]
        tp = temple_parts(mesh)
        self.temple_part = tp
        keep = ~np.isin(mesh.part, list(tp.values()))
        self.V0, self.F0 = _compact(mesh.V, mesh.F[keep])
        self.delivered_temples = {s: _compact(mesh.V, mesh.F[mesh.part == tp[s]]) for s in (1, -1)}
        self._mattes, self._photos, self._front = {}, {}, {}

    def photo(self, key) -> np.ndarray:
        if key not in self._photos:
            self._photos[key] = M.load_photo(self.photo_paths[key])
        return self._photos[key]

    def matte(self, key) -> np.ndarray:
        if key not in self._mattes:
            self._mattes[key] = M.matte(self.photo(key), mode=self.matte_mode)
        return self._mattes[key]

    def shape(self, key):
        return self.matte(key).shape

    def kind(self, key) -> str:
        return self.views[key][1]

    def front_scene(self):
        return raster.get_scene(self.V0, self.F0, self.frame)

    def hinge_ppm(self, cam: Camera, side: int = 1) -> float:
        p = np.array([side * (self.hinge["axis_xz_mm"][0] + 2.0), 0.0, self.hinge["z_mm"] - 25.0])
        return float(C.px_per_mm_at(cam, self.frame, p))

    def front_ppm(self, cam: Camera) -> float:
        return float(C.px_per_mm_at(cam, self.frame, self.pivot_mm))


def _compact(V, F):
    used = np.unique(F)
    remap = -np.ones(len(V), np.int64)
    remap[used] = np.arange(len(used))
    return V[used].copy(), remap[F]


def temple_parts(mesh) -> dict:
    """{side: part id}: per side the part reaching furthest back (>= 60 mm in z), by the sign of its x."""
    best = {1: (0.0, None), -1: (0.0, None)}
    for pid in range(len(mesh.names)):
        F = mesh.F[mesh.part == pid]
        if len(F) == 0:
            continue
        V = mesh.V[np.unique(F)]
        reach = float(V[:, 2].max() - V[:, 2].min())
        cx = float((V[:, 0].max() + V[:, 0].min()) / 2)
        if reach < 60.0 or abs(cx) < 20.0:
            continue
        s = 1 if cx > 0 else -1
        if reach > best[s][0]:
            best[s] = (reach, pid)
    if best[1][1] is None or best[-1][1] is None:
        raise ValueError("could not find two temples (parts reaching >= 60 mm back on both sides)")
    return {s: best[s][1] for s in (1, -1)}


# --------------------------------------------------------------------------------------------- models
@dataclass
class Model:
    label: str
    temples: dict                    # side -> (V world mm at opening 0, F)
    spec: dict | None = None
    build: object = None
    info: dict = field(default_factory=dict)

    @staticmethod
    def delivered(job: Job, label: str = "delivered") -> "Model":
        return Model(label, {s: job.delivered_temples[s] for s in (1, -1)})

    @staticmethod
    def from_spec(job: Job, spec: dict, label: str) -> "Model":
        tb = TB.build_temple(spec)
        t = {}
        for s in (1, -1):
            W, _, T = tb.world(s, opening_deg=0.0)
            t[s] = (W, T)
        return Model(label, t, spec=spec, build=tb)

    def temple_mesh(self, side: int, theta: float, axis_xz) -> tuple[np.ndarray, np.ndarray]:
        V, F = self.temples[side]
        return rot_about_hinge(V, side, theta, axis_xz), F

    def composite(self, job: Job, theta: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        Vs, Fs, tags, off = [job.V0], [job.F0], [np.zeros(len(job.F0), int)], len(job.V0)
        for s in (1, -1):
            V, F = self.temple_mesh(s, theta.get(s, 0.0), job.hinge["axis_xz_mm"])
            Vs.append(V)
            Fs.append(F + off)
            tags.append(np.full(len(F), s))
            off += len(V)
        return np.vstack(Vs), np.vstack(Fs), np.concatenate(tags)


def _cam_key(cam: Camera) -> bytes:
    """The front-render cache key: the camera's exact float64 fields (it was rounded to 1e-10, which let two distinct
    cameras share a cached render, so a value could depend on which camera a process rendered first: a hole for the
    pooled fit, whose workers each have their own cache)."""
    return np.array([float(v) for v in cam.to_dict().values()], np.float64).tobytes()


def render_model(job: Job, model: Model, key: str, cam: Camera, theta: dict, *, points: bool = True,
                 normals: bool = False) -> dict:
    """Full-image render merged by depth: mask, tag (-2 = miss, 0 = not a temple, +-1 temple side), depth,
    points / normals of the visible surface, d = station of temple hits (nan elsewhere)."""
    shape = job.shape(key)
    ck = (key, _cam_key(cam), points, normals)
    if ck not in job._front:
        if len(job._front) > 24:
            job._front.clear()
        job._front[ck] = job.front_scene().render(cam, shape, 1, want_points=points, want_normal=normals)
    parts = [(0, job._front[ck])]
    for s in (1, -1):
        V, F = model.temple_mesh(s, theta.get(s, 0.0), job.hinge["axis_xz_mm"])
        parts.append((s, raster.get_scene(V, F, job.frame).render(cam, shape, 1, want_points=points,
                                                                   want_normal=normals)))
    depth = np.stack([r["depth"] for _, r in parts])
    win = np.argmin(depth, axis=0)
    hit = np.isfinite(depth.min(axis=0))
    tagv = np.array([t for t, _ in parts])
    tag = np.where(hit, tagv[win], -2)
    out = {"mask": hit, "tag": tag, "depth": depth.min(axis=0)}
    if points:
        P = np.stack([r["points"] for _, r in parts])
        out["points"] = np.take_along_axis(P, win[None, ..., None], 0)[0]
    if normals:
        N = np.stack([r["normal"] for _, r in parts]).astype(np.float64)
        out["normal"] = np.take_along_axis(N, win[None, ..., None], 0)[0]
    out["part_masks"] = {t: r["mask"] for t, r in parts}
    if points:
        d = np.full(shape, np.nan)
        for s in (1, -1):
            sel = tag == s
            if sel.any():
                Q = rot_about_hinge(out["points"][sel], s, -theta.get(s, 0.0), job.hinge["axis_xz_mm"])
                d[sel] = job.hinge["z_mm"] - Q[:, 2]
        out["d"] = d
    return out


def render_mask(job: Job, model: Model, key: str, cam: Camera, theta: dict) -> np.ndarray:
    """render_model(job, model, key, cam, theta, points=False)["mask"], bit for bit, for the joint fit's objective
    (WP6 parallel-fit profiling: generating the temples' rays was most of a render). A pixel of the merged render is
    hit exactly when some part is hit (a hit's depth is finite, a miss's inf), so no depth merge is needed; the front
    part comes from the same per-camera cache as render_model's; each temple is cast with `_scene_mask`."""
    shape = job.shape(key)
    ck = (key, _cam_key(cam), False, False)
    if ck not in job._front:
        if len(job._front) > 24:
            job._front.clear()
        job._front[ck] = job.front_scene().render(cam, shape, 1, want_points=False, want_normal=False)
    m = job._front[ck]["mask"].copy()
    for s in (1, -1):
        V, F = model.temple_mesh(s, theta.get(s, 0.0), job.hinge["axis_xz_mm"])
        m |= _scene_mask(raster.get_scene(V, F, job.frame), cam, shape)
    return m


def render_tags(job: Job, model: Model, key: str, cam: Camera, theta: dict) -> dict:
    """render_model(job, model, key, cam, theta, points=True)'s "mask", "tag" and "d", bit for bit (what the front
    bound reads; WP6 parallel-fit profiling: the full-image merge of three point images was most of the front view's
    cost). Outside the box of the temples' hits every merged pixel is the front part's (tag 0) or a miss (-2), with
    no station; inside it the merge runs exactly as in render_model, per pixel, in the same part order (so a depth
    tie still goes to the front part)."""
    shape = job.shape(key)
    ck = (key, _cam_key(cam), True, False)
    if ck not in job._front:
        if len(job._front) > 24:
            job._front.clear()
        job._front[ck] = job.front_scene().render(cam, shape, 1, want_points=True, want_normal=False)
    parts = [(0, job._front[ck])]
    for s in (1, -1):
        V, F = model.temple_mesh(s, theta.get(s, 0.0), job.hinge["axis_xz_mm"])
        parts.append((s, raster.get_scene(V, F, job.frame).render(cam, shape, 1, want_points=True)))
    temple_hit = parts[1][1]["mask"] | parts[2][1]["mask"]
    out = {"mask": parts[0][1]["mask"] | temple_hit, "tag": np.where(parts[0][1]["mask"], 0, -2),
           "d": np.full(shape, np.nan)}
    box = roi_box(temple_hit)
    if box is None:
        return out
    sl = np.s_[box[0]:box[1], box[2]:box[3]]
    depth = np.stack([r["depth"][sl] for _, r in parts])
    win = np.argmin(depth, axis=0)
    hit = np.isfinite(depth.min(axis=0))
    tagv = np.array([t for t, _ in parts])
    tag = np.where(hit, tagv[win], -2)
    out["tag"][sl] = tag
    P = np.stack([r["points"][sl] for _, r in parts])
    pts = np.take_along_axis(P, win[None, ..., None], 0)[0]
    d = out["d"][sl]
    for s in (1, -1):
        sel = tag == s
        if sel.any():
            Q = rot_about_hinge(pts[sel], s, -theta.get(s, 0.0), job.hinge["axis_xz_mm"])
            d[sel] = job.hinge["z_mm"] - Q[:, 2]
    return out


_RAYS: "OrderedDict" = OrderedDict()      # per process: camera -> image region and its float32 rays
_RAYS_KEEP = 6                            # one camera per view (~15-25 MB each on Prada)
_RAYS_MARGIN_PX = 48


def _scene_mask(scene, cam: Camera, shape) -> np.ndarray:
    """RasterScene.render(cam, shape, 1)["mask"], bit for bit: the same culling box, the same near-plane rule and the
    same rays in the same order, but the rays come from a per-camera cache. raster.camera_rays is elementwise in
    (u, v), so a pixel's float32 ray is the same whether it was generated for this box or for a larger cached
    region."""
    H, W = int(shape[0]), int(shape[1])
    out = np.zeros((H, W), bool)
    us, vs = raster.pixel_grid(shape, 1, None)
    j0, j1, i0, i1 = 0, len(us), 0, len(vs)
    pts = raster._project_norm(scene.hull, cam)
    if pts is not None:
        lo, hi = pts.min(axis=0) - 1e-6, pts.max(axis=0) + 1e-6
        j0, j1 = np.searchsorted(us, lo[0], "left"), np.searchsorted(us, hi[0], "right")
        i0, i1 = np.searchsorted(vs, lo[1], "left"), np.searchsorted(vs, hi[1], "right")
        if j1 <= j0 or i1 <= i0:
            return out
    if cam.perspective * scene.max_radius >= 0.95:                  # RasterScene.cast's near-plane rule
        toward = raster.camera_basis(cam)[2]
        if np.any(1.0 - cam.perspective * (scene.hull @ toward) <= 0.05):
            raise ValueError("Geometry crosses the camera near plane")
    i0, i1, j0, j1 = int(i0), int(i1), int(j0), int(j1)
    rays = _region_rays(cam, (H, W), us, vs, scene.z0, i0, i1, j0, j1)
    ans = scene._scene.cast_rays(raster.o3d.core.Tensor(rays))
    prim = ans["primitive_ids"].numpy()
    t = ans["t_hit"].numpy().astype(np.float64)
    out[i0:i1, j0:j1] = ((prim != raster.INVALID) & np.isfinite(t)).reshape(i1 - i0, j1 - j0)
    return out


def _region_rays(cam: Camera, shape, us, vs, z0: float, i0: int, i1: int, j0: int, j1: int) -> np.ndarray:
    """(N, 6) float32 rays of the pixels [i0:i1, j0:j1], row-major (RasterScene.render's order), from the cache; a
    region not covered is regenerated as the union with the cached one, grown by _RAYS_MARGIN_PX."""
    key = (np.array([getattr(cam, f) for f in ("yaw", "pitch", "roll", "perspective", "scale", "center_x",
                                                 "center_y")] + [float(z0)], np.float64).tobytes(), shape)
    ent = _RAYS.get(key)
    if ent is not None and ent[0] <= i0 and i1 <= ent[1] and ent[2] <= j0 and j1 <= ent[3]:
        _RAYS.move_to_end(key)
    else:
        H, W = shape
        r0, r1, c0, c1 = (i0, i1, j0, j1) if ent is None else (min(i0, ent[0]), max(i1, ent[1]), min(j0, ent[2]),
                                                               max(j1, ent[3]))
        r0, r1 = max(0, r0 - _RAYS_MARGIN_PX), min(H, r1 + _RAYS_MARGIN_PX)
        c0, c1 = max(0, c0 - _RAYS_MARGIN_PX), min(W, c1 + _RAYS_MARGIN_PX)
        uu, vv = np.meshgrid(us[c0:c1], vs[r0:r1])
        o, d = raster.camera_rays(cam, uu.ravel(), vv.ravel(), z0)
        ent = (r0, r1, c0, c1, np.concatenate([o, d], axis=1).astype(np.float32).reshape(r1 - r0, c1 - c0, 6))
        _RAYS[key] = ent
        while len(_RAYS) > _RAYS_KEEP:
            _RAYS.popitem(last=False)
    r0, _, c0, _, R = ent
    return np.ascontiguousarray(R[i0 - r0:i1 - r0, j0 - c0:j1 - c0]).reshape(-1, 6)


# --------------------------------------------------------------------------------------------- metrics
def _edge(mask):
    return mask & ~ndimage.binary_erosion(mask, border_value=0)


def _dilate(mask, px):
    if px <= 0:
        return mask.copy()
    k = 2 * int(px) + 1
    return cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))).astype(bool)


def temple_roi(job: Job, key: str, renders: list) -> np.ndarray:
    """renders = [render_model results of every compared model at its own camera / theta family]."""
    fg = job.matte(key)
    union_t = np.zeros(fg.shape, bool)
    nontemple = np.zeros(fg.shape, bool)
    for r in renders:
        union_t |= r["mask"] & (np.abs(r["tag"]) == 1)
        nontemple |= r["mask"] & (r["tag"] == 0)
    return _dilate(union_t, RR.ROI_DILATE_MODEL_PX) | _dilate(fg & ~_dilate(nontemple, RR.ROI_NONTEMPLE_PX),
                                                              RR.ROI_DILATE_MATTE_PX)


def roi_box(roi: np.ndarray, grow: int = 0) -> tuple | None:
    """(y0, y1, x0, x1), half-open, of the ROI's True pixels grown by `grow` px and clipped to the image; None when
    the ROI is empty."""
    rows, cols = np.flatnonzero(roi.any(axis=1)), np.flatnonzero(roi.any(axis=0))
    if not len(rows):
        return None
    H, W = roi.shape
    return (max(0, int(rows[0]) - grow), min(H, int(rows[-1]) + 1 + grow),
            max(0, int(cols[0]) - grow), min(W, int(cols[-1]) + 1 + grow))


ROI_BOX_GROW_PX = RR.CONTOUR_TRUNC_PX + 2


def roi_metrics(fg: np.ndarray, mask: np.ndarray, roi: np.ndarray, ppm: float) -> dict:
    """`roi_metrics_full`, bit for bit, at a fraction of the cost (WP6 parallel-fit profiling: the two full-image
    distance transforms were half of an objective evaluation).
    1. Crop to the ROI's bounding box grown by CONTOUR_TRUNC_PX + 2 px. Every distance is truncated at
       CONTOUR_TRUNC_PX, so an edge pixel that can set a truncated distance at an ROI pixel lies >= 2 px inside the box
       (its 4-neighbourhood, which `_edge`'s erosion reads, is the image's); the crop changes only box-border pixels
       (eroded against the border value), >= 62 px from every ROI pixel: they can only offer distances above the
       truncation, and they are outside the ROI themselves.
    2. Nearest edge pixel by a k-d tree instead of the distance transform: the distance is then formed exactly as
       scipy's `distance_transform_edt` forms it from its (exact) feature transform, sqrt(float64(dy^2 + dx^2)) of the
       integer offsets, so ties between equally near pixels give the same value.
    The ROI's edge pixels keep their row-major order, so the means sum the same values in the same order
    (tests/test_temple_fit_parallel.py compares both formulas)."""
    box = roi_box(roi, ROI_BOX_GROW_PX)
    if box is None:
        return roi_metrics_full(fg, mask, roi, ppm)
    sl = np.s_[box[0]:box[1], box[2]:box[3]]
    fg, mask, roi = fg[sl], mask[sl], roi[sl]
    a, b = fg & roi, mask & roi
    inter, union = int((a & b).sum()), int((a | b).sum())
    e_fg, e_md = _edge(fg), _edge(mask)
    ea, eb = e_fg & roi, e_md & roi
    if not ea.any() or not eb.any():
        return {"iou": inter / max(union, 1), "contour_mm": float("inf"), "photo_to_model_mm": float("inf"),
                "model_to_photo_mm": float("inf")}
    da = np.minimum(_nearest_edge_px(ea, e_md), RR.CONTOUR_TRUNC_PX)
    db = np.minimum(_nearest_edge_px(eb, e_fg), RR.CONTOUR_TRUNC_PX)
    return {"iou": inter / max(union, 1), "contour_mm": float((da.mean() + db.mean()) / 2 / ppm),
            "photo_to_model_mm": float(da.mean() / ppm), "model_to_photo_mm": float(db.mean() / ppm)}


def _nearest_edge_px(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """For each True pixel of `src` (row-major order): the distance to the nearest True pixel of `dst` (non-empty),
    as distance_transform_edt(~dst)[src] computes it."""
    from scipy.spatial import cKDTree
    q, t = np.argwhere(src), np.argwhere(dst)
    _, idx = cKDTree(t).query(q, k=1)
    dv = (t[idx] - q).astype(np.float64)
    dv *= dv
    return np.sqrt(dv[:, 0] + dv[:, 1])


def roi_metrics_full(fg: np.ndarray, mask: np.ndarray, roi: np.ndarray, ppm: float) -> dict:
    a, b = fg & roi, mask & roi
    inter, union = int((a & b).sum()), int((a | b).sum())
    ea, eb = _edge(fg) & roi, _edge(mask) & roi
    if not ea.any() or not eb.any():
        return {"iou": inter / max(union, 1), "contour_mm": float("inf"), "photo_to_model_mm": float("inf"),
                "model_to_photo_mm": float("inf")}
    da = np.minimum(ndimage.distance_transform_edt(~_edge(mask))[ea], RR.CONTOUR_TRUNC_PX)
    db = np.minimum(ndimage.distance_transform_edt(~_edge(fg))[eb], RR.CONTOUR_TRUNC_PX)
    return {"iou": inter / max(union, 1), "contour_mm": float((da.mean() + db.mean()) / 2 / ppm),
            "photo_to_model_mm": float(da.mean() / ppm), "model_to_photo_mm": float(db.mean() / ppm)}


def front_contour_mm(job: Job, key: str, r: dict, cam: Camera) -> float:
    """Contour distance of the non-temple parts: inside dilate8(non-temple render) minus dilate6(temple render)."""
    fg = job.matte(key)
    front = r["mask"] & (r["tag"] == 0)
    roi = _dilate(front, 8) & ~_dilate(r["mask"] & (np.abs(r["tag"]) == 1), 6)
    m = roi_metrics(fg, r["mask"], roi, job.front_ppm(cam))
    return m["contour_mm"]


def whole_iou(fg, mask) -> float:
    return float((fg & mask).sum() / max((fg | mask).sum(), 1))


# --------------------------------------------------------------------------------------------- theta
def fit_theta(job: Job, model: Model, key: str, cam: Camera, theta0: dict | None = None,
              sides=(1, -1)) -> dict:
    """{side: {theta, loss, grid, observed}} per temple side: minimise 1 - IoU of the photo matte and whole-scene render inside
    the side's temple ROI (union of the side's renders over the grid, minus the non-temple parts), 1 deg grid then
    bounded Brent. A flat objective (range < 0.002) is UNOBSERVED and keeps theta0."""
    fg = job.matte(key)
    th = dict(theta0 or {1: 0.0, -1: 0.0})
    out = {}
    grid = np.arange(RR.THETA_RANGE_DEG[0], RR.THETA_RANGE_DEG[1] + 1e-9, RR.THETA_GRID_DEG)
    base = render_model(job, model, key, cam, th, points=False)
    nontemple = _dilate(base["mask"] & (base["tag"] == 0), RR.ROI_NONTEMPLE_PX)
    for s in sides:
        V0, F = model.temples[s]
        masks = []
        for t in grid:
            V = rot_about_hinge(V0, s, float(t), job.hinge["axis_xz_mm"])
            masks.append(raster.get_scene(V, F, job.frame).render(cam, fg.shape, 1)["mask"])
        roi = _dilate(np.any(masks, axis=0), RR.ROI_DILATE_MODEL_PX) & ~nontemple
        # The photo contains every part. An isolated arm can move entirely behind
        # a fixed part without changing the visible silhouette; that motion must
        # not create angle evidence. Hold the other components at their input pose.
        fixed_mask = base["part_masks"][0] | base["part_masks"][-s]

        def loss_of(mask_s):
            a, b = fg & roi, (mask_s | fixed_mask) & roi
            return 1.0 - (a & b).sum() / max((a | b).sum(), 1)
        gl = np.array([loss_of(m_) for m_ in masks])
        if gl.max() - gl.min() < 0.002:
            out[s] = {"theta": float(th.get(s, 0.0)), "loss": float(gl.min()), "observed": False,
                      "grid_range": float(gl.max() - gl.min())}
            continue
        i = int(np.argmin(gl))
        lo, hi = max(grid[0], grid[i] - RR.THETA_GRID_DEG), min(grid[-1], grid[i] + RR.THETA_GRID_DEG)

        def f(t):
            V = rot_about_hinge(V0, s, float(t), job.hinge["axis_xz_mm"])
            return loss_of(raster.get_scene(V, F, job.frame).render(cam, fg.shape, 1)["mask"])
        res = optimize.minimize_scalar(f, bounds=(lo, hi), method="bounded", options={"xatol": RR.THETA_XTOL_DEG})
        t_best, l_best = (float(res.x), float(res.fun)) if res.fun <= gl[i] else (float(grid[i]), float(gl[i]))
        out[s] = {"theta": t_best, "loss": l_best, "observed": True, "grid_range": float(gl.max() - gl.min()),
                  "grid_best": float(grid[i])}
    return out


# --------------------------------------------------------------------------------------------- cameras
def _box(cam: Camera, unit: float, pivot, b: dict, pitch_win=None, persp_win=None):
    x = C.pack(cam, unit, pivot)
    pw = pitch_win or (x[1] - b["pitch"], x[1] + b["pitch"])
    pp = persp_win or (max(0.0, cam.perspective - b["persp"]), min(C.PERSPECTIVE_BOUNDS[1], cam.perspective + b["persp"]))
    sh = 100.0 * b["shift_px"] / unit
    sc = 100.0 * math.log(1.0 + b["scale_pct"] / 100.0)
    return [(x[0] - b["yaw"], x[0] + b["yaw"]), (max(pw[0], x[1] - b["pitch"]) if pitch_win is None else pw[0],
                                                 min(pw[1], x[1] + b["pitch"]) if pitch_win is None else pw[1]),
            (x[2] - b["roll"], x[2] + b["roll"]), (10 * pp[0], 10 * pp[1]),
            (x[4] - sc, x[4] + sc), (x[5] - sh, x[5] + sh), (x[6] - sh, x[6] + sh)]


def refit_camera(job: Job, model: Model, key: str, cam0: Camera, theta: dict, *, bounds: dict | None = None,
                 pitch_win=None, persp_win=None, maxfev: dict | None = None) -> tuple[Camera, dict]:
    """Bounded staged Powell (fit level, then final level) inside a fixed box around cam0; reverted to cam0 when the
    front-part contour degrades by more than FRONT_DEGRADE_MAX_MM."""
    t0 = time.perf_counter()
    b = dict(RR.REFIT_BOUNDS if bounds is None else bounds)
    mf = dict(RR.REFIT_MAXFEV if maxfev is None else maxfev)
    fg = job.matte(key)
    V, F, _ = model.composite(job, theta)
    scene = raster.get_scene(V, F, job.frame)
    vf = C.ViewFit(job.view_name[key], fg, scene, pivot=job.pivot)
    box = _box(cam0, vf.unit, job.pivot, b, pitch_win, persp_win)
    start = C.unpack(C._clip_into(C.pack(cam0, vf.unit, job.pivot), box), vf.unit, job.pivot)
    lv = vf.levels["fit"]
    c1, l1, n1 = C.powell(scene, lv, start, box, mf["fit"], vf.unit, xtol=0.02, pivot=job.pivot)
    pts = C.project(scene.hull, c1)
    lvF = C.Level(fg, C.FINAL_W, "final", margin=C.FINAL_ROI_MARGIN, include_xyxy=(*pts.min(axis=0), *pts.max(axis=0)))
    c2, l2, n2 = C.powell(scene, lvF, c1, box, mf["final"], vf.unit, xtol=0.02, pivot=job.pivot)
    l0 = lvF.loss(scene, start)
    r0 = render_model(job, model, key, start, theta, points=False)
    r2 = render_model(job, model, key, c2, theta, points=False)
    f0, f2 = front_contour_mm(job, key, r0, start), front_contour_mm(job, key, r2, c2)
    guard_measured = bool(np.isfinite(f0) and np.isfinite(f2))
    kept = guard_measured and f2 <= f0 + RR.FRONT_DEGRADE_MAX_MM
    at_bound = []
    x2 = C.pack(c2, vf.unit, job.pivot)
    for i, (nm, (lo, hi)) in enumerate(zip(("yaw", "pitch", "roll", "persp", "scale", "shift_x", "shift_y"), box)):
        if min(x2[i] - lo, hi - x2[i]) < 1e-3 * max(hi - lo, 1e-9):
            at_bound.append(nm)
    info = {"loss_start": float(l0), "loss": float(l2 if kept else l0), "front_contour_start_mm": f0,
            "front_contour_mm": f2 if kept else f0, "front_guard_measured": guard_measured,
            "kept": bool(kept), "evals": n1 + n2, "at_bound": at_bound,
            "seconds": time.perf_counter() - t0}
    return (c2 if kept else start), info


# --------------------------------------------------------------------------------------------- crease views
CREASE_VIEWS = (("05", 1), ("01", -1), ("02", 1))     # the crease views and their near temple (05 +X, 01 -X, 02 +X)


def crease_fit(fits: dict, key: str) -> dict:
    """The camera and theta a crease measurement reads in view `key` (R2, R3, the fit's crease terms): the view's own
    refit camera and theta. A side view carries theta 0 for both temples (fit_model_view: WP6_DESIGN §6 scores theta
    on the 02 / 03 / 06 / 04 arm rows; a side view sees a hinge rotation only as an image tilt that its camera roll
    reproduces), so R2 reads no hinge angle; R3 reads the angled view's."""
    f = fits[key]
    return {"camera": f["camera"], "theta": dict(f["theta"])}


def turntable_seed(job: Job, cam_front: Camera) -> Camera:
    """A rear seed that mirrors the front photo on a turntable: the front camera's pitch, roll and perspective with
    yaw + 180, scaled and centred on the rear matte by cameras.align."""
    fg = job.matte("06")
    V, F, _ = Model.delivered(job).composite(job, {})
    scene = raster.get_scene(V, F, job.frame)
    lv = C.Level(fg, C.FIT_W, "fit")
    return C.align(scene, lv, (cam_front.yaw + 180.0) % 360.0, cam_front.pitch, cam_front.roll,
                   cam_front.perspective)


REAR_BOUNDS = {"yaw": 4.0, "pitch": 14.0, "roll": 3.0, "scale_pct": 20.0, "shift_px": 40.0, "persp": 0.4}


REAR_REFIT = {"bounds": REAR_BOUNDS, "pitch_win": RR.REAR_PITCH_DEG, "persp_win": RR.REAR_PERSPECTIVE,
              "maxfev": {"fit": 300, "final": 160}}


def rear_family(job: Job, model: Model, seeds: list, alphas=RR.REAR_ALPHAS_DEG, refit_many=None) -> dict:
    """06: one camera per symmetric opening alpha (pitch in REAR_PITCH_DEG, perspective in REAR_PERSPECTIVE), from the
    best of the seeds; the family = every alpha with loss <= REAR_FAMILY_RATIO x the minimum. `refit_many(calls)`
    (relief_fit.FitPool) runs the alpha x seed refits [(key, seed, theta, kwargs)] in parallel and returns their
    (camera, info) in call order; the best seed per alpha is then chosen exactly as in the serial loop."""
    calls = [("06", seed, {1: float(a), -1: float(a)}, REAR_REFIT) for a in alphas for seed in seeds]
    if refit_many is None:
        done = [refit_camera(job, model, k, seed, th, **kw) for k, seed, th, kw in calls]
    else:
        done = refit_many(calls)
    rows = []
    for i, a in enumerate(alphas):
        th = {1: float(a), -1: float(a)}
        best = None
        for j in range(len(seeds)):
            cam, info = done[i * len(seeds) + j]
            if best is None or info["loss"] < best[1]["loss"]:
                best = (cam, info)
        cam, info = best
        r = render_model(job, model, "06", cam, th, points=False)
        guard = front_contour_mm(job, "06", r, cam)
        rows.append({"alpha": float(a), "camera": cam.to_dict(), "loss": info["loss"], "guard_mm": guard,
                     "pitch_at_bound": bool(min(abs(cam.pitch - RR.REAR_PITCH_DEG[0]),
                                                abs(cam.pitch - RR.REAR_PITCH_DEG[1])) < 0.05),
                     "refit": info})
    lmin = min(r["loss"] for r in rows)
    fam = [r for r in rows if r["loss"] <= RR.REAR_FAMILY_RATIO * lmin]
    stop_r = all(r["pitch_at_bound"] or r["guard_mm"] > RR.STOP_R_GUARD_MM for r in fam)
    return {"rows": rows, "family": [r["alpha"] for r in fam], "loss_min": lmin, "stop_r": stop_r}


# --------------------------------------------------------------------------------------------- envelopes, outline, bound
def _runs(row):
    c = np.r_[False, row, False].astype(np.int8)
    d = np.diff(c)
    return np.nonzero(d == 1)[0], np.nonzero(d == -1)[0] - 1


def _outer_run(row, left_half: bool, W: int):
    st, en = _runs(row)
    if len(st) == 0:
        return None
    if left_half:
        sel = np.nonzero(st < W // 2)[0]
        return None if not len(sel) else (int(st[sel[0]]), int(en[sel[0]]))
    sel = np.nonzero(en >= W // 2)[0]
    return None if not len(sel) else (int(en[sel[-1]]), int(st[sel[-1]]))


def image_side(job: Job, key: str, cam: Camera, side: int) -> bool:
    """True when the temple `side` appears on the image's left half."""
    p = np.array([[side * 70.0, 0.0, job.hinge["z_mm"] - 40.0]])
    u = C.project(job.frame.to_norm(p), cam)[0, 0]
    return bool(u < job.shape(key)[1] / 2)


def envelopes(job: Job, key: str, r: dict, cam: Camera) -> dict:
    """Per side: rows whose outermost render run is that temple; photo - model outer / inner edge and width (mm,
    + = photo further out / wider), split by the render hit's station."""
    fg = job.matte(key)
    H, W = fg.shape
    out = {}
    for s in (1, -1):
        left = image_side(job, key, cam, s)
        sgn = -1 if left else 1
        rows = []
        for v in range(H):
            md = _outer_run(r["mask"][v], left, W)
            if md is None or r["tag"][v, md[0]] != s:
                continue
            ph = _outer_run(fg[v], left, W)
            if ph is None:
                continue
            p = r["points"][v, md[0]]
            k = float(C.px_per_mm_at(cam, job.frame, p))
            d = r["d"][v, md[0]]
            rows.append((v, d, sgn * (ph[0] - md[0]) / k, sgn * (ph[1] - md[1]) / k,
                         abs(ph[0] - ph[1]) / k, abs(md[0] - md[1]) / k))
        A = np.array(rows) if rows else np.zeros((0, 6))
        res = {"rows": int(len(A))}
        for zone, (d0, d1) in (("hinge", RR.HINGE_ZONE_D), ("arm", RR.ARM_BAND_D)):
            sel = (A[:, 1] >= d0) & (A[:, 1] < d1) if len(A) else np.zeros(0, bool)
            if sel.sum() < 3:
                res[zone] = None
                continue
            Z = A[sel]
            res[zone] = {"n": int(sel.sum()), "outer_signed_median_mm": float(np.median(Z[:, 2])),
                         "outer_abs_median_mm": float(np.median(np.abs(Z[:, 2]))),
                         "inner_signed_median_mm": float(np.median(Z[:, 3])),
                         "inner_abs_median_mm": float(np.median(np.abs(Z[:, 3]))),
                         "width_abs_median_mm": float(np.median(np.abs(Z[:, 4] - Z[:, 5]))),
                         "width_photo_median_mm": float(np.median(Z[:, 4])),
                         "width_model_median_mm": float(np.median(Z[:, 5]))}
        out[s] = res
    return out


def side_outline(job: Job, key: str, r: dict, cam: Camera, near: int, search_px: int = 60) -> dict:
    """The near temple's top / bottom silhouette offset per image column (mm, + = photo beyond the model), rows of
    the column where the near temple is the silhouette."""
    fg = job.matte(key)
    H, W = fg.shape
    nm = r["tag"] == near
    res = {"top": [], "bottom": []}
    for u in np.nonzero(nm.any(axis=0))[0]:
        rows = np.nonzero(nm[:, u])[0]
        for edge, rr in (("top", rows.min()), ("bottom", rows.max())):
            if edge == "top" and rr > 0 and r["mask"][rr - 1, u]:
                continue
            if edge == "bottom" and rr < H - 1 and r["mask"][rr + 1, u]:
                continue
            st, en = _runs(fg[:, u])
            cands = st if edge == "top" else en
            if not len(cands):
                continue
            j = int(np.argmin(np.abs(cands - rr)))
            if abs(cands[j] - rr) > search_px:
                continue
            p = r["points"][rr, u]
            k = float(C.px_per_mm_at(cam, job.frame, p))
            sgn = -1 if edge == "top" else 1
            res[edge].append((float(r["d"][rr, u]), sgn * (cands[j] - rr) / k))
    out = {}
    for e, L in res.items():
        A = np.array(L) if L else np.zeros((0, 2))
        out[e] = {"n": int(len(A)), "mean_abs_mm": float(np.mean(np.abs(A[:, 1]))) if len(A) else None,
                  "p90_abs_mm": float(np.percentile(np.abs(A[:, 1]), 90)) if len(A) else None,
                  "samples": A.tolist()}
    return out


def outside_px(job: Job, key: str) -> np.ndarray:
    """distance_transform_edt(~matte) of a view (px from the photo silhouette), computed once per job: a pure
    function of the matte, which the front bound read anew at every objective evaluation."""
    cache = job.__dict__.setdefault("_outside_px", {})
    fg = job.matte(key)
    ent = cache.get(key)
    if ent is None or ent[0] is not fg:
        ent = (fg, ndimage.distance_transform_edt(~fg))
        cache[key] = ent
    return ent[1]


def front_bound(job: Job, r: dict, cam: Camera, zone_end: float = RR.HINGE_ZONE_D[1]) -> dict:
    """04: temple pixels outside the photo silhouette by more than FRONT_BOUND_TOL_MM (hinge zone d < zone_end
    reported apart from arm tops)."""
    fg = job.matte("04")
    out_px = outside_px(job, "04")
    k = job.front_ppm(cam)
    t = (np.abs(r["tag"]) == 1) & r["mask"]
    over = t & (out_px > RR.FRONT_BOUND_TOL_MM * k)
    hz = over & (r["d"] < zone_end)
    arm = over & ~(r["d"] < zone_end)
    return {"clear": not bool(hz.any()), "hinge_zone_mm2": float(hz.sum() / k ** 2),
            "arm_tops_mm2": float(arm.sum() / k ** 2),
            "hinge_zone_max_mm": float(out_px[hz].max() / k) if hz.any() else 0.0,
            "temple_outside_mm2": float((t & ~fg).sum() / k ** 2)}


# --------------------------------------------------------------------------------------------- comparison
def score_view(job: Job, model: Model, key: str, cam: Camera, theta: dict, roi: np.ndarray | None) -> dict:
    r = render_model(job, model, key, cam, theta, points=True)
    fg = job.matte(key)
    out = {"camera": cam.to_dict(), "theta": {str(s): float(theta.get(s, 0.0)) for s in (1, -1)},
           "whole_iou": whole_iou(fg, r["mask"]), "front_contour_mm": front_contour_mm(job, key, r, cam)}
    if roi is not None:
        out["roi"] = roi_metrics(fg, r["mask"], roi, job.hinge_ppm(cam))
    kind = job.kind(key)
    if kind in ("top", "rear"):
        env = envelopes(job, key, r, cam)
        out["envelopes"] = {str(s): v for s, v in env.items()}
    if kind == "front":
        out["front_bound"] = front_bound(job, r, cam)
    if kind == "side":
        near = 1 if math.sin(math.radians(cam.yaw)) > 0 else -1
        out["outline"] = {k_: {kk: vv for kk, vv in v.items() if kk != "samples"}
                          for k_, v in side_outline(job, key, r, cam, near).items()}
        out["near_side"] = near
    return out, r


SIDE_THETA_WHY = ("not fitted in a side view (fit-4; WP6_DESIGN §6 scores theta on the 02 / 03 / 06 / 04 arm rows): "
                  "a hinge rotation moves a temple along a side view's line of sight, so its only image is a tilt of "
                  "sin(pitch) x theta (~0.23 theta at Prada's 13.5 deg pitch) that the camera roll reproduces; the S1 "
                  "round trip traded them (05: theta -2.57 deg against roll -0.61 deg) and could not recover either")


def _initial_view_fit(job: Job, model: Model, key: str, cam0: Camera) -> dict:
    """theta at the base camera -> camera refit with theta fixed -> theta again (the §6 block coordinates, one round).
    A side view keeps theta 0 for both temples and refits only its camera (SIDE_THETA_WHY)."""
    if job.kind(key) == "side":
        th = {1: 0.0, -1: 0.0}
        cam, info = refit_camera(job, model, key, cam0, th)
        tf = {"theta": 0.0, "loss": None, "observed": False, "why": SIDE_THETA_WHY}
        return {"camera": cam, "theta": th, "theta_fit": {"1": dict(tf), "-1": dict(tf)}, "refit": info}
    t1 = fit_theta(job, model, key, cam0)
    th = {s: t1[s]["theta"] for s in (1, -1)}
    cam, info = refit_camera(job, model, key, cam0, th)
    t2 = fit_theta(job, model, key, cam, th)
    th = {s: t2[s]["theta"] for s in (1, -1)}
    return {"camera": cam, "theta": th, "theta_fit": {str(s): t2[s] for s in (1, -1)}, "refit": info}


def fit_model_view(job: Job, model: Model, key: str, cam0: Camera | None = None) -> dict:
    """Initial block coordinates, then bounded joint silhouette alignment (fit-6).

    Both steps use the original camera box; side and unobserved hinges stay fixed.
    Rear-family alpha rows still use refit_camera directly with their fixed theta.
    """
    from .relief_registration import align_view
    cam0 = cam0 or job.base[key]
    return align_view(job, model, key, _initial_view_fit(job, model, key, cam0), cam0)


def compare_temple_views(job: Job, models: dict, views="all", *, out_dir: str | Path | None = None,
                         fits: dict | None = None, rear: bool = True, cross_pair: tuple | None = None,
                         log=print) -> dict:
    """Every model at its own refit cameras and theta_k, one ROI per view; the rear family per model; the 2x2 cross
    check for `cross_pair` = (candidate label, delivered label). `fits` caches {label: {key: fit}}; a cached "06_family"
    must be the rear family OF THAT MODEL'S SHAPE (relief_fit.fit_temple's final camera block computes it on the
    final shape), and a cached "06" fit is kept as given (only a model without one gets the family's best camera and
    its fit_theta, which is what relief_fit.refit_views records as the 06 fit)."""
    keys = list(VIEW_ORDER) if views == "all" else list(views)
    fits = fits if fits is not None else {}
    rep = {"views": {}, "models": list(models), "rules": RR.RULES_VERSION}
    for label, model in models.items():
        fits.setdefault(label, {})
        for k in keys:
            if k == "06" and rear:
                continue
            if k not in fits[label]:
                t0 = time.perf_counter()
                fits[label][k] = fit_model_view(job, model, k)
                log(f"  [{label}] view {k}: refit {time.perf_counter() - t0:.0f} s, theta "
                    f"{fits[label][k]['theta']}, kept {fits[label][k]['refit']['kept']}")
    families = {}
    if rear and "06" in keys:
        seeds = [job.base["06"], turntable_seed(job, job.base["04"])]
        for label, model in models.items():
            if "06_family" not in fits[label]:
                t0 = time.perf_counter()
                fits[label]["06_family"] = rear_family(job, model, seeds)
                log(f"  [{label}] rear family {fits[label]['06_family']['family']} "
                    f"({time.perf_counter() - t0:.0f} s)")
            fam = fits[label]["06_family"]
            families[label] = fam
            if (fits[label].get("06") or {}).get("camera") is None:
                best = min(fam["rows"], key=lambda r_: r_["loss"])
                cam = Camera.from_dict(best["camera"])
                th = fit_theta(job, model, "06", cam, {1: best["alpha"], -1: best["alpha"]})
                fits[label]["06"] = {"camera": cam, "theta": {s: th[s]["theta"] for s in (1, -1)},
                                     "theta_fit": {str(s): th[s] for s in (1, -1)}, "refit": best["refit"],
                                     "alpha": best["alpha"]}
    for k in keys:
        renders = {}
        for label, model in models.items():
            f = fits[label][k]
            renders[label] = render_model(job, model, k, f["camera"], f["theta"], points=False)
        extra = []
        if k == "06" and families:
            for label, model in models.items():
                for row in families[label]["rows"]:
                    if row["alpha"] in families[label]["family"]:
                        extra.append(render_model(job, model, k, Camera.from_dict(row["camera"]),
                                                  {1: row["alpha"], -1: row["alpha"]}, points=False))
        roi = temple_roi(job, k, list(renders.values()) + extra)
        vrep = {"models": {}}
        for label, model in models.items():
            f = fits[label][k]
            sc, _ = score_view(job, model, k, f["camera"], f["theta"], roi)
            sc["refit"] = f.get("refit")
            sc["theta_fit"] = f.get("theta_fit")
            for field in ("alignment", "initial_refit", "initial_theta_fit"):
                if field in f:
                    sc[field] = f[field]
            vrep["models"][label] = sc
        if k == "06" and families:
            vrep["family"] = {}
            for label, model in models.items():
                rows = []
                for row in families[label]["rows"]:
                    if row["alpha"] not in families[label]["family"]:
                        continue
                    cam = Camera.from_dict(row["camera"])
                    th = fit_theta(job, model, k, cam, {1: row["alpha"], -1: row["alpha"]})
                    thd = {s: th[s]["theta"] for s in (1, -1)}
                    sc, _ = score_view(job, model, k, cam, thd, roi)
                    rows.append({"alpha": row["alpha"], "guard_mm": row["guard_mm"],
                                 "pitch_at_bound": row["pitch_at_bound"], **sc})
                vrep["family"][label] = {"rows": rows, "alphas": families[label]["family"],
                                         "stop_r": families[label]["stop_r"]}
        if cross_pair and all(c in models for c in cross_pair):
            a, b = cross_pair
            cross = {}
            for x, y in ((a, b), (b, a)):
                cam = fits[y][k]["camera"]
                if job.kind(k) == "side":                   # no theta in a side view (SIDE_THETA_WHY)
                    thd = {1: 0.0, -1: 0.0}
                else:
                    th = fit_theta(job, models[x], k, cam, fits[x][k]["theta"])
                    thd = {s: th[s]["theta"] for s in (1, -1)}
                sc, _ = score_view(job, models[x], k, cam, thd, roi)
                cross[f"{x}@{y}"] = sc
            vrep["cross"] = cross
        vrep["roi_px"] = int(roi.sum())
        rep["views"][k] = vrep
        if out_dir is not None:
            _overlays(job, k, models, fits, roi, Path(out_dir))
    if out_dir is not None:
        sheet(job, models, fits, keys, Path(out_dir) / "six_view_sheet.png")
    return rep


# --------------------------------------------------------------------------------------------- sheets
def _crop_box(mask, pad=30):
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return 0, 0, mask.shape[1], mask.shape[0]
    return (max(0, xs.min() - pad), max(0, ys.min() - pad), min(mask.shape[1], xs.max() + pad),
            min(mask.shape[0], ys.max() + pad))


def overlay(job: Job, key: str, mask: np.ndarray, roi: np.ndarray | None = None) -> np.ndarray:
    img = job.photo(key).copy()
    fg = job.matte(key)
    e_ph = _dilate(_edge(fg), 1)
    e_md = _dilate(_edge(mask), 1)
    img[e_ph] = (0, 200, 0)
    img[e_md] = (255, 0, 255)
    if roi is not None:
        img[_edge(roi)] = (255, 170, 0)
    return img


def _overlays(job, key, models, fits, roi, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for label, model in models.items():
        f = fits[label][key]
        r = render_model(job, model, key, f["camera"], f["theta"], points=False)
        x0, y0, x1, y1 = _crop_box(roi)
        Image.fromarray(overlay(job, key, r["mask"], roi)[y0:y1, x0:x1]).save(
            out_dir / f"overlay_{key}_{_safe(label)}.png")


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def sheet(job, models, fits, keys, path: Path, width: int = 520):
    """Six-view sheet: one row per view: the photo crop, then each model's overlay (matte edge green, model edge
    magenta). Labelled as a TOOL TEST image when a model label says so."""
    rows = []
    for k in keys:
        tiles = []
        crop = None
        for label, model in models.items():
            f = fits[label][k]
            r = render_model(job, model, k, f["camera"], f["theta"], points=False)
            if crop is None:
                crop = _crop_box(job.matte(k) | r["mask"])
            x0, y0, x1, y1 = crop
            tiles.append(overlay(job, k, r["mask"])[y0:y1, x0:x1])
        x0, y0, x1, y1 = crop
        tiles.insert(0, job.photo(k)[y0:y1, x0:x1])
        h = int(round(width * (y1 - y0) / max(x1 - x0, 1)))
        rows.append(np.hstack([cv2.resize(t, (width, h), interpolation=cv2.INTER_AREA) for t in tiles]))
    W = max(r.shape[1] for r in rows)
    canvas = np.vstack([np.pad(r, ((0, 6), (0, W - r.shape[1]), (0, 0)), constant_values=255) for r in rows])
    header = np.full((40, W, 3), 255, np.uint8)
    labels = ["photo"] + list(models)
    for i, lab in enumerate(labels):
        cv2.putText(header, lab[:48], (10 + i * width, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1, cv2.LINE_AA)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.vstack([header, canvas])).save(path)
    return path
