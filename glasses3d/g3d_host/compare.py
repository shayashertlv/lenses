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

Photo-matched compare (WP6_FACETS §3.3; the pieces agent/seeing.py assembles, all numpy/PIL):

- a basis image may carry a temple pose (``Basis.pose``), a colourway and a fit status (§3.1);
- ``linear_gain``: one scalar = median photo luminance / median render luminance (both linear) over a mask (the
  front acetate body eroded >= 12 px on the best-registered target photo; ``gain_region``); ``linear_gain_rgb``: the
  same per channel (white balance: the render's acetate hue matches the photo's);
- ``read_linear``: (RGB, straight alpha) of a linear pass (16-bit PNG or the float ``.npz`` of g3d/render.py);
- ``stretch_range`` / ``apply_stretch``: one monotone curve from the photo's acetate p2-p98 luma (8-bit sRGB),
  applied identically to the photo and the render (``composite_render`` puts the gained linear render on the photo's
  backdrop first); both have a float form (no 8-bit rounding) for measurements;
- ``crease_edges`` / ``crease_polylines``: mesh edges with a dihedral >= 6 deg (ridge = convex, valley = concave)
  drawn where both faces face the camera and the raster depth shows them; ``iso_lines``: the chart grid (iso-d every
  10 mm, iso-y every 5 mm) with mm labels per side and level (the side with more line pixels first, overlapping
  labels dropped); ``overlay_matched`` draws outline, creases and grid on the photo;
- ``outer_edge_offsets``: per side and 10 mm station, the photo's outer silhouette edge minus the model's along the
  image direction of the temple's outward x (mm; + = the photo reaches further out);
- ``stack_rows``: one labelled image per photo, rows photo / render / overlay (/ gloss_black).
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
from scipy import ndimage

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

    # optional WP6_FACETS §3.1 fields (absent in older bases)
    def theta(self, rid: str) -> dict | None:
        return self.refs[rid].get("theta_deg")

    def pose(self, rid: str) -> dict | None:
        """The image's temple pose {"+1": {theta_deg, axis_xz_mm}, "-1": ...} (g3d_host/pose), or None when the basis
        has no pose for it."""
        rec = self.refs[rid]
        th, ax = rec.get("theta_deg"), rec.get("hinge_axis_xz_mm")
        if not th or not ax:
            return None
        return {k: {"theta_deg": float(th[k]), "axis_xz_mm": [float(v) for v in ax[k]]} for k in ("+1", "-1")
                if k in th and k in ax}

    def colourway(self, rid: str) -> str:
        return self.refs[rid].get("colourway") or "target"

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


OPTIONAL_IMAGE_FIELDS = ("theta_deg", "theta_observed", "hinge_axis_xz_mm", "colourway", "fit_status")


def freeze_basis(job_root, basis_dir, photos: dict, *, reason: str, call_id: str | None, source: str,
                 model: dict | None = None) -> Basis:
    """Freeze ``basis_dir`` (which holds a fit's ``cameras.json``): check every photo against the fit's sha256,
    compute and store each matte once, check the ROI, and write ``basis.json``. ``photos`` = {image name: path}.

    An image record may name its ``matte_mode``: a matte.MODES mode (default "default"), or "supplied" with
    ``matte_file`` (a PNG next to cameras.json, > 127 = foreground) and its ``matte_file_sha256``, for a matte made
    elsewhere (its ``matte_note`` says how). It may carry the optional pose fields of WP6_FACETS §3.1
    (OPTIONAL_IMAGE_FIELDS), which are copied into the basis."""
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
        mode = rec.get("matte_mode") or "default"
        if mode == "supplied":
            mf = basis_dir / rec["matte_file"]
            if sha256_file(mf) != rec.get("matte_file_sha256"):
                raise ValueError(f"{name}: supplied matte {mf} does not match its matte_file_sha256")
            fg = np.asarray(Image.open(mf).convert("L")) > 127
        elif mode in M.MODES:
            fg = M.matte(M.load_photo(p), mode=mode)
        else:
            raise ValueError(f"{name}: matte mode {mode!r} is not a matte.py mode and no matte was supplied")
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
                     "matte_mode": mode, "fg_px": int(fg.sum()),
                     "overlay": (f"overlay_{rid}.png" if (basis_dir / f"overlay_{rid}.png").is_file() else None),
                     "fit": {k: rec.get(k) for k in ("status", "iou", "contour_mean_px", "contour_mean_mm",
                                                     "contour_p90_px", "contour_p90_mm", "flags", "refusal",
                                                     "px_per_mm_at_front_pivot")}}
        refs[rid].update({k: rec[k] for k in OPTIONAL_IMAGE_FIELDS + ("matte_note",) if k in rec})
    meta = {"schema": BASIS_SCHEMA, "id": basis_dir.name, "k": int(BASIS_RE.match(basis_dir.name).group(1)),
            "reason": reason, "call_id": call_id, "source": source, "created": _utc(),
            "cameras_json_sha256": sha256_file(basis_dir / CAMERAS_JSON),
            "model": {"fit_glb_path": mo.get("path"), "sha256": mo.get("sha256"), **(model or {})},
            "frame": mo["frame"], "front_pivot_mm": mo["front_pivot_mm"], "skip": list(mo.get("skip") or []),
            "front_parts": mo.get("front_parts"), "faces": mo.get("faces"), "parts": mo.get("parts"),
            "refs": refs, "notes": notes}
    if mo.get("pose_source"):
        meta["pose_source"] = mo["pose_source"]
    write_json(basis_dir / "basis.json", meta)
    return Basis(basis_dir, job_root)


def import_cameras_json(job_root, cameras_json, photos: dict, *, reason: str, call_id: str | None = None,
                        basis_id: str | None = None) -> Basis:
    """Copy a WP3b ``cameras.json`` (and its overlays and supplied mattes next to it) into a new basis and freeze
    it."""
    src = Path(cameras_json)
    bid = basis_id or next_basis_id(job_root)
    d = basis_root(job_root) / bid
    if d.exists():
        raise FileExistsError(f"{d} exists; a basis is never overwritten")
    d.mkdir(parents=True)
    shutil.copyfile(src, d / CAMERAS_JSON)
    for ov in sorted(src.parent.glob("overlay_*.png")):
        shutil.copyfile(ov, d / ov.name)
    for rec in json.loads(src.read_text(encoding="utf-8"))["images"].values():
        if rec.get("matte_mode") == "supplied":
            (d / rec["matte_file"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src.parent / rec["matte_file"], d / rec["matte_file"])
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


# ------------------------------------------------------------------------------------------------ photo-matched compare
LUMA = np.array([0.2126, 0.7152, 0.0722])
GAIN_ERODE_PX = 12
GAIN_MIN_PX = 1500
GAIN_FRONT_SHARE = 0.25
STRETCH_PCT = (2.0, 98.0)
CROP_PAD = 0.08
STACK_MAX_PX = 1024
LABEL_PX = 24
CREASE_MIN_DEG = 6.0
CREASE_DEPTH_TOL_MM = 0.4
ISO_D_MM, ISO_Y_MM = 10.0, 5.0
ISO_MIN_LABEL_PX = 12
LABEL_GAP_PX, LABEL_CHAR_PX = 12, 6          # labels closer than 12 px vertically and their text width apart: drop
OUTER_REACH_MM = 6.0
OUTLINE_RGB, RIDGE_RGB, VALLEY_RGB = (255, 0, 255), (255, 128, 0), (0, 110, 255)
GRID_RGB, LABEL_RGB = (0, 150, 90), (0, 90, 50)
CAMERA_NOTE = ("A shift shared by the outline, the creases and the grid against the photo is camera error, not "
               "shape. So is an outer-edge offset with the same sign on both sides at every d that the start model "
               "shows too: read the change from the start value, not the value itself.")


def srgb_to_linear(c8) -> np.ndarray:
    c = np.asarray(c8, np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb8(x) -> np.ndarray:
    x = np.clip(np.asarray(x, np.float64), 0.0, 1.0)
    s = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.round(s * 255.0).astype(np.uint8)


def luma8(rgb8) -> np.ndarray:
    """Rec. 709 luma of 8-bit sRGB values (the "L8" of WP6_FACETS)."""
    return np.asarray(rgb8, np.float64)[..., :3] @ LUMA


def read_linear_png(path) -> tuple[np.ndarray, np.ndarray]:
    """(RGB, alpha) in [0, 1] of a 16-bit RGBA PNG (a native_linear / gloss_black pass; straight alpha)."""
    import cv2
    a = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if a is None or a.ndim != 3 or a.shape[2] != 4:
        raise ValueError(f"{path}: not an RGBA PNG")
    scale = 65535.0 if a.dtype == np.uint16 else 255.0
    a = a.astype(np.float64) / scale
    return a[..., 2::-1].copy(), a[..., 3].copy()


def read_linear(path) -> tuple[np.ndarray, np.ndarray]:
    """(RGB, straight alpha) of a linear pass: a 16-bit PNG (clipped at 1) or g3d/render.py's float ``.npz``
    (``rgb`` straight, ``alpha``; unclipped)."""
    if str(path).lower().endswith(".npz"):
        with np.load(path) as z:
            return z["rgb"].astype(np.float64), z["alpha"].astype(np.float64)
    return read_linear_png(path)


def erode_px(mask: np.ndarray, px: float) -> np.ndarray:
    """Pixels further than ``px`` from the mask's outside."""
    m = np.asarray(mask, bool)
    if not m.any():
        return m
    return ndimage.distance_transform_edt(np.pad(m, 1))[1:-1, 1:-1] > px


def largest_component(mask: np.ndarray) -> np.ndarray:
    lab, n = ndimage.label(mask)
    if n <= 1:
        return np.asarray(mask, bool)
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    return lab == 1 + int(np.argmax(sizes))


def linear_gain(photo8: np.ndarray, render_rgb: np.ndarray, mask: np.ndarray) -> dict:
    """{gain, px, photo_Y, render_Y}: median linear photo luminance / median linear render luminance over mask."""
    m = np.asarray(mask, bool)
    if not m.any():
        raise ValueError("the gain region is empty")
    py = float(np.median(srgb_to_linear(photo8[..., :3][m]) @ LUMA))
    ry = float(np.median(np.asarray(render_rgb)[m] @ LUMA))
    if not ry > 0:
        raise ValueError("the render is black over the gain region")
    return {"gain": py / ry, "px": int(m.sum()), "photo_Y": py, "render_Y": ry}


def linear_gain_rgb(photo8: np.ndarray, render_rgb: np.ndarray, mask: np.ndarray) -> dict:
    """Per-channel white balance over mask: {gain [r, g, b] = median linear photo channel / median render channel,
    gain_Y (the luminance of the gain), px, photo_rgb, render_rgb, photo_Y, render_Y}."""
    m = np.asarray(mask, bool)
    if not m.any():
        raise ValueError("the gain region is empty")
    p = np.median(srgb_to_linear(photo8[..., :3][m]), axis=0)
    r = np.median(np.asarray(render_rgb)[m], axis=0)
    if not (r > 0).all():
        raise ValueError("the render is black in a channel over the gain region")
    g = p / r
    return {"gain": [float(v) for v in g], "gain_Y": float(g @ LUMA), "px": int(m.sum()),
            "photo_rgb": [float(v) for v in p], "render_rgb": [float(v) for v in r],
            "photo_Y": float(np.median(srgb_to_linear(photo8[..., :3][m]) @ LUMA)),
            "render_Y": float(np.median(np.asarray(render_rgb)[m] @ LUMA))}


def gain_region(front: np.ndarray, acetate: np.ndarray, matte: np.ndarray, alpha: np.ndarray,
                erode: int = GAIN_ERODE_PX, min_px: int = GAIN_MIN_PX,
                front_share: float = GAIN_FRONT_SHARE) -> tuple[np.ndarray, str]:
    """The front acetate body (visible, inside the matte and the render's full alpha) eroded ``erode`` px. When that
    is not usable — fewer than ``min_px`` pixels, or less than ``front_share`` of the largest connected acetate region
    (a front seen edge-on, e.g. in a side view) — the largest connected acetate region instead (labelled so)."""
    base = np.asarray(matte, bool) & (np.asarray(alpha) > 0.999)
    m = erode_px(np.asarray(front, bool) & base, erode)
    big = largest_component(erode_px(np.asarray(acetate, bool) & base, erode))
    if m.sum() >= max(min_px, front_share * big.sum()):
        return m, "front acetate body"
    return big, "largest acetate region (no usable front in this photo)"


def stretch_range(rgb8: np.ndarray, mask: np.ndarray, pct=STRETCH_PCT) -> tuple[float, float]:
    """(lo, hi) = the p2 and p98 luma of the masked pixels (8-bit sRGB)."""
    L = luma8(rgb8)[np.asarray(mask, bool)]
    if L.size < 10:
        raise ValueError("too few acetate pixels for a stretch")
    lo, hi = (float(v) for v in np.percentile(L, pct))
    return lo, max(hi, lo + 1.0)


def apply_stretch(rgb8: np.ndarray, lo: float, hi: float, *, as_float: bool = False) -> np.ndarray:
    """The monotone luma curve L' = (L - lo) * 255 / (hi - lo), applied by shifting every channel by L' - L (so hue
    and the channel differences stay; a per-channel stretch would turn beige yellow), clipped to 8 bits (as_float:
    clipped to [0, 255] but not rounded, for measurements)."""
    x = np.asarray(rgb8, np.float64)[..., :3]
    L = x @ LUMA
    shift = (L - lo) * (255.0 / (hi - lo)) - L
    out = np.clip(x + shift[..., None], 0, 255)
    return out if as_float else np.round(out).astype(np.uint8)


def photo_background(photo8: np.ndarray, matte: np.ndarray) -> tuple[int, int, int]:
    """Median RGB of the photo outside its matte (the backdrop a render is composited on)."""
    bg = photo8[..., :3][~np.asarray(matte, bool)]
    if not len(bg):
        return (255, 255, 255)
    return tuple(int(v) for v in np.median(bg, axis=0))


def linear_to_srgb(x) -> np.ndarray:
    """Linear [0, 1] (clipped) -> sRGB in [0, 255], not rounded."""
    x = np.clip(np.asarray(x, np.float64), 0.0, 1.0)
    return 255.0 * np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def composite_render(rgb: np.ndarray, alpha: np.ndarray, gain, bg8, *, as_float: bool = False) -> np.ndarray:
    """The linear render x gain (a scalar, or [r, g, b] for a white-balanced gain) over the backdrop (in linear), as
    8-bit sRGB (as_float: sRGB in [0, 255], not rounded, for measurements)."""
    a = np.asarray(alpha, np.float64)[..., None]
    bgl = srgb_to_linear(np.asarray(bg8, np.float64))
    lin = np.asarray(rgb) * np.asarray(gain, np.float64) * a + bgl * (1.0 - a)
    return linear_to_srgb(lin) if as_float else linear_to_srgb8(lin)


def to_norm_depth(P_mm: np.ndarray, cam: Camera, frame: NormFrame) -> np.ndarray:
    """The raster's depth (mm along the view axis from the frame centre plane) of model points."""
    toward = raster.camera_basis(cam)[2]
    return -(frame.to_norm(P_mm) @ toward) * frame.extent


def weld(V: np.ndarray, F: np.ndarray, tol: float = 1e-4) -> tuple[np.ndarray, np.ndarray]:
    """Merge vertices closer than ~tol (rounded positions); faces re-indexed."""
    key = np.round(np.asarray(V, float) / tol).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    return np.asarray(V, float)[first], inv.reshape(-1)[np.asarray(F)]


def crease_edges(V: np.ndarray, F: np.ndarray, min_deg: float = CREASE_MIN_DEG) -> dict:
    """Edges shared by exactly two faces whose normals differ by >= min_deg. Returns {a, b (E, 3) end points,
    shape (+1 ridge = convex, -1 valley), dihedral_deg, n1, n2 (unit face normals)}; V/F as one closed or open mesh
    (welded first)."""
    Vw, Fw = weld(V, F)
    Fw = Fw[(Fw[:, 0] != Fw[:, 1]) & (Fw[:, 1] != Fw[:, 2]) & (Fw[:, 0] != Fw[:, 2])]
    n = np.cross(Vw[Fw[:, 1]] - Vw[Fw[:, 0]], Vw[Fw[:, 2]] - Vw[Fw[:, 0]])
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-12
    Fw, n = Fw[ok], n[ok] / ln[ok, None]
    e = np.concatenate([Fw[:, [0, 1]], Fw[:, [1, 2]], Fw[:, [2, 0]]])
    opp = np.concatenate([Fw[:, 2], Fw[:, 0], Fw[:, 1]])
    fid = np.tile(np.arange(len(Fw)), 3)
    key = np.sort(e, axis=1)
    order = np.lexsort((key[:, 1], key[:, 0]))
    key, opp, fid, e = key[order], opp[order], fid[order], e[order]
    same = np.all(key[1:] == key[:-1], axis=1)               # rows j and j + 1 are one edge
    prev, nxt = np.r_[False, same[:-1]], np.r_[same[1:], False]
    keep = np.nonzero(same & ~prev & ~nxt)[0]                  # exactly two faces share it
    out = {"a": np.zeros((0, 3)), "b": np.zeros((0, 3)), "shape": np.zeros(0, np.int8),
           "dihedral_deg": np.zeros(0), "n1": np.zeros((0, 3)), "n2": np.zeros((0, 3))}
    if not len(keep):
        return out
    f1, f2 = fid[keep], fid[keep + 1]
    n1, n2 = n[f1], n[f2]
    dih = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", n1, n2), -1.0, 1.0)))
    sel = dih >= min_deg
    a, b = Vw[key[keep, 0]], Vw[key[keep, 1]]
    c2 = Vw[opp[keep + 1]]
    convex = np.einsum("ij,ij->i", n1, c2 - a) < 0
    out.update(a=a[sel], b=b[sel], shape=np.where(convex, 1, -1).astype(np.int8)[sel], dihedral_deg=dih[sel],
               n1=n1[sel], n2=n2[sel])
    return out


def crease_polylines(edges: dict, cam: Camera, frame: NormFrame, depth: np.ndarray,
                     tol_mm: float = CREASE_DEPTH_TOL_MM, step_px: float = 1.5) -> list:
    """[(points (K, 2) px, shape)] of the visible runs of each crease edge: both faces facing the camera, each sample
    no deeper than the raster depth at its pixel + tol_mm. ``depth`` is the raster depth image of the camera's grid."""
    if not len(edges["a"]):
        return []
    H, W = depth.shape
    toward = raster.camera_basis(cam)[2]
    p = float(cam.perspective)
    out = []
    ua, ub = project_mm(edges["a"], cam, frame), project_mm(edges["b"], cam, frame)
    for k in range(len(edges["a"])):
        n_s = int(max(2, math.ceil(float(np.linalg.norm(ub[k] - ua[k])) / step_px) + 1))
        t = np.linspace(0.0, 1.0, n_s)[:, None]
        P = edges["a"][k] * (1 - t) + edges["b"][k] * t
        Pn = frame.to_norm(P)
        view = (toward / p - Pn) if p > 0 else np.broadcast_to(toward, Pn.shape)
        facing = (view @ edges["n1"][k] > 0) & (view @ edges["n2"][k] > 0)
        uv = ua[k] * (1 - t) + ub[k] * t
        iu, iv = np.round(uv[:, 0]).astype(int), np.round(uv[:, 1]).astype(int)
        inside = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)
        vis = np.zeros(n_s, bool)
        d = to_norm_depth(P, cam, frame)
        vis[inside] = d[inside] <= depth[iv[inside], iu[inside]] + tol_mm
        vis &= facing & inside
        start = None
        for j in range(n_s + 1):
            if j < n_s and vis[j]:
                start = j if start is None else start
            elif start is not None:
                if j - start >= 2:
                    out.append((uv[start:j], int(edges["shape"][k])))
                start = None
    return out


def crease_polylines_fast(edges: dict, cam: Camera, frame: NormFrame, depth: np.ndarray,
                          tol_mm: float = CREASE_DEPTH_TOL_MM, step_px: float = 1.5) -> list:
    """``crease_polylines`` vectorised over every edge at once (the same samples, tests and runs in the same order:
    tests/agent/test_temple_lab pins the equality); about 10 x faster on a temple's ~4k crease edges."""
    E = len(edges["a"])
    if not E:
        return []
    H, W = depth.shape
    toward = raster.camera_basis(cam)[2]
    p = float(cam.perspective)
    ua, ub = project_mm(edges["a"], cam, frame), project_mm(edges["b"], cam, frame)
    n_s = np.array([int(max(2, math.ceil(float(np.linalg.norm(ub[k] - ua[k])) / step_px) + 1)) for k in range(E)])
    idx = np.repeat(np.arange(E), n_s)                                  # the edge of every sample
    first = np.cumsum(n_s) - n_s
    j = np.arange(len(idx)) - first[idx]
    t = j * (1.0 / (n_s[idx] - 1))                                     # np.linspace(0, 1, n): j x step, end exact
    last = j == n_s[idx] - 1
    t[last] = 1.0
    t = t[:, None]
    P = edges["a"][idx] * (1 - t) + edges["b"][idx] * t
    Pn = frame.to_norm(P)
    view = (toward / p - Pn) if p > 0 else np.broadcast_to(toward, Pn.shape)
    facing = (np.einsum("ij,ij->i", view, edges["n1"][idx]) > 0) & (np.einsum("ij,ij->i", view, edges["n2"][idx]) > 0)
    uv = ua[idx] * (1 - t) + ub[idx] * t
    iu, iv = np.round(uv[:, 0]).astype(int), np.round(uv[:, 1]).astype(int)
    inside = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)
    vis = np.zeros(len(idx), bool)
    d = to_norm_depth(P, cam, frame)
    vis[inside] = d[inside] <= depth[iv[inside], iu[inside]] + tol_mm
    vis &= facing & inside
    # runs of visible samples inside one edge: a run starts where the previous sample is hidden or another edge's
    prev = np.r_[False, vis[:-1]] & np.r_[False, idx[1:] == idx[:-1]]
    nxt = np.r_[vis[1:], False] & np.r_[idx[1:] == idx[:-1], False]
    starts, ends = np.flatnonzero(vis & ~prev), np.flatnonzero(vis & ~nxt) + 1
    shape = edges["shape"]
    return [(uv[s:e], int(shape[idx[s]])) for s, e in zip(starts.tolist(), ends.tolist()) if e - s >= 2]


def crease_polylines_posed(bodies: list, occluder, cam: Camera, frame: NormFrame, shape: tuple[int, int]) -> list:
    """The overlay's crease polylines (``crease_edges`` >= CREASE_MIN_DEG per body, ``crease_polylines_fast``) of
    temple bodies [(V mm, triangles)] already posed in the camera's scene, depth-tested on the host raster of the
    bodies plus ``occluder`` (V mm, triangles: the rest of the scene, posed the same way; None = the bodies alone).
    The raster scene is built here and never enters the LRU cache of ``raster.get_scene`` (a sheet makes one per
    model and photo)."""
    parts = [crease_edges(V, F) for V, F in bodies]
    edges = {key: np.concatenate([e[key] for e in parts]) for key in parts[0]} if parts else \
        crease_edges(np.zeros((0, 3)), np.zeros((0, 3), int))
    Vs, Fs, n = [], [], 0
    for V, F in list(bodies) + ([occluder] if occluder is not None else []):
        Vs.append(np.asarray(V, np.float64))
        Fs.append(np.asarray(F, np.int64) + n)
        n += len(V)
    depth = raster.RasterScene(np.concatenate(Vs), np.concatenate(Fs), frame).render(cam, tuple(shape), 1)["depth"] \
        if n else np.full(tuple(shape), np.inf, np.float32)
    return crease_polylines_fast(edges, cam, frame, depth)


def iso_lines(d: np.ndarray, y: np.ndarray, valid: np.ndarray, side: np.ndarray | None = None,
              step_d: float = ISO_D_MM, step_y: float = ISO_Y_MM) -> dict:
    """The chart grid on an image: {d_mask, y_mask, labels [(u, v, text)]}. A pixel is on an iso-d line when the
    floor(d / step_d) of it and its right or lower neighbour differ (both valid and on the same side); likewise y.
    Each line is labelled once per side (iso-d at its topmost pixel, iso-y at its pixel of least d); labels are placed
    in order of their line's pixel count (so the side seen best wins) and one that would overlap a placed label
    (closer than LABEL_GAP_PX vertically and than the text width horizontally) is dropped."""
    valid = np.asarray(valid, bool)
    side = np.zeros(valid.shape, np.int8) if side is None else np.asarray(side)
    out = {"d_mask": np.zeros(valid.shape, bool), "y_mask": np.zeros(valid.shape, bool), "labels": []}
    cands = []
    for key, val, step in (("d", d, step_d), ("y", y, step_y)):
        lv = np.full(valid.shape, np.iinfo(np.int64).min, np.int64)
        lv[valid] = np.floor(np.asarray(val)[valid] / step).astype(np.int64)
        line = np.zeros(valid.shape, bool)
        level = np.zeros(valid.shape, np.int64)
        for sl_a, sl_b in (((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                           ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
            both = valid[sl_a] & valid[sl_b] & (side[sl_a] == side[sl_b])
            hit = both & (lv[sl_a] != lv[sl_b])
            line[sl_a] |= hit
            level[sl_a] = np.where(hit, np.maximum(lv[sl_a], lv[sl_b]), level[sl_a])
        out[f"{key}_mask"] = line
        for s in np.unique(side[line]):
            on_side = line & (side == s)
            for lvl in np.unique(level[on_side]):
                vs, us = np.nonzero(on_side & (level == lvl))
                if len(us) < ISO_MIN_LABEL_PX:
                    continue
                j = int(np.argmin(vs)) if key == "d" else int(np.argmin(np.asarray(d)[vs, us]))
                cands.append((len(us), int(us[j]), int(vs[j]), f"{key}{lvl * step:g}"))
    for _n, u, v, text in sorted(cands, key=lambda c: -c[0]):
        w = LABEL_CHAR_PX * len(text) + 4
        if any(abs(v - pv) < LABEL_GAP_PX and abs(u - pu) < max(w, LABEL_CHAR_PX * len(pt) + 4)
               for pu, pv, pt in out["labels"]):
            continue
        out["labels"].append((u, v, text))
    return out


def overlay_matched(base8: np.ndarray, model_mask: np.ndarray, polylines: list, grid: dict | None) -> Image.Image:
    """The (stretched) photo with the chart grid (green, mm labels), the model outline (magenta) and the crease
    polylines (orange ridge, blue valley) on top."""
    img = np.array(base8[..., :3], np.uint8, copy=True)
    if grid is not None:
        g = grid["d_mask"] | grid["y_mask"]
        img[g] = (0.45 * img[g] + 0.55 * np.array(GRID_RGB)).astype(np.uint8)
    img[cameras._edge(np.asarray(model_mask, bool))] = OUTLINE_RGB
    im = Image.fromarray(img)
    dr = ImageDraw.Draw(im)
    for pts, shape in polylines:
        dr.line([tuple(map(float, p)) for p in pts], fill=RIDGE_RGB if shape > 0 else VALLEY_RGB, width=2)
    if grid is not None:
        f = _font(11)
        for u, v, text in grid["labels"]:
            dr.text((u + 2, v - 12), text, fill=LABEL_RGB, font=f, stroke_width=2, stroke_fill=(255, 255, 255))
    return im


def outer_edge_offsets(stations: list, model_mask: np.ndarray, matte: np.ndarray,
                       reach_mm: float = OUTER_REACH_MM) -> list:
    """Per station {d_mm, uv, dir, px_per_mm}: walk +-reach_mm along ``dir`` (unit, image px, outward) through ``uv``
    and compare the model mask's and the photo matte's outward edges nearest the station: offset_mm = (photo - model)
    / px_per_mm (+ = the photo reaches further out). Stations whose edges are not found within the reach (e.g. the
    photo edge there is another part's) get None."""
    H, W = model_mask.shape
    out = []
    for st in stations:
        uv, e, ppm = np.asarray(st["uv"], float), np.asarray(st["dir"], float), float(st["px_per_mm"])
        reach = reach_mm * ppm
        t = np.arange(-reach, reach + 0.25, 0.5)
        pts = uv[None] + t[:, None] * e[None]
        iu, iv = np.round(pts[:, 0]).astype(int), np.round(pts[:, 1]).astype(int)
        ok = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)
        rec = {"d_mm": st["d_mm"], "offset_mm": None}

        def edge(mask, near):
            s = np.zeros(len(t), bool)
            s[ok] = mask[iv[ok], iu[ok]]
            ends = np.nonzero(s[:-1] & ~s[1:] & ok[:-1] & ok[1:])[0]       # never the image border
            if not len(ends):
                return None
            te = (t[ends] + t[ends + 1]) / 2
            return float(te[np.argmin(np.abs(te - near))])
        tm = edge(np.asarray(model_mask, bool), 0.0)
        tp = edge(np.asarray(matte, bool), 0.0 if tm is None else tm)
        if tm is not None and tp is not None and abs(tp - tm) <= reach:
            rec["offset_mm"] = round((tp - tm) / ppm, 2)
        out.append(rec)
    return out


def stack_rows(rows: list, gap: int = 4) -> Image.Image:
    """One image: each (label, image) row labelled above it, stacked top to bottom on white."""
    tiles = [labelled(im, label) for label, im in rows]
    W = max(t.width for t in tiles)
    H = sum(t.height for t in tiles) + gap * (len(tiles) - 1)
    out = Image.new("RGB", (W, H), (255, 255, 255))
    y = 0
    for t in tiles:
        out.paste(t.convert("RGB"), (0, y))
        y += t.height + gap
    return out


def stack_scale(crop_w: int, crop_h: int, n_rows: int, max_px: int = STACK_MAX_PX, label_px: int = LABEL_PX,
                gap: int = 4) -> float:
    """The downscale k (<= 1, never up) at which n_rows labelled crop rows stack within max_px on both sides."""
    k_h = (max_px - n_rows * label_px - gap * (n_rows - 1)) / max(1, n_rows * crop_h)
    return float(min(1.0, max_px / max(1, crop_w), k_h))


def crop_box(temple_mask: np.ndarray, matte: np.ndarray, pad: float = CROP_PAD, near_px: int = 25):
    """(x0, y0, x1, y1): the box of the temple mask and the photo matte near it (within near_px), padded."""
    t = np.asarray(temple_mask, bool)
    if not t.any():
        return None
    near = ndimage.binary_dilation(t, iterations=near_px) & np.asarray(matte, bool)
    ys, xs = np.nonzero(t | near)
    H, W = t.shape
    w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    mx, my = int(math.ceil(pad * w)), int(math.ceil(pad * h))
    return (int(max(0, xs.min() - mx)), int(max(0, ys.min() - my)), int(min(W, xs.max() + 1 + mx)),
            int(min(H, ys.max() + 1 + my)))
