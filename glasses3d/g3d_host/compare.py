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

WP6 v3 (WP6_V3 §5, P4: geometry is judged geometrically):

- model edges: the creases the lab sends from the build, by identity (``{pair, shape, deg, pts}``), not mesh dihedrals.
  ``edge_runs`` projects posed 3D polylines and keeps the samples the model does not hide (``raster.cast`` at each
  sample's own sub-pixel position, so the frame and the other temple hide them as in the render; ``edge_runs_posed``
  builds that scene from swapped temple bodies for variants), optionally carrying a
  per-point value (a signed angle) along each run; ``draw_edges`` draws them ridge orange / valley blue / flat grey,
  2 px from EDGE_WIDE_DEG 6 deg and 1 px below (a run with per-sample angles segment by segment, ``edge_shape_of``),
  and labels each pair once at its longest visible run (``run_label`` of the build's own angles, deg_at or range:
  "16°", "-3°", "24-33°", "-8 to -3°"; negative = valley; ``deg_text``); a soft facet|base border (``soft``) dashed
  thin grey and never labelled; lines go on whole pixels (``line_px``: 1 px on the nearest, 2 px on floor and
  floor + 1);
  ``edges_with_runs`` pairs edges with their runs for it; ``overlay_matched(..., edges=...)`` draws them instead of
  the mesh crease polylines;
- ``outline_offsets``: a side view's top / bottom outline, photo minus model along the image's up / down direction,
  per 10 mm station of one temple, None with its reason (OUTLINE_REASONS) where it is not measured;
- ``geometry_view``: the worker's ``normal`` pass (world normal x 0.5 + 0.5) as a light-free false-colour panel, each
  channel stretched over a region (the zoom: the temple bodies' pixels) at most 8 x (a colour step is where the
  surface turns; tone is never a depth cue);
- ``zoom_scale``: a zoom panel's scale k = min(ZOOM_MAX_K 2, ZOOM_PANEL_PX / w, / h): native or larger when the box
  fits the panel, the render made at k (never an upscaled render); ``resample_box``: a photo box on a crop camera's
  exact pixel grid at k (bilinear: no ringing after a stretch);
- ``backdrop_share``: the share of the model a render panel shows in the backdrop's colour (the compare then draws
  the model's outline on its render panels).
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
from .camera import CAMERA_FIELDS, EYE_FIELDS, Camera, NormFrame, project_mm
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


class UnusableReference(ValueError):
    """A retained photo has diagnostic evidence but no accepted camera."""

    code = "E_UNUSABLE_CAMERA"

    def __init__(self, rid: str, details: dict):
        self.ref_id, self.details = rid, dict(details)
        error = details.get("error")
        reason = details.get("refusal") or details.get("message") or (error.get("message")
                  if isinstance(error, dict) else error)
        super().__init__(f"{rid}: camera fit {details.get('status') or 'unavailable'}: "
                         f"{reason or 'no successful fitted camera'} ({self.code})")


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


def _fit_usable(fit: dict) -> bool:
    status = fit.get("status")
    if status not in (None, "ok", "failed", "refused", "unusable"):
        raise ValueError(f"unknown camera fit status {status!r}")
    return status in (None, "ok") and not fit.get("refusal")


def _valid_camera(value, rid: str) -> Camera:
    try:
        if not isinstance(value, dict) or any(isinstance(value.get(k), bool) for k in CAMERA_FIELDS + EYE_FIELDS):
            raise ValueError("camera fields must be numeric")
        camera = Camera.from_dict(value)
        if not all(math.isfinite(getattr(camera, k)) for k in CAMERA_FIELDS + EYE_FIELDS) or camera.scale <= 0:
            raise ValueError("camera fields must be finite with positive scale")
        return camera
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{rid}: malformed successful camera: {exc}") from exc


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
        self._verify_evidence()

    def _verify_evidence(self):
        """A failed newest fit may be skipped; damaged evidence may never be skipped."""
        source = self.dir / CAMERAS_JSON
        if sha256_file(source) != self.meta.get("cameras_json_sha256"):
            raise ValueError(f"{source}: cameras.json differs from the frozen basis")
        doc = json.loads(source.read_text(encoding="utf-8"))
        images = doc["images"]
        model = doc["model"]
        if self.id != self.dir.name or any(self.meta.get(k) != model.get(k) for k in
                                           ("frame", "front_pivot_mm", "front_parts")):
            raise ValueError("the frozen basis identity or camera frame differs from its source")
        if list(self.skip) != list(model.get("skip") or ()):
            raise ValueError("the frozen skip list differs from its source")
        if (len(self.frame.center) != 3 or not all(map(math.isfinite, self.frame.center))
                or not math.isfinite(self.frame.extent) or self.frame.extent <= 0
                or self.pivot_mm.shape != (3,) or not np.isfinite(self.pivot_mm).all()):
            raise ValueError("the frozen camera frame or pivot is malformed")
        if not isinstance(self.refs, dict) or not isinstance(images, dict):
            raise ValueError("a frozen basis needs a reference and image catalog")
        if len(self.refs) != len(images) or {r.get("image") for r in self.refs.values()} != set(images):
            raise ValueError("the frozen reference catalog differs from cameras.json")
        for rid, ref in self.refs.items():
            raw, fit = images[ref["image"]], ref.get("fit") or {}
            if ref.get("camera") != raw.get("camera") or any(fit.get(k) != raw.get(k) for k in
                                                             ("status", "refusal")):
                raise ValueError(f"{rid}: camera or fit status differs from the frozen source")
            if any(value != raw.get(key) for key, value in fit.items() if key in FIT_EVIDENCE_FIELDS):
                raise ValueError(f"{rid}: fit diagnostic evidence differs from cameras.json")
            if ref.get("lens_loss") != raw.get("lens_loss"):
                raise ValueError(f"{rid}: lens loss evidence differs from cameras.json")
            for key in ("theta_deg", "hinge_axis_xz_mm", "theta_observed", "fit_status"):
                if ref.get(key) != raw.get(key):
                    raise ValueError(f"{rid}: frozen pose or camera lineage differs from cameras.json ({key})")
            # Older imports omitted transport; when present it must still describe the frozen source.
            if "transport" in ref and ref["transport"] != raw.get("transport"):
                raise ValueError(f"{rid}: frozen camera transport differs from cameras.json")
            if raw.get("image_sha256") and ref.get("photo_sha256") != raw["image_sha256"]:
                raise ValueError(f"{rid}: photo identity differs from cameras.json")
            photo = self.photo_path(rid)
            if sha256_file(photo) != ref.get("photo_sha256"):
                raise ValueError(f"{photo}: the photo differs from the one the basis was fitted on")
            for key in ("matte", "overlay"):
                if ref.get(key):
                    path = (self.dir / ref[key]).resolve()
                    if not path.is_relative_to(self.dir.resolve()):
                        raise ValueError(f"{rid}: frozen {key} escapes its basis directory")
                    digest = ref.get(key + "_sha256")
                    # Older bases have no overlay digest; every new basis records one.
                    if (key == "matte" or digest is not None) and sha256_file(path) != digest:
                        raise ValueError(f"{path}: {key} differs from basis.json (a frozen basis must not change)")
            if self.is_usable(rid):
                _valid_camera(ref.get("camera"), rid)
                if not ref.get("matte") or not ref.get("roi") or not ref.get("shape"):
                    raise ValueError(f"{rid}: successful camera lacks frozen matte, shape or ROI")
                if (len(ref["shape"]) != 2 or any(type(v) is not int or v <= 0 for v in ref["shape"])
                        or len(ref["roi"]) != 4 or any(type(v) is not int for v in ref["roi"])):
                    raise ValueError(f"{rid}: frozen shape or ROI is malformed")

    def is_usable(self, rid: str) -> bool:
        """Only a successful fit can supply a camera; a refused candidate remains diagnostic."""
        return _fit_usable(self.refs[rid].get("fit") or {})

    def usable_refs(self) -> list[str]:
        return [rid for rid in self.refs if self.is_usable(rid)]

    def unusable_refs(self) -> dict[str, dict]:
        return {rid: {"image": ref["image"], **ref.get("fit", {})}
                for rid, ref in self.refs.items() if not self.is_usable(rid)}

    def camera(self, rid: str) -> Camera:
        if not self.is_usable(rid):
            raise UnusableReference(rid, self.unusable_refs()[rid])
        return _valid_camera(self.refs[rid]["camera"], rid)

    def shape(self, rid: str) -> tuple[int, int]:
        h, w = self.refs[rid]["shape"]
        return int(h), int(w)

    def roi(self, rid: str) -> tuple[int, int, int, int]:
        return tuple(int(v) for v in self.refs[rid]["roi"])

    def matte(self, rid: str) -> np.ndarray:
        if rid not in self._mattes:
            rec = self.refs[rid]
            if rec.get("matte") is None:
                raise UnusableReference(rid, self.unusable_refs().get(rid, {"status": "unavailable"}))
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
        if th is None and ax is None:
            return None
        if (not isinstance(th, dict) or not isinstance(ax, dict) or not th or set(th) != set(ax)
                or set(th) - {"+1", "-1"}):
            raise ValueError(f"{rid}: opening angles and hinge axes must name the same explicit sides")
        from .pose import parse_pose
        result = {k: {"theta_deg": th[k], "axis_xz_mm": ax[k]} for k in th}
        try:
            parse_pose(result)
        except (TypeError, KeyError, ValueError) as error:
            raise ValueError(f"{rid}: invalid frozen temple pose: {error}") from error
        return {k: {"theta_deg": float(th[k]), "axis_xz_mm": [float(v) for v in ax[k]]} for k in th}

    def colourway(self, rid: str) -> str:
        return self.refs[rid].get("colourway") or "target"

    def summary(self) -> dict:
        return {"id": self.id, "dir": str(self.dir), "reason": self.meta.get("reason"),
                "source": self.meta.get("source"), "model_sha256": (self.meta.get("model") or {}).get("sha256"),
                "refs": list(self.refs), "usable_refs": self.usable_refs(), "unusable_refs": self.unusable_refs()}


def load_basis(job_root, basis_id: str | None = None) -> Basis:
    """The named diagnostic basis or newest usable one. Damaged evidence always refuses."""
    ids = list_bases(job_root)
    if basis_id is None:
        if not ids:
            raise NoBasis("this job has no camera basis yet: run fit_cameras(reason) first (E_NO_BASIS)")
        for candidate in reversed(ids):
            basis = Basis(basis_root(job_root) / candidate, job_root)
            if basis.usable_refs():
                return basis
        raise NoBasis(f"no successful camera in bases {ids}: inspect their fit diagnostics and run fit_cameras "
                      "again (E_NO_BASIS)")
    if basis_id not in ids:
        raise NoBasis(f"no camera basis {basis_id!r} (bases: {ids or 'none'}; fit_cameras makes one)")
    return Basis(basis_root(job_root) / basis_id, job_root)


OPTIONAL_IMAGE_FIELDS = ("theta_deg", "theta_observed", "hinge_axis_xz_mm", "colourway", "fit_status", "lens_loss")
FIT_EVIDENCE_FIELDS = ("status", "code", "message", "error", "refusal", "flags", "iou", "contour_mean_px",
                       "contour_mean_mm", "contour_p90_px", "contour_p90_mm", "px_per_mm_at_front_pivot",
                       "candidates", "all_candidates", "loss", "loss_common_level", "sign", "matte", "seconds",
                       "iou_lens_optional", "lens_pixels_optional", "contour_mean_px_lens_optional",
                       "contour_p95_px_lens_optional", "render_only_px_lens_optional", "usability", "off_axis")


def freeze_basis(job_root, basis_dir, photos: dict, *, reason: str, call_id: str | None, source: str,
                 model: dict | None = None) -> Basis:
    """Freeze ``basis_dir`` (which holds a fit's ``cameras.json``): check every photo against the fit's sha256,
    compute and store available mattes once, check the ROI, and write ``basis.json``. Failed photos remain as
    diagnostic refs without fabricated cameras/mattes. ``photos`` = {image name: path}.

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
        if rid in refs:
            raise ValueError(f"image names share reference id {rid!r}; no photo may replace another")
        if name not in photos:
            raise ValueError(f"no photo given for {name} (photos: {sorted(photos)})")
        p = Path(photos[name]).resolve()
        sha = sha256_file(p)
        if rec.get("image_sha256") and sha != rec["image_sha256"]:
            raise ValueError(f"{p}: sha256 {sha[:12]} != the fit's {rec['image_sha256'][:12]} for {name}")
        usable = _fit_usable(rec)
        if usable:
            _valid_camera(rec.get("camera"), rid)
        mode = rec.get("matte_mode") or "default"
        fg = None
        # The fitter reports whether it reached matte generation. In particular, a decoded shape alone does not
        # turn a failed matte into evidence. Legacy successful/refused fits always computed this same matte.
        available = usable or rec.get("status") == "refused" or rec.get("matte") is not None or mode == "supplied"
        if available:
            if mode == "supplied":
                mf = (basis_dir / rec["matte_file"]).resolve()
                if not mf.is_relative_to(basis_dir.resolve()):
                    raise ValueError(f"{name}: supplied matte escapes its basis directory")
                if sha256_file(mf) != rec.get("matte_file_sha256"):
                    raise ValueError(f"{name}: supplied matte {mf} does not match its matte_file_sha256")
                fg = np.asarray(Image.open(mf).convert("L")) > 127
            elif mode in M.MODES:
                fg = M.matte(M.load_photo(p), mode=mode)
            else:
                raise ValueError(f"{name}: matte mode {mode!r} is not a matte.py mode and no matte was supplied")
            if list(fg.shape) != rec.get("shape"):
                raise ValueError(f"{name}: photo shape {list(fg.shape)} != the fit's {rec.get('shape')}")
        roi, mp = None, None
        if fg is not None:
            if fg.any():
                roi = cameras.matte_roi(fg)
                if rec.get("roi") is not None and list(roi) != list(rec["roi"]):
                    notes.append(f"{rid}: recomputed matte ROI {list(roi)} != the fit's {rec['roi']}; the fit's ROI is kept")
                    roi = tuple(rec["roi"])
            elif usable:
                raise ValueError(f"{name}: successful camera has an empty matte")
            mp = basis_dir / "mattes" / f"{rid}.png"
            Image.fromarray((fg * 255).astype(np.uint8)).save(mp)
        overlay = basis_dir / f"overlay_{rid}.png"
        if not overlay.is_file() and rec.get("overlay"):
            overlay = basis_dir / Path(rec["overlay"]).name
        overlay = overlay if overlay.is_file() else None
        refs[rid] = {"image": name, "photo": _rel(p, job_root), "photo_sha256": sha, "shape": rec.get("shape"),
                     "view_kind": rec.get("view_kind"), "view": rec.get("view"), "camera": rec.get("camera"),
                     "roi": [int(v) for v in roi] if roi is not None else None,
                     "matte": f"mattes/{rid}.png" if mp else None, "matte_sha256": sha256_file(mp) if mp else None,
                     "matte_mode": mode, "fg_px": int(fg.sum()) if fg is not None else None,
                     "overlay": overlay.name if overlay else None,
                     "overlay_sha256": sha256_file(overlay) if overlay else None,
                     "fit": {k: rec.get(k) for k in FIT_EVIDENCE_FIELDS}}
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
    for name, rec in json.loads(src.read_text(encoding="utf-8"))["images"].items():
        if rec.get("matte_mode") == "supplied":
            target = (d / rec["matte_file"]).resolve()
            if not target.is_relative_to(d.resolve()):
                raise ValueError("supplied matte escapes its basis directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src.parent / rec["matte_file"], target)
        if rec.get("overlay"):
            original = Path(rec["overlay"])
            original = original if original.is_absolute() else src.parent / original
            if original.is_file():
                shutil.copyfile(original, d / f"overlay_{ref_id(name)}.png")
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


def labelled(img: Image.Image, label: str, size: int = 16, *, fit: bool = False) -> Image.Image:
    """``img`` under a one-line label. ``fit``: the label's font shrinks (down to 10 px) until it fits the image's
    width, and a label still too wide ends in "..." (a zoom panel can be narrower than its label; the full label is in
    the call's text)."""
    img = img.convert("RGBA")
    pad = size + 8
    out = Image.new("RGBA", (img.width, img.height + pad), (255, 255, 255, 255))
    out.paste(img, (0, pad), img)
    dr = ImageDraw.Draw(out)
    font = _font(size)
    if fit:
        room = img.width - 8
        for px in range(size, 9, -1):
            font = _font(px)
            if dr.textlength(label, font=font) <= room:
                break
        while len(label) > 3 and dr.textlength(label, font=font) > room:
            label = label[:-4].rstrip() + "..."
    dr.text((4, 3), label, fill=(20, 20, 20, 255), font=font)
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
FLAT_RGB = (160, 160, 160)
EDGE_RGB = {"ridge": RIDGE_RGB, "valley": VALLEY_RGB, "flat": FLAT_RGB}
EDGE_SHAPES = tuple(EDGE_RGB)
EDGE_WIDE_DEG = 6.0             # a model edge of at least this angle is drawn 2 px wide, below it 1 px (WP6_V3 §5)
EDGE_FLAT_DEG = 2.0             # a per-point signed angle below this is drawn flat (= g3d/temple/spec FLAT_CREASE_DEG)
EDGE_RANGE_DEG = 2.0            # a label reads "min-max°" when the angle along its visible run spans >= 2 deg
EDGE_STEP_PX = 1.0              # model edges are sampled (and depth-tested) at <= 1 image px
EDGE_LABEL_PX = 14
SOFT_RGB = FLAT_RGB             # a soft facet|base border (I1: a soft facet's run with no sharp edge, so smooth-shaded
SOFT_DASH_PX = (4.0, 3.0)       # in the build, WP6_V3 §2): dashed thin grey, 1 px, 4 px on / 3 px off along the run;
#                                 never labelled with an angle (WP6 v3 I7; its fold is in the build's crease lines)
EDGE_RANGE_TOL_DEG = 0.1        # a model edge's deg / deg_at lie in its own range within this (values printed to 0.1);
#                                 more is the roll-band mix-up of probe1 ("crown|shoulder 36.9 (37.7-39.3)", "1..39")
CLIP_LEVELS = 3                 # a render pixel within 3 levels of the backdrop (every channel) reads as backdrop
GRID_RGB, LABEL_RGB = (0, 150, 90), (0, 90, 50)
ZOOM_MAX_K = 2.0                # a zoom panel is never more than 2 x native (WP6_V3 §5)
ZOOM_PANEL_PX = (760, 500)      # a zoom panel's largest (w, h); the 2 x 2 grid stays under SHEET_MAX_PX
GEOMETRY_MIN_SPAN = 32.0        # geometry_view: a channel's stretch never exceeds 255 / 32 = 8 x (8-bit noise)
GEOMETRY_BG = (118, 118, 118)
OUTLINE_WALK_MM = 45.0          # outline_offsets walks this far from a temple's centre for its edge (> half its height)
OUTLINE_REASONS = ("off image", "covered", "off temple", "no model edge", "no photo edge")   # outline_offsets' Nones
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
    right, up, toward = raster.camera_basis(cam)
    lateral_eye = cam.eye_x * right + cam.eye_y * up
    p = float(cam.perspective)
    out = []
    ua, ub = project_mm(edges["a"], cam, frame), project_mm(edges["b"], cam, frame)
    for k in range(len(edges["a"])):
        n_s = int(max(2, math.ceil(float(np.linalg.norm(ub[k] - ua[k])) / step_px) + 1))
        t = np.linspace(0.0, 1.0, n_s)[:, None]
        P = edges["a"][k] * (1 - t) + edges["b"][k] * t
        Pn = frame.to_norm(P)
        view = (toward / p + lateral_eye - Pn) if p > 0 else np.broadcast_to(toward, Pn.shape)
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


def overlay_matched(base8: np.ndarray, model_mask: np.ndarray | None, polylines: list, grid: dict | None,
                    edges: list | None = None, labels: bool = False, open_border: bool = False) -> Image.Image:
    """The (stretched) photo with the chart grid (green, mm labels), the model outline (magenta; None: no outline)
    and on top either the mesh crease polylines (orange ridge, blue valley) or, when ``edges`` is given (WP6 v3), the
    model's own edges instead (``draw_edges``; ``labels``: their angle labels). ``open_border``: the model goes on
    past the image's border (a zoom), so no outline is drawn along it."""
    img = np.array(base8[..., :3], np.uint8, copy=True)
    if grid is not None:
        g = grid["d_mask"] | grid["y_mask"]
        img[g] = (0.45 * img[g] + 0.55 * np.array(GRID_RGB)).astype(np.uint8)
    if model_mask is not None:
        m = np.asarray(model_mask, bool)
        img[cameras._edge(np.pad(m, 1, mode="edge"))[1:-1, 1:-1] if open_border else cameras._edge(m)] = OUTLINE_RGB
    im = Image.fromarray(img)
    dr = ImageDraw.Draw(im)
    if edges is not None:
        draw_edges(im, edges, labels=labels)
    else:
        for pts, shape in polylines:
            dr.line(line_px(pts, 2), fill=RIDGE_RGB if shape > 0 else VALLEY_RGB, width=2)
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
    """One image: each (label, image) row labelled above it (the label fitted to the row's width: a narrow crop
    shrinks its font, then shortens it), stacked top to bottom on white."""
    tiles = [labelled(im, label, fit=True) for label, im in rows]
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
    """The downscale k (<= 1, never up) at which n_rows labelled crop rows stack within max_px on both sides. Each row
    is round(crop_h x k) px tall, up to 0.5 px more than crop_h x k, so n_rows x 0.5 px are kept for the rounding (a
    3-row black-side stack came out 1025 px tall without it)."""
    k_h = (max_px - n_rows * label_px - gap * (n_rows - 1) - 0.5 * n_rows) / max(1, n_rows * crop_h)
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


# ------------------------------------------------------------------------------------------------ WP6 v3 seeing
def edge_runs(polylines: list, cam: Camera, frame: NormFrame, scene, shape: tuple[int, int],
              tol_mm: float = CREASE_DEPTH_TOL_MM, step_px: float = EDGE_STEP_PX, values: list | None = None) -> list:
    """Per polyline ((K, 3) posed model mm, K >= 2) its visible runs [(M, 2) image px, M >= 2] in the camera's image
    of ``shape`` (H, W). Every segment is sampled in 3D at <= step_px of image length (its ends exact); a sample shows
    when it projects inside the image and the first hit of the ray through its own sub-pixel position (``scene.cast``,
    the posed model the image shows) is not nearer than the sample's depth less tol_mm, or nothing is hit (a sample on
    the silhouette). A model edge lies on the surface, so its own ray meets the surface there unless something nearer
    hides it: the frame, the other temple, or the temple's own body when the camera sees the face from inside. The
    exact ray (not the depth image's pixel centre) keeps a valley seen at a grazing angle from being hidden by its own
    faces' slope across half a pixel. ``values`` (per polyline None or (K,) numbers aligned with its points, e.g. a
    signed crease angle per point): every run then has a third column, the value interpolated linearly along each
    segment (nan for a polyline without values)."""
    H, W = shape
    if not polylines:
        return []
    P_all, owner, val_all = [], [], []
    for i, P in enumerate(polylines):
        P = np.asarray(P, float).reshape(-1, 3)
        uv = project_mm(P, cam, frame)
        n = np.maximum(1, np.ceil(np.linalg.norm(np.diff(uv, axis=0), axis=1) / step_px).astype(int))
        seg = np.repeat(np.arange(len(P) - 1), n)
        t = (np.arange(len(seg)) - np.repeat(np.cumsum(n) - n, n)) / np.repeat(n, n)
        S = np.vstack([P[seg] + t[:, None] * (P[seg + 1] - P[seg]), P[-1:]])
        P_all.append(S)
        owner.append(np.full(len(S), i))
        if values is not None:
            v = None if values[i] is None else np.asarray(values[i], float).reshape(-1)
            val_all.append(np.full(len(S), np.nan) if v is None else np.r_[v[seg] + t * (v[seg + 1] - v[seg]), v[-1:]])
    S, owner = np.vstack(P_all), np.concatenate(owner)
    uv = project_mm(S, cam, frame)
    if values is not None:
        uv = np.column_stack([uv, np.concatenate(val_all)])
    inside = (uv[:, 0] >= -0.5) & (uv[:, 0] <= W - 0.5) & (uv[:, 1] >= -0.5) & (uv[:, 1] <= H - 0.5)
    vis = np.zeros(len(S), bool)
    if inside.any():
        r = scene.cast(cam, uv[inside, 0], uv[inside, 1])
        d = to_norm_depth(S[inside], cam, frame)
        vis[inside] = ~r["hit"] | (d <= r["depth"].astype(np.float64) + tol_mm)
    same = np.r_[False, owner[1:] == owner[:-1]]
    prev = np.r_[False, vis[:-1]] & same
    nxt = np.r_[vis[1:], False] & np.r_[same[1:], False]
    out = [[] for _ in polylines]
    for s, e in zip(np.flatnonzero(vis & ~prev).tolist(), (np.flatnonzero(vis & ~nxt) + 1).tolist()):
        if e - s >= 2:
            out[int(owner[s])].append(uv[s:e])
    return out


def edge_runs_posed(polylines: list, bodies: list, occluder, cam: Camera, frame: NormFrame,
                    shape: tuple[int, int], values: list | None = None) -> list:
    """``edge_runs`` against a scene built here (never in raster.get_scene's LRU cache) of temple bodies [(V mm,
    triangles)] already posed in the camera's scene plus ``occluder`` (V mm, triangles: the rest of the scene posed the
    same way; None = the bodies alone): the model edges of a variant whose bodies are not the head's (the lab's
    facet_variants sheets); ``values`` as edge_runs'."""
    Vs, Fs, n = [], [], 0
    for V, F in list(bodies) + ([occluder] if occluder is not None else []):
        Vs.append(np.asarray(V, np.float64))
        Fs.append(np.asarray(F, np.int64) + n)
        n += len(V)
    if not n:
        return [[] for _ in polylines]
    return edge_runs(polylines, cam, frame, raster.RasterScene(np.concatenate(Vs), np.concatenate(Fs), frame), shape,
                     values=values)


def edges_with_runs(edges: list, runs: list) -> list:
    """The model edges ``draw_edges`` takes: each edge {pair, shape, deg, range?, soft?, side?} with its visible runs
    (``edge_runs`` / ``edge_runs_posed``, aligned with ``edges``): {pair, shape, deg, side, runs} plus range and soft
    when given (soft only when true). The lab's variant panels and seeing's overlays build them alike, so a soft border
    is dashed and a label reads the build's own angles in both."""
    out = []
    for e, rr in zip(edges, runs):
        d = {"pair": e["pair"], "shape": e["shape"], "deg": e["deg"], "side": e.get("side"), "runs": rr}
        if e.get("range") is not None:
            d["range"] = e["range"]
        if e.get("soft"):
            d["soft"] = True
        out.append(d)
    return out


def deg_text(lo: float, hi: float | None = None) -> str:
    """An angle label in whole degrees with its unit: one angle "16°", "-3°"; a range lo..hi (least-largest) "16-29°"
    when lo >= 0, else "-8 to -3°" / "-9 to 4°" (a hyphen between two signed numbers would read as a minus). Why: the
    old "16..29" read as "16.29" at the size Astra is sent the zoom (the two dots ~2 px apart in a 1218 px image,
    v3p3 review), beside the receipt's "(15.7-29.2)" and the fit's "+- 2.7°" for the same crease."""
    a = int(round(float(lo)))
    if hi is None:
        return f"{a}°"
    b = int(round(float(hi)))
    return f"{a}-{b}°" if a >= 0 else f"{a} to {b}°"


def edge_label(deg: float, shape: str) -> str:
    """A model edge's angle label in whole degrees, negative for a valley ("16°", "-3°"): a signed angle is shown as
    given, an unsigned valley's negated."""
    v = -abs(float(deg)) if shape == "valley" else float(deg)
    return deg_text(v)


def edge_shape_of(deg) -> np.ndarray:
    """The shape of signed angles (+ ridge): "ridge" from +EDGE_FLAT_DEG, "valley" from -EDGE_FLAT_DEG, else "flat"
    (the builder's crease classes)."""
    d = np.asarray(deg, float)
    return np.where(d >= EDGE_FLAT_DEG, "ridge", np.where(d <= -EDGE_FLAT_DEG, "valley", "flat"))


def run_label(run, deg: float, shape: str, rng=None) -> tuple[str, str]:
    """(text, shape) of a model edge's label at one visible run, from the build's own angles only (WP6 v3 I1/I7: never
    an angle seeing measures, so no roll-band edge can enter it): with a signed angle per sample (a run's third
    column, ``edge_runs(values=...)``: the edge's ``deg_at``) its least-largest in whole degrees when it spans >=
    EDGE_RANGE_DEG along the run ("24-33°", "-8 to -3°": ``deg_text``), else the run's mean ("25°"), shaped by the
    mean; without one, an edge with a ``rng`` [lo, hi] (I1's form: signed over its whole length, ``deg`` signed too)
    reads lo-hi when it spans >= EDGE_RANGE_DEG, else its deg as given, shaped by deg (``edge_shape_of``: the
    crease's vote may differ in sign); else ``edge_label`` of the edge's own angle and shape."""
    run = np.asarray(run, float)
    if run.shape[1] >= 3 and np.isfinite(run[:, 2]).all():
        lo, hi, mean = float(run[:, 2].min()), float(run[:, 2].max()), float(run[:, 2].mean())
        sh = str(edge_shape_of(mean))
        if hi - lo >= EDGE_RANGE_DEG:
            return deg_text(lo, hi), sh
        return deg_text(mean), sh
    if rng is not None:
        sh = str(edge_shape_of(float(deg)))
        if float(rng[1]) - float(rng[0]) >= EDGE_RANGE_DEG:
            return deg_text(rng[0], rng[1]), sh
        return deg_text(deg), sh
    return edge_label(deg, shape), shape


def line_px(pts, width: int) -> list:
    """Whole-pixel (u, v) points for ``ImageDraw.line`` (pixel centres at integers: the compare's map u_img = (u - x0
    + 0.5) k - 0.5). Pillow (12.3) truncates float points toward zero, so a 1 px line at v 30.6 or 30.9 went on row 30
    (0.5 px up-left on average, up to 0.9 px; a v in (-1, -0.5) on row 0) and a 2 px line at v in (-1, 0) on rows 0-1:
    width 1 goes on the nearest pixel, floor(v + 0.5); wider (2 px, which Pillow draws on p and p + 1) on floor(v),
    so it covers rows floor(v) and floor(v) + 1, centred within 0.5 px of v. The computed points (records, checks)
    keep their sub-pixel values; only the pixels drawn change."""
    P = np.asarray(pts, float)
    if not P.size:
        return []
    P = np.floor(P.reshape(len(P), -1)[:, :2] + (0.5 if width <= 1 else 0.0)).astype(np.int64)
    return [(int(u), int(v)) for u, v in P]


def dashed_line(dr: ImageDraw.ImageDraw, pts, fill, width: int = 1, dash=SOFT_DASH_PX) -> None:
    """A polyline drawn dashed: ``dash`` = (on, off) image px measured along it from its first point, each dash cut
    exactly at its ends (the runs are sampled at <= 1 px, so a dash follows the line's own bends), then put on whole
    pixels (``line_px``)."""
    P = np.asarray(pts, float)[:, :2]
    if len(P) < 2:
        return
    cum = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    on, off = (float(v) for v in dash)
    for s0 in np.arange(0.0, cum[-1], on + off):
        s1 = min(s0 + on, float(cum[-1]))
        inner = P[(cum > s0) & (cum < s1)]
        ends = [(float(np.interp(s, cum, P[:, 0])), float(np.interp(s, cum, P[:, 1]))) for s in (s0, s1)]
        dr.line(line_px(np.vstack([ends[:1], inner.reshape(-1, 2), ends[1:]]), width), fill=fill, width=width)


def draw_edges(im: Image.Image, edges: list, *, labels: bool = False, font_px: int = EDGE_LABEL_PX) -> list:
    """Draw model edges [{pair, shape (ridge | valley | flat), deg, runs [(M, 2) px], range? [lo, hi], soft?}] on
    ``im`` (in place): ridge orange, valley blue, flat grey; 2 px wide from EDGE_WIDE_DEG, 1 px below; the weaker drawn
    first. A run with a signed angle per sample (third column, ``edge_runs(values=...)``) is drawn segment by segment,
    each by its own angle's sign and size (``edge_shape_of``; a warp face's crease changes along its length, and may
    change sign). A soft edge (``soft`` true: I1 marks only a soft facet's base border run with no sharp edge, so it is
    smooth-shaded in the build, WP6_V3 §2) is drawn first, dashed thin grey (SOFT_RGB, 1 px, SOFT_DASH_PX) whatever its
    shape and angles, and never labelled: the render shows no line there, and an angle beside it would read as a
    crease's (probe1 drew shoulder|base soft as a valley "-5..1"); its fold stays in the build's crease lines. Every
    line goes on whole pixels (``line_px``). ``labels``: each other pair labelled once (``run_label`` of the build's
    angles: "16°", "-3°", "24-33°", "-8 to -3°") at the middle of its longest visible run, beside the line (the first
    of a few positions along it and on either side that stays inside the image and clear of the labels already
    placed; else the middle), in its shape's colour outlined black. Returns [(pair, text, (u, v) the label's
    centre)]."""
    dr = ImageDraw.Draw(im)
    for e in sorted(edges, key=lambda e: (not e.get("soft"), e["shape"] != "flat", abs(float(e["deg"])))):
        w = 2 if abs(float(e["deg"])) >= EDGE_WIDE_DEG else 1
        for run in e["runs"]:
            run = np.asarray(run, float)
            if e.get("soft"):
                dashed_line(dr, run, SOFT_RGB, 1, SOFT_DASH_PX)
            elif run.shape[1] >= 3 and np.isfinite(run[:, 2]).all():
                segv = (run[:-1, 2] + run[1:, 2]) / 2
                cls = [(str(sh), 2 if abs(v) >= EDGE_WIDE_DEG else 1) for sh, v in zip(edge_shape_of(segv), segv)]
                j = 0
                while j < len(cls):                       # consecutive segments of one class as one polyline
                    k = j
                    while k + 1 < len(cls) and cls[k + 1] == cls[j]:
                        k += 1
                    dr.line(line_px(run[j:k + 2], cls[j][1]), fill=EDGE_RGB[cls[j][0]], width=cls[j][1])
                    j = k + 1
            else:
                dr.line(line_px(run, w), fill=EDGE_RGB.get(e["shape"], FLAT_RGB), width=w)
    if not labels:
        return []
    best: dict = {}
    for e in edges:
        if e.get("soft"):
            continue
        for run in e["runs"]:
            L = float(np.linalg.norm(np.diff(np.asarray(run, float)[:, :2], axis=0), axis=1).sum())
            if L > 0 and (e["pair"] not in best or L > best[e["pair"]][0]):
                best[e["pair"]] = (L, np.asarray(run, float), e)
    f = _font(font_px)
    placed, out = [], []
    for pair, (L, run, e) in sorted(best.items(), key=lambda kv: -kv[1][0]):
        text, lshape = run_label(run, e["deg"], e["shape"], e.get("range"))
        run = run[:, :2]
        x0, y0, x1, y1 = dr.textbbox((0, 0), text, font=f, stroke_width=2)
        tw, th = x1 - x0, y1 - y0
        cum = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(run, axis=0), axis=1))]
        cands = []
        for frac in (0.5, 0.35, 0.65, 0.2, 0.8):
            j = int(np.clip(np.searchsorted(cum, frac * L), 1, len(run) - 1))
            p, q = run[j - 1], run[j]
            dv = q - p
            dv = dv / max(float(np.hypot(*dv)), 1e-9)
            n = np.array([-dv[1], dv[0]])
            gap = 5.0 + 0.5 * (abs(n[0]) * tw + abs(n[1]) * th)
            cands += [(p + q) / 2 + s * gap * n for s in (1.0, -1.0)]
        box = None
        for c in cands:
            b = (c[0] - tw / 2, c[1] - th / 2, c[0] + tw / 2, c[1] + th / 2)
            if b[0] >= 0 and b[1] >= 0 and b[2] <= im.width and b[3] <= im.height and not any(
                    b[0] < o[2] and o[0] < b[2] and b[1] < o[3] and o[1] < b[3] for o in placed):
                box = b
                break
        box = box or (cands[0][0] - tw / 2, cands[0][1] - th / 2, cands[0][0] + tw / 2, cands[0][1] + th / 2)
        placed.append(box)
        dr.text((box[0] - x0, box[1] - y0), text, fill=EDGE_RGB.get(lshape, FLAT_RGB), font=f, stroke_width=2,
                stroke_fill=(0, 0, 0))
        out.append((pair, text, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)))
    return out


def outline_offsets(stations: list, model_mask: np.ndarray, temple_mask: np.ndarray, matte: np.ndarray,
                    reach_mm: float = OUTER_REACH_MM, walk_mm: float = OUTLINE_WALK_MM) -> list:
    """A side view's outline offsets of one temple: per station {d_mm, offset_mm, reason}. A station is {d_mm, uv (a
    point inside that temple at d), dir (unit image px, outward: the image's up for the top edge, down for the
    bottom), px_per_mm (mm along dir)}: walk from uv along dir; the model's edge is where that temple's own pixels end
    (``temple_mask``: first hits on its parts). The photo's edge is the matte's end along the line nearest the
    model's (within reach_mm): offset_mm = (photo - model) / px_per_mm, + = the photo reaches further out (higher for
    top, lower for bottom), reason None. Otherwise offset_mm None and the reason (OUTLINE_REASONS): "off image" (uv,
    or the line before the temple ends, leaves the image), "covered" (another part of the model lies on uv, or the
    model mask goes on within 1.5 px past the temple's end: the frame or the other temple), "off temple" (uv misses
    the model), "no model edge" (the temple does not end within the walk), "no photo edge" (the matte has no end
    within reach_mm of the model's). The line is sampled every 0.25 px into the pixel it falls in (round half up for
    both masks alike), so an edge is placed within 0.125 px and a difference of two edges carries no rounding bias."""
    H, W = model_mask.shape
    model_mask, temple_mask, matte = (np.asarray(m, bool) for m in (model_mask, temple_mask, matte))
    out = []
    for st in stations:
        rec = {"d_mm": st["d_mm"], "offset_mm": None, "reason": None}
        out.append(rec)
        uv, e, ppm = np.asarray(st["uv"], float), np.asarray(st["dir"], float), float(st["px_per_mm"])
        t = np.arange(0.0, (walk_mm + reach_mm) * ppm + 0.125, 0.25)
        pts = uv[None] + t[:, None] * e[None]
        iu, iv = np.floor(pts[:, 0] + 0.5).astype(int), np.floor(pts[:, 1] + 0.5).astype(int)
        ok = (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)

        def along(mask):
            s = np.zeros(len(t), bool)
            s[ok] = mask[iv[ok], iu[ok]]
            return s
        tem, mod = along(temple_mask), along(model_mask)
        if not tem[0]:
            rec["reason"] = "off image" if not ok[0] else "covered" if mod[0] else "off temple"
            continue
        j = int(np.argmin(tem))                      # the first sample off the temple
        if tem[j] or not ok[j]:
            rec["reason"] = "off image" if not ok[j] else "no model edge"
            continue
        tm = (t[j - 1] + t[j]) / 2
        if (mod & (t > tm) & (t <= tm + 1.5)).any():
            rec["reason"] = "covered"
            continue
        s = along(matte)
        ends = np.nonzero(s[:-1] & ~s[1:] & ok[:-1] & ok[1:])[0]
        te = (t[ends] + t[ends + 1]) / 2
        te = te[np.abs(te - tm) <= reach_mm * ppm]
        if len(te):
            rec["offset_mm"] = round(float(te[np.argmin(np.abs(te - tm))] - tm) / ppm, 2)
        else:
            rec["reason"] = "no photo edge"
    return out


def geometry_view(rgba8: np.ndarray, region: np.ndarray | None = None, min_span: float = GEOMETRY_MIN_SPAN,
                  bg=GEOMETRY_BG) -> np.ndarray:
    """The worker's ``normal`` pass (8-bit RGBA, world normal x 0.5 + 0.5) as a light-free false-colour panel: each
    channel stretched from its p2 to its p98 over ``region`` (e.g. the temple bodies' pixels, so the frame and the
    rolled rim, whose normals differ most, do not take the range; with fewer than 100 pixels or None: the model's
    pixels, alpha > 0.5), the span never under min_span levels (at most 255 / min_span x: 8-bit steps stay small),
    over a neutral grey; what lies outside the range is clipped. A flat face is one colour, a crease a colour step, a
    warped face a gradient; nothing depends on a light."""
    a = np.asarray(rgba8)
    rgb = a[..., :3].astype(np.float64)
    alpha = (a[..., 3].astype(np.float64) / 255.0)[..., None] if a.shape[-1] == 4 else np.ones(a.shape[:2] + (1,))
    m = alpha[..., 0] > 0.5
    if region is not None and (np.asarray(region, bool) & m).sum() >= 100:
        m = np.asarray(region, bool) & m
    out = rgb.copy()
    if m.sum() >= 10:
        for c in range(3):
            lo, hi = (float(v) for v in np.percentile(rgb[..., c][m], (2.0, 98.0)))
            if hi - lo < min_span:
                mid = (lo + hi) / 2
                lo, hi = mid - min_span / 2, mid + min_span / 2
            out[..., c] = np.clip((rgb[..., c] - lo) * 255.0 / (hi - lo), 0.0, 255.0)
    comp = out * alpha + np.asarray(bg, np.float64) * (1.0 - alpha)
    return np.round(comp).astype(np.uint8)


def zoom_scale(w: int, h: int, max_k: float = ZOOM_MAX_K, panel=ZOOM_PANEL_PX) -> float:
    """A zoom panel's scale for a native box w x h: min(max_k, panel w / w, panel h / h) (>= 1 whenever the box fits
    the panel; the render is made at this scale)."""
    return float(min(max_k, panel[0] / max(1, w), panel[1] / max(1, h)))


def resample_box(a: np.ndarray, box, k: float, shape: tuple[int, int], resample=None) -> np.ndarray:
    """The native box (x0, y0, x1, y1) of an image at scale k on a crop camera's exact pixel grid (blender_camera
    crop_camera, cameras.resized_camera): output pixel (i, j) of ``shape`` (H, W) samples native (x0 + (j + 0.5) / k -
    0.5, y0 + (i + 0.5) / k - 0.5), so a photo and a render made at k register exactly (resizing the box to round(w k)
    px scales it by round(w k) / w, up to 0.5 output px off at the far edge). ``resample`` defaults to BILINEAR, whose
    kernel is never negative, so a stretched photo shows no ringing halo beside an edge (LANCZOS overshoots up to 43
    levels after the stretch at k 2); Pillow scales its support when it shrinks (area-like) and it keeps the grid
    within 0.08 native px at k 0.37-0.93 (BOX, which drops partial pixels, moves it up to 0.46 px). At k 1 on the
    box's own size: the box itself. Beyond the image the edge pixels repeat. ``a``: uint8 (H, W, 3) or float32
    (H, W)."""
    x0, y0, x1, y1 = (int(v) for v in box)
    H, W = (int(v) for v in shape)
    if k == 1.0 and (x1 - x0, y1 - y0) == (W, H):
        return np.asarray(a[y0:y1, x0:x1])
    Ha, Wa = a.shape[:2]
    bw, bh = W / k, H / k
    sx0, sy0 = max(0, x0 - 2), max(0, y0 - 2)
    sx1, sy1 = min(Wa, math.ceil(x0 + bw) + 2), min(Ha, math.ceil(y0 + bh) + 2)
    px, py = max(0, math.ceil(x0 + bw) - Wa), max(0, math.ceil(y0 + bh) - Ha)
    sub = np.asarray(a[sy0:sy1, sx0:sx1])
    if px or py:
        sub = np.pad(sub, ((0, py), (0, px)) + ((0, 0),) * (sub.ndim - 2), mode="edge")
    im = Image.fromarray(np.ascontiguousarray(sub))
    out = im.resize((W, H), Image.BILINEAR if resample is None else resample,
                    box=(x0 - sx0, y0 - sy0, x0 - sx0 + bw, y0 - sy0 + bh))
    return np.asarray(out)


def backdrop_share(shown8: np.ndarray, alpha: np.ndarray, bg_shown8, levels: int = CLIP_LEVELS) -> float:
    """The share of the model's pixels (alpha > 0.5) shown within ``levels`` of the shown backdrop in every channel:
    a render face the stretch clips to the backdrop's colour (a white top face on a white floor) loses its edge there,
    so the compare draws the model's outline on its render panels and states this share."""
    m = np.asarray(alpha, float) > 0.5
    if not m.any():
        return 0.0
    d = np.abs(np.asarray(shown8, np.int64)[..., :3][m] - np.asarray(bg_shown8, np.int64)[:3]).max(axis=1)
    return float((d <= levels).mean())
