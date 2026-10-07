"""Camera bases and the comparison of a model revision with the reference photos (WP2_DESIGN §6), host side.

Basis (``cameras/basis-K/`` in a job): the WP3b ``cameras.json`` of one fit (``fit_views.fit_views``) or an imported
one, frozen together with what every later comparison must reuse unchanged (the GEOMETRY_COMPARISON frozen-baseline
lesson): the NormFrame, the front pivot, the skip list, the front parts, each reference's camera, native shape and
metrics ROI, the photo mattes (computed once here, ``mattes/<ref>.png``) and the fit's overlays. ``basis.json``
(``g3d.basis/1``) records {id, k, reason, call_id, source, model {glb sha256, fingerprint, checkpoint}, frame,
front_pivot_mm, skip, front_parts, refs {reference-NN: {image, photo, photo_sha256, shape, view_kind, view, camera,
roi, matte, matte_sha256, fg_px, fit {status, iou, contour_mean_px, contour_mean_mm, contour_p90_mm, flags}}}}.
A refit is basis-K+1; metrics and deltas compare only within one basis.

Metrics per reference (``reference_metrics``), at the frozen camera on the host raster of the exported model
(``fitmesh.load_fit_mesh(glb, skip=basis.skip, frame=basis.frame)``; the frozen frame is mandatory):

- ``all`` = the fit's own definitions (``cameras.native_metrics`` / ``fit_views.contour_summary``): IoU, contour mean,
  p90, p95 and both directed means in px and in mm at the frozen front pivot, model-only and photo-only pixels, all
  inside the frozen ROI (the matte box + 15 % + 8 px); so the unedited model reproduces the basis's own numbers.
  Plus ``iou_full_image`` and ``model_outside_roi_px``: a temple splayed beyond the ROI still shows.
  The full-image render is cropped to the ROI: same pixel centres, same rays, the same bits as native_metrics.
- ``front`` = the front parts alone (the basis's names, or the faces within ``fitmesh.FRONT_DEPTH_MM`` of the
  front-most vertex) against the matte, inside the front render's box + 5 %.

Images: per reference one sheet ``photo | native | clay | overlay`` (photo edge green, model edge magenta:
``fit_views.overlay_image``), crops from a part's projected box + 15 % or a pixel box; the parts pass of render_views
(``parts_image``) colours the first-hit part of the host raster; ``contact_sheet`` lays out labelled tiles.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import cameras, fitmesh, raster
from . import matte as M
from .camera import Camera, NormFrame, project_mm
from .fit_views import CAMERAS_JSON, contour_summary, overlay_image

BASIS_SCHEMA = "g3d.basis/1"
REPORT_SCHEMA = "g3d.compare/1"
CAMERAS_DIR = "cameras"
BASIS_RE = re.compile(r"^basis-(\d{2,})$")
HISTORY = "compares.jsonl"
FRONT_REGION_MARGIN = 0.05
CROP_MARGIN = 0.15
SHEET_MAX_PX = 2048
PANEL_MAX_PX = 1024
BACKGROUNDS = {"grey": (235, 235, 235), "white": (255, 255, 255), "black": (0, 0, 0), "dark": (40, 40, 40)}
PALETTE = ((230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48), (145, 30, 180),
           (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212), (0, 128, 128), (220, 190, 255),
           (170, 110, 40), (255, 250, 200), (128, 0, 0), (170, 255, 195), (128, 128, 0), (255, 215, 180),
           (0, 0, 128), (128, 128, 128))
METRIC_KEYS = ("iou", "contour_mean_mm", "contour_p90_mm", "iou_full_image")


class NoBasis(LookupError):
    """The job has no camera basis yet (E_NO_BASIS): fit_cameras makes one."""


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ref_id(name: str) -> str:
    return Path(str(name)).stem


def _rel(path: Path, root: Path) -> str:
    path, root = Path(path).resolve(), Path(root).resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _abs(text: str, root: Path) -> Path:
    p = Path(text)
    return p if p.is_absolute() else Path(root) / p


def write_json(path: Path, doc) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=_json_default), encoding="utf-8")
    tmp.replace(path)


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


# ------------------------------------------------------------------------------------------------ bases
def basis_root(job_root) -> Path:
    return Path(job_root) / CAMERAS_DIR


def list_bases(job_root) -> list[str]:
    d = basis_root(job_root)
    if not d.is_dir():
        return []
    ids = [p.name for p in d.iterdir() if p.is_dir() and BASIS_RE.match(p.name) and (p / "basis.json").is_file()]
    return sorted(ids, key=lambda s: int(BASIS_RE.match(s).group(1)))


def next_basis_id(job_root) -> str:
    d = basis_root(job_root)
    used = [int(BASIS_RE.match(p.name).group(1)) for p in d.iterdir()
            if p.is_dir() and BASIS_RE.match(p.name)] if d.is_dir() else []
    return f"basis-{(max(used) + 1) if used else 1:02d}"


class Basis:
    """A frozen camera basis (see module doc)."""

    def __init__(self, directory: Path, job_root: Path):
        self.dir = Path(directory)
        self.job_root = Path(job_root)
        self.meta = json.loads((self.dir / "basis.json").read_text(encoding="utf-8"))
        if self.meta.get("schema") != BASIS_SCHEMA:
            raise ValueError(f"{self.dir}: basis.json schema {self.meta.get('schema')!r} is not {BASIS_SCHEMA}")
        self.id = self.meta["id"]
        self.frame = NormFrame.from_dict(self.meta["frame"])
        self.pivot_mm = np.asarray(self.meta["front_pivot_mm"], float)
        self.skip = tuple(self.meta.get("skip") or ())
        self.front_parts = tuple(self.meta["front_parts"]) if self.meta.get("front_parts") else None
        self.refs: dict = self.meta["refs"]
        self._mattes: dict = {}

    def camera(self, rid: str) -> Camera:
        return Camera.from_dict(self.refs[rid]["camera"])

    def shape(self, rid: str) -> tuple[int, int]:
        h, w = self.refs[rid]["shape"]
        return int(h), int(w)

    def roi(self, rid: str) -> tuple[int, int, int, int]:
        return tuple(int(v) for v in self.refs[rid]["roi"])

    def matte(self, rid: str) -> np.ndarray:
        if rid not in self._mattes:
            rec = self.refs[rid]
            p = self.dir / rec["matte"]
            if sha256_file(p) != rec["matte_sha256"]:
                raise ValueError(f"{p}: matte differs from basis.json (a frozen basis must not change)")
            self._mattes[rid] = np.asarray(Image.open(p).convert("L")) > 127
        return self._mattes[rid]

    def photo_path(self, rid: str) -> Path:
        return _abs(self.refs[rid]["photo"], self.job_root)

    def photo(self, rid: str) -> np.ndarray:
        p = self.photo_path(rid)
        if sha256_file(p) != self.refs[rid]["photo_sha256"]:
            raise ValueError(f"{p}: the photo differs from the one the basis was fitted on")
        return M.load_photo(p)

    def summary(self) -> dict:
        return {"id": self.id, "dir": str(self.dir), "reason": self.meta.get("reason"),
                "source": self.meta.get("source"), "model_sha256": (self.meta.get("model") or {}).get("sha256"),
                "refs": list(self.refs)}


def load_basis(job_root, basis_id: str | None = None) -> Basis:
    """The named basis or the newest one. Raises NoBasis when there is none."""
    ids = list_bases(job_root)
    if basis_id is None:
        if not ids:
            raise NoBasis("this job has no camera basis yet: run fit_cameras(reason) first (E_NO_BASIS)")
        basis_id = ids[-1]
    if basis_id not in ids:
        raise NoBasis(f"no camera basis {basis_id!r} (bases: {ids or 'none'}; fit_cameras makes one)")
    return Basis(basis_root(job_root) / basis_id, job_root)


def freeze_basis(job_root, basis_dir, photos: dict, *, reason: str, call_id: str | None, source: str,
                 model: dict | None = None) -> Basis:
    """Freeze ``basis_dir`` (which holds a fit's ``cameras.json``): check every photo against the fit's sha256,
    compute and store each matte once, check the ROI, and write ``basis.json``. ``photos`` = {image name: path}."""
    job_root, basis_dir = Path(job_root), Path(basis_dir)
    doc = json.loads((basis_dir / CAMERAS_JSON).read_text(encoding="utf-8"))
    mo = doc["model"]
    refs, notes = {}, []
    (basis_dir / "mattes").mkdir(exist_ok=True)
    for name, rec in doc["images"].items():
        rid = ref_id(name)
        if name not in photos:
            raise ValueError(f"no photo given for {name} (photos: {sorted(photos)})")
        p = Path(photos[name]).resolve()
        sha = sha256_file(p)
        if rec.get("image_sha256") and sha != rec["image_sha256"]:
            raise ValueError(f"{p}: sha256 {sha[:12]} != the fit's {rec['image_sha256'][:12]} for {name}")
        fg = M.matte(M.load_photo(p))
        if list(fg.shape) != list(rec["shape"]):
            raise ValueError(f"{name}: photo shape {list(fg.shape)} != the fit's {rec['shape']}")
        roi = cameras.matte_roi(fg)
        if rec.get("roi") is not None and list(roi) != list(rec["roi"]):
            notes.append(f"{rid}: recomputed matte ROI {list(roi)} != the fit's {rec['roi']}; the fit's ROI is kept")
            roi = tuple(rec["roi"])
        mp = basis_dir / "mattes" / f"{rid}.png"
        Image.fromarray((fg * 255).astype(np.uint8)).save(mp)
        refs[rid] = {"image": name, "photo": _rel(p, job_root), "photo_sha256": sha, "shape": list(rec["shape"]),
                     "view_kind": rec.get("view_kind"), "view": rec.get("view"), "camera": rec["camera"],
                     "roi": [int(v) for v in roi], "matte": f"mattes/{rid}.png", "matte_sha256": sha256_file(mp),
                     "matte_mode": "default", "fg_px": int(fg.sum()),
                     "overlay": (f"overlay_{rid}.png" if (basis_dir / f"overlay_{rid}.png").is_file() else None),
                     "fit": {k: rec.get(k) for k in ("status", "iou", "contour_mean_px", "contour_mean_mm",
                                                     "contour_p90_px", "contour_p90_mm", "flags", "refusal",
                                                     "px_per_mm_at_front_pivot")}}
    meta = {"schema": BASIS_SCHEMA, "id": basis_dir.name, "k": int(BASIS_RE.match(basis_dir.name).group(1)),
            "reason": reason, "call_id": call_id, "source": source, "created": _utc(),
            "cameras_json_sha256": sha256_file(basis_dir / CAMERAS_JSON),
            "model": {"fit_glb_path": mo.get("path"), "sha256": mo.get("sha256"), **(model or {})},
            "frame": mo["frame"], "front_pivot_mm": mo["front_pivot_mm"], "skip": list(mo.get("skip") or []),
            "front_parts": mo.get("front_parts"), "faces": mo.get("faces"), "parts": mo.get("parts"),
            "refs": refs, "notes": notes}
    write_json(basis_dir / "basis.json", meta)
    return Basis(basis_dir, job_root)


def import_cameras_json(job_root, cameras_json, photos: dict, *, reason: str, call_id: str | None = None,
                        basis_id: str | None = None) -> Basis:
    """Copy a WP3b ``cameras.json`` (and its overlays next to it) into a new basis and freeze it."""
    src = Path(cameras_json)
    bid = basis_id or next_basis_id(job_root)
    d = basis_root(job_root) / bid
    if d.exists():
        raise FileExistsError(f"{d} exists; a basis is never overwritten")
    d.mkdir(parents=True)
    shutil.copyfile(src, d / CAMERAS_JSON)
    for ov in sorted(src.parent.glob("overlay_*.png")):
        shutil.copyfile(ov, d / ov.name)
    return freeze_basis(job_root, d, photos, reason=reason, call_id=call_id, source=f"imported {src}")


# ------------------------------------------------------------------------------------------------ metrics
def _mask_metrics(m: np.ndarray, ref: np.ndarray, ppm: float) -> dict:
    inter = int(np.count_nonzero(m & ref))
    union = int(np.count_nonzero(m | ref))
    cs = contour_summary(m, ref)
    out = {"iou": inter / max(union, 1), "contour_mean_px": cs["mean"], "contour_p90_px": cs["p90"],
           "contour_p95_px": cs["p95"], "render_to_photo_mean_px": cs["render_to_photo_mean"],
           "photo_to_render_mean_px": cs["photo_to_render_mean"],
           "model_only_px": int(np.count_nonzero(m & ~ref)), "photo_only_px": int(np.count_nonzero(ref & ~m))}
    for k in ("contour_mean", "contour_p90", "contour_p95", "render_to_photo_mean", "photo_to_render_mean"):
        out[f"{k}_mm"] = out[f"{k}_px"] / ppm
    return out


def front_faces(mesh: fitmesh.FitMesh, front_parts=None) -> np.ndarray:
    """Bool per face: the named front parts, or the faces whose centroid is within FRONT_DEPTH_MM of the front."""
    if front_parts:
        return np.isin(mesh.part, mesh.part_ids(front_parts))
    zc = mesh.V[mesh.F].mean(axis=1)[:, 2]
    return zc >= float(mesh.V[:, 2].max()) - fitmesh.FRONT_DEPTH_MM


def _box(mask: np.ndarray, margin: float, shape) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    H, W = shape
    w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    mx, my = int(math.ceil(margin * w)), int(math.ceil(margin * h))
    return (int(max(0, xs.min() - mx)), int(max(0, ys.min() - my)), int(min(W, xs.max() + 1 + mx)),
            int(min(H, ys.max() + 1 + my)))


def reference_metrics(mesh: fitmesh.FitMesh, basis: Basis, rid: str, *, front: bool = True) -> tuple[dict, dict]:
    """(metrics record, {"mask": full-image model mask, "face_id": first-hit faces}) of one reference."""
    cam, shape, fg = basis.camera(rid), basis.shape(rid), basis.matte(rid)
    scene = mesh.scene()
    r = scene.render(cam, shape, 1)
    mask = r["mask"]
    x0, y0, x1, y1 = basis.roi(rid)
    ppm = cameras.px_per_mm_at(cam, basis.frame, basis.pivot_mm)
    m, ref = mask[y0:y1, x0:x1], fg[y0:y1, x0:x1]
    rec_all = _mask_metrics(m, ref, ppm)
    rec_all["iou_full_image"] = int(np.count_nonzero(mask & fg)) / max(int(np.count_nonzero(mask | fg)), 1)
    rec_all["model_outside_roi_px"] = int(np.count_nonzero(mask)) - int(np.count_nonzero(m))
    out = {"ref": rid, "image": basis.refs[rid]["image"], "view_kind": basis.refs[rid].get("view_kind"),
           "view": basis.refs[rid].get("view"), "shape": list(shape), "roi": [x0, y0, x1, y1], "px_per_mm": ppm,
           "all": rec_all, "basis_fit": basis.refs[rid].get("fit")}
    if front:
        sel = front_faces(mesh, basis.front_parts)
        if sel.any():
            fs = raster.get_scene(mesh.V, mesh.F[sel], mesh.frame)
            fm = fs.render(cam, shape, 1)["mask"]
            box = _box(fm, FRONT_REGION_MARGIN, shape)
            if box is not None:
                a0, b0, a1, b1 = box
                rec_f = _mask_metrics(fm[b0:b1, a0:a1], fg[b0:b1, a0:a1], ppm)
                rec_f["region"] = [a0, b0, a1, b1]
                rec_f["faces"] = int(sel.sum())
                out["front"] = rec_f
    return out, {"mask": mask, "face_id": r["face_id"]}


def geometry_key(mesh: fitmesh.FitMesh) -> str:
    return raster.mesh_key(mesh.V, mesh.F, mesh.frame)


def history(basis: Basis) -> list[dict]:
    p = basis.dir / HISTORY
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_history(basis: Basis, row: dict) -> None:
    with open(basis.dir / HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, default=_json_default) + "\n")


def deltas(current: dict, previous: dict | None) -> dict | None:
    """{metric: current - previous} for the 'all' and 'front' metric groups (None without a previous compare)."""
    if not previous:
        return None
    out = {}
    for group in ("all", "front"):
        a, b = current.get(group), previous.get(group)
        if a and b:
            out[group] = {k: a[k] - b[k] for k in METRIC_KEYS if k in a and k in b
                          and all(math.isfinite(x) for x in (a[k], b[k]))}
    return out


# ------------------------------------------------------------------------------------------------ images
def rgba_on(img: Image.Image, background) -> Image.Image:
    """An RGBA image composited on a background colour name / RGB tuple ('transparent' keeps the alpha)."""
    img = img.convert("RGBA")
    if background in (None, "transparent"):
        return img
    color = BACKGROUNDS.get(background, background) if isinstance(background, str) else tuple(background)
    if isinstance(color, str):
        raise ValueError(f"unknown background {background!r}; use {sorted(BACKGROUNDS)}, 'transparent' or [r, g, b]")
    bg = Image.new("RGBA", img.size, tuple(int(c) for c in color[:3]) + (255,))
    bg.alpha_composite(img)
    return bg


def fit_into(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    k = min(max_w / img.width, max_h / img.height, 1.0)
    if k >= 1.0:
        return img
    return img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))), Image.LANCZOS)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                       # Pillow < 10.1
        return ImageFont.load_default()


def labelled(img: Image.Image, label: str, size: int = 16) -> Image.Image:
    img = img.convert("RGBA")
    pad = size + 8
    out = Image.new("RGBA", (img.width, img.height + pad), (255, 255, 255, 255))
    out.paste(img, (0, pad), img)
    ImageDraw.Draw(out).text((4, 3), label, fill=(20, 20, 20, 255), font=_font(size))
    return out


def grid(images: list[Image.Image], cols: int, max_px: int = SHEET_MAX_PX, gap: int = 6,
         background=(255, 255, 255)) -> Image.Image:
    """Images in a grid of ``cols`` columns (each column as wide as its widest image, each row as tall as its
    tallest), scaled so the sheet's longer side is <= max_px."""
    rows = max(1, math.ceil(len(images) / cols))
    cw = [max((images[n].width for n in range(c, len(images), cols)), default=0) for c in range(cols)]
    rh = [max(i.height for i in images[r * cols:(r + 1) * cols]) for r in range(rows)]
    W, H = sum(cw) + (cols + 1) * gap, sum(rh) + (rows + 1) * gap
    sheet = Image.new("RGBA", (W, H), tuple(background) + (255,))
    for n, im in enumerate(images):
        r, c = divmod(n, cols)
        sheet.paste(im, (gap + sum(cw[:c]) + c * gap, gap + sum(rh[:r]) + r * gap), im.convert("RGBA"))
    return fit_into(sheet, max_px, max_px)


def contact_sheet(tiles: list[tuple[str, list[Image.Image]]], *, cols: int = 2, max_px: int = SHEET_MAX_PX,
                  pass_max_px: int = 512) -> Image.Image:
    """One labelled cell per view (its passes side by side), ``cols`` cells per row."""
    cells = []
    for label, imgs in tiles:
        imgs = [fit_into(i, pass_max_px, pass_max_px) for i in imgs]
        h = max(i.height for i in imgs)
        w = sum(i.width for i in imgs) + 4 * (len(imgs) - 1)
        cell = Image.new("RGBA", (w, h), (255, 255, 255, 255))
        x = 0
        for i in imgs:
            cell.paste(i, (x, (h - i.height) // 2), i)
            x += i.width + 4
        cells.append(labelled(cell, label))
    return grid(cells, min(cols, len(cells)), max_px)


def parts_image(mesh: fitmesh.FitMesh, cam: Camera, shape) -> tuple[Image.Image, dict]:
    """The host raster at ``cam``: each pixel coloured by the part of its first hit (RGBA, transparent misses).
    Returns (image, {part name: '#rrggbb'}) for the parts that show."""
    r = mesh.scene().render(cam, tuple(shape), 1)
    fid = r["face_id"]
    H, W = fid.shape
    rgba = np.zeros((H, W, 4), np.uint8)
    hit = fid >= 0
    part = np.full(fid.shape, -1, np.int64)
    part[hit] = mesh.part[fid[hit]]
    legend = {}
    for p in np.unique(part[hit]):
        color = PALETTE[int(p) % len(PALETTE)]
        rgba[part == p] = color + (255,)
        legend[mesh.node_names[int(p)]] = "#%02x%02x%02x" % color
    return Image.fromarray(rgba, "RGBA"), legend


def reference_sheet(photo: np.ndarray, fg: np.ndarray, mask: np.ndarray, native: Image.Image | None,
                    clay: Image.Image | None, label: str, max_px: int = SHEET_MAX_PX) -> Image.Image:
    """photo | native | clay | overlay (2 x 2; photo | overlay without renders), each panel labelled."""
    ov = Image.fromarray(overlay_image(photo, fg, mask))
    ph = Image.fromarray(photo)
    panels = [labelled(fit_into(ph, PANEL_MAX_PX, PANEL_MAX_PX), f"{label}: photo")]
    for name, im in (("native", native), ("clay", clay)):
        if im is not None:
            panels.append(labelled(fit_into(rgba_on(im, "white"), PANEL_MAX_PX, PANEL_MAX_PX), f"{label}: {name}"))
    panels.append(labelled(fit_into(ov, PANEL_MAX_PX, PANEL_MAX_PX),
                           f"{label}: overlay (photo edge green, model edge magenta)"))
    return grid(panels, 2 if len(panels) > 2 else len(panels), max_px)


def part_box(mesh: fitmesh.FitMesh, cam: Camera, shape, part: str, margin: float = CROP_MARGIN):
    """Pixel box (x0, y0, x1, y1) of the parts whose node name contains ``part`` (case-insensitive), + margin."""
    want = [i for i, n in enumerate(mesh.node_names) if part.lower() in n.lower()]
    if not want:
        raise ValueError(f"no part matches {part!r}; parts: {sorted(set(mesh.names))}")
    sel = np.isin(mesh.part, want)
    V = mesh.V[np.unique(mesh.F[sel])]
    uv = project_mm(V, cam, mesh.frame)
    H, W = shape
    lo, hi = uv.min(0), uv.max(0)
    w, h = hi - lo
    x0, y0 = math.floor(lo[0] - margin * w), math.floor(lo[1] - margin * h)
    x1, y1 = math.ceil(hi[0] + margin * w) + 1, math.ceil(hi[1] + margin * h) + 1
    box = (max(0, x0), max(0, y0), min(W, x1), min(H, y1))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError(f"part {part!r} projects outside the photo")
    return box, [mesh.node_names[i] for i in want]
