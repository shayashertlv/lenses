"""Observations of a built candidate: host-owned cameras, silhouette/lens metrics, photo-matched renders, neutral
shape renders, the actual AR renderer.

The host owns every camera (fitted to each photo's matte with the same procedure for every candidate) and every
number; the author sees images and metrics, never chooses a camera to flatter a shape. Held-out views are fitted
and measured too, but written under ``heldout/`` and never placed in the author's turn package.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from bsa import archeck
from bsa import cameras as bcam
from bsa import raster
from bsa.core import NormFrame, camera_from_dict, px_per_mm, sha256_file
from bsa.front import rasterize as raster_polys
from reconstruction.camera import Camera

from . import export as mexport
from .worker import run_harness

LENS_PARTS = ("lens_R", "lens_L", "lens_C")
CANONICAL_VIEWS = (
    {"id": "clay_front", "kind": "clay", "yaw": 0, "pitch": 0, "ortho": True},
    {"id": "clay_right", "kind": "clay", "yaw": -90, "pitch": 0, "ortho": True},
    {"id": "clay_top", "kind": "clay", "yaw": 0, "pitch": 89, "ortho": True},
    {"id": "clay_back", "kind": "clay", "yaw": 180, "pitch": 0, "ortho": True},
    {"id": "clay_three_quarter", "kind": "clay", "yaw": 35, "pitch": 15, "ortho": False},
    {"id": "tex_front", "kind": "textured", "yaw": 0, "pitch": 0, "ortho": True},
    {"id": "tex_three_quarter", "kind": "textured", "yaw": 35, "pitch": 15, "ortho": False},
)
# 'side' (yaw 70, the harness allows 80): the near arm and the hardware inside a crystal temple, which no other view shows
AR_VIEWS = ({"id": "front", "yaw_degrees": 0}, {"id": "angled", "yaw_degrees": 35},
            {"id": "rolled", "roll_degrees": 25}, {"id": "back", "type": "asset-back"}, {"id": "side", "yaw_degrees": 70})
FITTED_VIEWS = ("front", "back", "left", "right", "angled")
UNSUPPORTED_VIEW_REASON = ("no camera prior for this view (bsa.cameras fits front, back, left, right and angled): no fitted render, "
                           "contour or IoU; judge it on the photo alone")
FIT_SEEDS_REFINE = 2
WARM_START_OFFSETS = ((0, 0), (0, 8), (0, -8), (6, 0), (-6, 0))
WARM_PRIOR_SEEDS = 12
# The NormFrame is the product's bbox centre and extent; a temple edit renormalised it (test-pilot-002 r0003: extent
# 156.2 -> 165.1 mm), so no parent camera could be evaluated as it was. A child keeps its parent's frame while its own
# bbox stays within these limits (a frame is only a normalisation; any fixed one images the same geometry).
FRAME_KEEP_EXTENT_RATIO = 0.15
FRAME_KEEP_CENTRE_SHIFT = 0.10     # of the parent's extent
# The lens camera: the first front camera, kept while the front piece (frame + lens parts) keeps its bbox within
# max(this, share of its width) per coordinate (test-pilot-002 r0001 -> r0006: 0.94 mm at most)
LENS_CAMERA_BBOX_TOL_MM = 1.0
LENS_CAMERA_BBOX_TOL_FRAC = 0.02
DECAL_INSIDE_SHARE = 0.95          # a non-lens object whose first hits lie on the lens footprint is a print on the lens
DECAL_MAX_SHARE = 0.25             # ... when it covers at most this share of that footprint (a shield brow is not a print)
LENS_GUARD_FLAGS = ("rim_invisible_to_matte", "low_contrast_high")     # modeler.intake: the front matte cannot see the rim
# EEVEE TAA samples of the observation renders. No measurement reads these images (every metric comes from the host
# rasterizer and the AR renderer); they only make the author's sheets and the sealed held-out render, and 16 (the
# harness default) keeps those readable at half the render cost of the 32 the first paid run spent (test-pilot-001:
# 543 s of a 12-minute build were 11 EEVEE renders on the worker's CPU rasterizer).
RENDER_SAMPLES = 16
# canonical render size: the sheets show these at grid cells of 360 (clay) and 480 (textured) px, so 540x360 renders
# land at the same on-sheet size as the earlier 720x480 with 44% fewer pixels to shade
CANONICAL_RENDER_SIZE = (540, 360)
# summary.lens_colour: the lens predicted over the front photo's backdrop against the photo (modeler.lens_colour)
LENS_COLOUR_SUMMARY_KEYS = ("basis", "match", "flags", "hue_error", "saturation_ratio", "value_ratio", "photo_hsv", "predicted_hsv",
                            "photo_rgb", "predicted_rgb", "backdrop_rgb", "mirrored", "lens_transmission_effective", "lens_reflection",
                            "lens_transmission_recommended", "lens_transmission_upper_bound", "lens_transmission_scale")


# --------------------------------------------------------------------------- mesh helpers
def mesh_of(objects: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """All exported triangles in the MODEL frame (mm) with a per-face part label."""
    V, F, P, off = [], [], [], 0
    for o in objects.values():
        if len(o["F"]) == 0:
            continue
        V.append(o["V"])
        F.append(o["F"] + off)
        P.append(np.full(len(o["F"]), o["part"], dtype=object))
        off += len(o["V"])
    if not F:
        raise ValueError("candidate has no triangles")
    return np.vstack(V), np.vstack(F), np.concatenate(P)


def norm_frame(V: np.ndarray) -> NormFrame:
    lo, hi = V.min(0), V.max(0)
    return NormFrame(tuple(float(x) for x in (lo + hi) / 2), float((hi - lo).max()))


def front_piece_centre(V: np.ndarray, F: np.ndarray, part: np.ndarray) -> np.ndarray:
    sel = np.isin(part, ("frame",) + LENS_PARTS)
    idx = np.unique(F[sel]) if sel.any() else np.arange(len(V))
    return V[idx].mean(0)


def front_piece_bbox(V: np.ndarray, F: np.ndarray, part: np.ndarray) -> list[list[float]]:
    """[[lo xyz], [hi xyz]] (mm) of the front piece: the frame and lens parts, no temple."""
    sel = np.isin(part, ("frame",) + LENS_PARTS)
    idx = np.unique(F[sel]) if sel.any() else np.arange(len(V))
    return [V[idx].min(0).round(3).tolist(), V[idx].max(0).round(3).tolist()]


def face_objects(objects: dict) -> np.ndarray:
    """The object index of every face, in ``mesh_of`` order (objects without faces skipped as there)."""
    ids = [np.full(len(o["F"]), k, np.int32) for k, o in enumerate(objects.values()) if len(o["F"])]
    return np.concatenate(ids) if ids else np.zeros(0, np.int32)


def see_through_faces(objects: dict, materials: dict) -> np.ndarray:
    """Faces whose material is a ``gl.material_translucent`` (kind 'translucent'): a photo sees the lens through them."""
    out = []
    for o in objects.values():
        if not len(o["F"]):
            continue
        names = list(o.get("materials") or [])
        kinds = np.array([(materials.get(n) or {}).get("kind") == "translucent" for n in names] + [False], bool)
        M = np.asarray(o.get("M", np.zeros(len(o["F"]), np.int64)), np.int64)
        out.append(kinds[np.where((M >= 0) & (M < len(names)), M, len(names))])
    return np.concatenate(out) if out else np.zeros(0, bool)


def sticky_frame(previous_cameras: dict | None, V: np.ndarray) -> tuple[NormFrame, str]:
    """(the frame, 'parent' | 'own'): the parent's NormFrame while this geometry's own bbox frame stays close to it, so
    a parent camera is still the same camera here; else the geometry's own bbox frame."""
    own = norm_frame(V)
    prev = next((v.get("frame") for v in (previous_cameras or {}).values() if isinstance(v, dict) and v.get("frame")), None)
    if prev is None:
        return own, "own"
    try:
        pf = NormFrame.from_dict(prev)
    except (KeyError, TypeError, ValueError):
        return own, "own"
    shift = float(np.linalg.norm(np.asarray(own.center) - np.asarray(pf.center)))
    if abs(own.extent / pf.extent - 1.0) <= FRAME_KEEP_EXTENT_RATIO and shift <= FRAME_KEEP_CENTRE_SHIFT * pf.extent:
        return pf, "parent"
    return own, "own"


def lens_camera_for(previous_front: dict | None, front_camera: dict, frame: NormFrame, fp_bbox: list) -> dict:
    """The camera the lens outline is measured under: the parent's lens camera (with its own frame) while the front
    piece keeps its bbox, else this revision's front camera. The whole-silhouette front fit moves with the temples
    (test-pilot-002 r0003: pitch 15.7 -> 17.0 with no lens edit, lens outline 1.311 -> 1.918 mm); the lens is judged
    against the photo under one camera until the front itself changes."""
    prev = (previous_front or {}).get("lens_camera")
    if prev and prev.get("camera") and prev.get("frame") and prev.get("front_piece_bbox_mm"):
        a, b = np.asarray(prev["front_piece_bbox_mm"], float), np.asarray(fp_bbox, float)
        tol = max(LENS_CAMERA_BBOX_TOL_MM, LENS_CAMERA_BBOX_TOL_FRAC * float(a[1, 0] - a[0, 0]))
        if a.shape == b.shape == (2, 3) and float(np.abs(a - b).max()) <= tol:
            return {"camera": dict(prev["camera"]), "frame": dict(prev["frame"]), "origin": "carried",
                    "front_piece_bbox_mm": prev["front_piece_bbox_mm"], "front_piece_bbox_shift_mm": round(float(np.abs(a - b).max()), 3)}
    return {"camera": dict(front_camera), "frame": frame.to_dict(), "origin": "fitted", "front_piece_bbox_mm": fp_bbox}


# --------------------------------------------------------------------------- camera fitting
def fit_roi(fg: np.ndarray) -> tuple[int, int, int, int]:
    """The native-metric ROI of a view: the matte bbox plus 15 % of its width (fixed per photo, camera independent)."""
    ys, xs = np.nonzero(fg)
    w = xs.max() - xs.min() + 1
    m = int(0.15 * w) + 8
    return (int(max(0, xs.min() - m)), int(max(0, ys.min() - m)), int(min(fg.shape[1], xs.max() + m + 1)), int(min(fg.shape[0], ys.max() + m + 1)))


def choose_camera(warm: dict, refit: dict) -> str:
    """'refit' only when the refit beats the parent camera (both evaluated as they are, on one level) on the fit loss AND
    on the native contour; else 'warm'. On test-pilot-002 the loss alone preferred the temple-driven front refit
    (0.2498 vs 0.2528) whose front contour was worse (1.27 vs 1.11 mm), and the back refit was worse on both."""
    return "refit" if refit["loss"] < warm["loss"] and refit["contour_mean_px"] < warm["contour_mean_px"] else "warm"


def _silhouette_unchanged(metrics: dict, record: dict) -> bool:
    """The parent camera sees exactly the parent's silhouette: every native pixel count equal (the rasterizer is
    deterministic), so nothing facing this photo changed."""
    try:
        return (int(metrics["photo_only_px"]) == int(record["photo_only_px"]) and int(metrics["render_only_px"]) == int(record["render_only_px"])
                and abs(float(metrics["contour_mean_px"]) - float(record["contour_mean_px"])) < 1e-6)
    except (KeyError, TypeError, ValueError):
        return False


def fit_view(view: str, fg: np.ndarray, scene: raster.RasterScene, frame: NormFrame, pivot_mm: np.ndarray,
             warm: Camera | None = None, *, warm_record: dict | None = None) -> dict:
    """One frozen camera for a photo: BSA's multi-start silhouette fit (coarse -> fit -> final levels) or, with a
    previous candidate's camera, a warm start around it plus the prior seeds nearest to it.

    ``warm_record`` (the parent's view record, with its ``frame``): when it is this frame, the parent camera is also
    evaluated AS IT IS. If it sees the parent's silhouette pixel for pixel, it is reused and no fit runs
    ('reused_parent'); else the refit replaces it only when it wins on the final-level loss AND the native contour
    (``choose_camera``; 'warm_kept' otherwise). test-pilot-002 r0004: the parent back camera (yaw 180.06, perspective
    0.224, 1.60 mm) was re-seeded at perspective 0.1, lost, and the fit returned yaw 186 / perspective 0.005 / 3.24 mm
    on an unchanged silhouette; every child warm-started from there."""
    t0 = time.time()
    fit = bcam.ViewFit(view, fg, scene, pivot=frame.to_norm(np.asarray(pivot_mm, float)[None])[0])
    roi = fit_roi(fg)
    verbatim = warm is not None and warm_record is not None and warm_record.get("frame") == frame.to_dict()
    source, comparison, res = "cold" if warm is None else "refit", None, None
    if verbatim and warm_record.get("roi") and list(warm_record["roi"]) == list(roi):
        wm = bcam.native_metrics(scene, warm, fg, roi)
        if _silhouette_unchanged(wm, warm_record):
            res = {"camera": warm, "loss_final_level": fit.final_losses([warm])[0]}
            source = "reused_parent"
    if res is None:
        seeds = bcam.prior_seeds(view)
        if warm is not None:
            seeds = ([(warm.yaw + dy, warm.pitch + dp, warm.roll, "warm") for dy, dp in WARM_START_OFFSETS]
                     + bcam.prior_seeds_near(view, warm.yaw, warm.pitch, WARM_PRIOR_SEEDS))
        fit.run_starts(seeds, FIT_SEEDS_REFINE)
        res = fit.refine()
        if verbatim:
            cams = [warm, res["camera"]]
            losses = fit.final_losses(cams)
            contours = [float(bcam.native_metrics(scene, c, fg, roi)["contour_mean_px"]) for c in cams]
            comparison = {"warm": {"loss": round(losses[0], 5), "contour_mean_px": round(contours[0], 3)},
                          "refit": {"loss": round(losses[1], 5), "contour_mean_px": round(contours[1], 3)}}
            if choose_camera({"loss": losses[0], "contour_mean_px": contours[0]}, {"loss": losses[1], "contour_mean_px": contours[1]}) == "warm":
                res = {"camera": warm, "loss_final_level": losses[0]}
                source = "warm_kept"
            else:
                res = dict(res, loss_final_level=losses[1])
    cam = res["camera"]
    metrics = bcam.native_metrics(scene, cam, fg, roi)
    mask = metrics.pop("mask")
    metrics.pop("face_id", None)
    # millimetres at the front piece for the views that look at it (front, back, angled): scale/extent is the magnification
    # at the NormFrame centre, mid-temple, x0.87-1.20 of the front piece's on the calibration assets (review M6); the side
    # views keep it (x1.00 +/- 0.02 there)
    ppm_centre = px_per_mm(cam, frame)
    ppm_rule = "front_piece_centre" if view in ("front", "back", "angled") else "projection_centre"
    ppm = bcam.px_per_mm_at(cam, frame, np.asarray(pivot_mm, float)) if ppm_rule == "front_piece_centre" else ppm_centre
    w = float(np.ptp(np.nonzero(fg.any(0))[0]) + 1)
    out = {"camera": cam.to_dict(), "loss": float(res.get("loss_final_level", res.get("loss_fit_level", float("nan")))),
           "px_per_mm": ppm, "px_per_mm_rule": ppm_rule, "px_per_mm_projection_centre": ppm_centre, "roi": [int(v) for v in roi], "evaluations": int(fit.evals), "seconds": round(time.time() - t0, 1),
           "iou": round(float(metrics["iou"]), 4),
           "contour_mean_px": float(metrics["contour_mean_px"]), "contour_p95_px": float(metrics["contour_p95_px"]),
           "contour_mean_mm": float(metrics["contour_mean_px"]) / ppm, "contour_p95_mm": float(metrics["contour_p95_px"]) / ppm,
           "contour_mean_pct_width": 100.0 * float(metrics["contour_mean_px"]) / max(w, 1.0),
           "photo_only_px": metrics["photo_only_px"], "render_only_px": metrics["render_only_px"], "extent": metrics["extent"],
           "camera_source": source}
    if comparison is not None:
        out["warm_comparison"] = comparison
    if warm is not None:
        # what moved: a camera move is not a shape change (the author read the r0004 back jump as a 'temple-pose mismatch')
        out["camera_delta_vs_parent"] = {"yaw": round(cam.yaw - warm.yaw, 3), "pitch": round(cam.pitch - warm.pitch, 3),
                                         "roll": round(cam.roll - warm.roll, 3), "perspective": round(cam.perspective - warm.perspective, 4),
                                         "scale_ratio": round(cam.scale / warm.scale, 4) if verbatim else None}
    return out, mask


def lens_front_metrics(scene: raster.RasterScene, lens_faces: np.ndarray, cam: Camera, frame: NormFrame, evidence_front: dict,
                       shape: tuple[int, int], *, face_object: np.ndarray | None = None, see_through_faces: np.ndarray | None = None) -> dict:
    """The lens region AS THE PHOTO SHOWS IT vs the measured lens outlines of the front photo, in mm at the lens.

    Lens pixels: the first hit is a lens face; or the ray meets a lens behind a PRINT on the lens (a non-lens object whose
    first hits lie >= ``DECAL_INSIDE_SHARE`` on the lens footprint and cover <= ``DECAL_MAX_SHARE`` of it: gl.lens_print's
    'Lens print', exported as part frame, punched its glyphs out of the mask and every glyph edge counted as outline:
    test-pilot-002 r0006 1.969 -> 1.494 mm) or behind a see-through (translucent) face (a crystal rim shows the lens under
    it). A lens edge under an opaque rim stays hidden, as in the photo. Millimetres at the lens centre
    (``bcam.px_per_mm_at``): scale/extent is the magnification at the NormFrame centre, mid-temple.

    Also reported: ``as_drawn`` (first-hit lens pixels, scale/extent millimetres: the instrument until 2026-09-28, for
    comparison), ``lens_only`` (the whole lens footprint whatever covers it) and ``projection`` (the camera pitch and the
    lens aspect h/w in the photo and in the render: the evidence outline is the photo's projection, not a pitch-corrected
    front view)."""
    if not lens_faces.any() or evidence_front is None:
        return {"status": "unmeasured", "reason": "no lens parts or no front measurement"}
    polys = [np.asarray(l["outline_px"], float) for l in evidence_front["lenses"]]
    ref = raster_polys(polys, shape) > 0
    r = scene.render(cam, shape, 1, None)
    fid = r["face_id"]
    hit = fid >= 0
    first = np.zeros(fid.shape, bool)
    first[hit] = lens_faces[fid[hit]]
    V_mm = frame.to_mm(scene.V_norm)
    lens_F = scene.F[lens_faces]
    footprint = raster.RasterScene(V_mm, lens_F, frame).render(cam, shape, 1, None)["mask"]
    covered = footprint & hit & ~first
    decal = np.zeros(fid.shape, bool)
    decal_objects = []
    if face_object is not None and len(face_object) == len(lens_faces) and covered.any():
        obj_hit = np.full(fid.shape, -1, np.int64)
        obj_hit[hit] = face_object[fid[hit]]
        fp_px = max(int(footprint.sum()), 1)
        for o in np.unique(obj_hit[covered]):
            pix = (obj_hit == o) & ~first
            inside = int((pix & footprint).sum())
            if inside >= DECAL_INSIDE_SHARE * int(pix.sum()) and inside <= DECAL_MAX_SHARE * fp_px:
                decal |= pix & footprint
                decal_objects.append(int(o))
    through = np.zeros(fid.shape, bool)
    if see_through_faces is not None and len(see_through_faces) == len(lens_faces) and covered.any():
        through[hit] = see_through_faces[fid[hit]]
        through &= covered
    m = first | decal | through
    centre = V_mm[np.unique(lens_F)].mean(0)
    ppm = bcam.px_per_mm_at(cam, frame, centre)
    ppm_old = px_per_mm(cam, frame)

    def stats(mask, scale):
        mean, p95 = bcam.contour_stats(mask, ref)
        inter, union = int((mask & ref).sum()), int((mask | ref).sum())
        return {"iou": round(inter / max(union, 1), 4), "contour_mean_px": float(mean), "contour_p95_px": float(p95),
                "contour_mean_mm": float(mean) / scale, "contour_p95_mm": float(p95) / scale, "lens_pixels": int(mask.sum())}
    main = stats(m, ppm)
    out = {"status": "measured", "iou": main["iou"], "contour_mean_px": main["contour_mean_px"], "contour_p95_px": main["contour_p95_px"],
           "contour_mean_mm": main["contour_mean_mm"], "contour_p95_mm": main["contour_p95_mm"], "lens_pixels_rendered": int(m.sum()),
           "lens_pixels_measured": int(ref.sum()), "px_per_mm": float(ppm), "px_per_mm_rule": "lens_centre",
           "px_per_mm_projection_centre": float(ppm_old),
           "as_drawn": stats(first, ppm_old), "lens_only": stats(footprint, ppm),
           "counted_as_lens": {"decal_pixels": int((decal & ~first).sum()), "see_through_pixels": int((through & ~first & ~decal).sum()),
                               "decal_objects": decal_objects}}
    out["projection"] = _lens_projection(cam, polys, footprint, shape)
    return out


def _lens_projection(cam: Camera, polys: list[np.ndarray], footprint: np.ndarray, shape) -> dict:
    """Camera pitch and per-lens bbox aspect (h/w) in the photo and in the render (review M7: through a camera fitted
    16-21 degrees from above, a lens copied from the projected photo outline renders about cos(pitch) shorter)."""
    photo, render = [], []
    for p in polys:
        x0, y0 = p.min(0)
        x1, y1 = p.max(0)
        photo.append(round(float((y1 - y0) / max(x1 - x0, 1e-9)), 4))
        mx, my = 0.2 * (x1 - x0), 0.2 * (y1 - y0)
        sub = footprint[max(0, int(y0 - my)):min(shape[0], int(y1 + my) + 1), max(0, int(x0 - mx)):min(shape[1], int(x1 + mx) + 1)]
        ys, xs = np.nonzero(sub)
        render.append(round(float((np.ptp(ys) + 1) / max(np.ptp(xs) + 1, 1)), 4) if len(xs) else None)
    return {"pitch_deg": float(cam.pitch), "cos_pitch": round(math.cos(math.radians(cam.pitch)), 4),
            "lens_aspect_hw": {"photo": photo, "render": render}}


# --------------------------------------------------------------------------- sheets
def _font(size: int = 14):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _label(im: Image.Image, text: str) -> Image.Image:
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, min(im.size[0], 8 + 7 * len(text)), 18], fill=(0, 0, 0))
    d.text((4, 2), text, fill=(255, 255, 255), font=_font(13))
    return im


def _edges(mask: np.ndarray) -> np.ndarray:
    return mask & ~ndimage.binary_erosion(mask)


def match_tile(photo_path: Path, render_path: Path, fg_crop: np.ndarray, title: str, height: int = 320) -> Image.Image:
    """[photo | render over light grey | photo with render silhouette (green) and matte edge (red)]."""
    photo = Image.open(photo_path).convert("RGB")
    render = Image.open(render_path).convert("RGBA")
    if render.size != photo.size:
        render = render.resize(photo.size, Image.BILINEAR)
    alpha = np.asarray(render)[..., 3] > 64
    bg = Image.new("RGB", render.size, (225, 225, 225))
    bg.paste(render, mask=render.split()[3])
    over = np.asarray(photo).copy()
    fg_e = _edges(fg_crop) if fg_crop is not None and fg_crop.shape == alpha.shape else None
    if fg_e is not None:
        over[ndimage.binary_dilation(fg_e)] = (255, 60, 60)
    over[ndimage.binary_dilation(_edges(alpha))] = (40, 255, 90)
    tiles = [_label(photo.copy(), f"{title}: photo"), _label(bg, "render (fitted camera)"), _label(Image.fromarray(over), "overlay: red photo edge, green render edge")]
    scale = height / photo.size[1]
    tiles = [t.resize((max(1, int(t.size[0] * scale)), height), Image.LANCZOS) for t in tiles]
    sheet = Image.new("RGB", (sum(t.size[0] for t in tiles) + 8 * (len(tiles) - 1), height), (255, 255, 255))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.size[0] + 8
    return sheet


def stack(images: list[Image.Image], gap: int = 8, background=(255, 255, 255)) -> Image.Image:
    w = max(i.size[0] for i in images)
    h = sum(i.size[1] for i in images) + gap * (len(images) - 1)
    out = Image.new("RGB", (w, h), background)
    y = 0
    for i in images:
        out.paste(i, (0, y))
        y += i.size[1] + gap
    return out


def grid(images: list[tuple[str, Path]], cols: int = 3, cell: int = 360, background=(255, 255, 255)) -> Image.Image:
    rows = math.ceil(len(images) / cols)
    out = Image.new("RGB", (cols * (cell + 8), rows * (cell + 8)), background)
    for k, (label, path) in enumerate(images):
        im = Image.open(path).convert("RGBA")
        bg = Image.new("RGB", im.size, (225, 225, 225))
        bg.paste(im, mask=im.split()[3])
        bg.thumbnail((cell, cell), Image.LANCZOS)
        tile = Image.new("RGB", (cell, cell), background)
        tile.paste(bg, ((cell - bg.size[0]) // 2, (cell - bg.size[1]) // 2))
        _label(tile, label)
        out.paste(tile, ((k % cols) * (cell + 8), (k // cols) * (cell + 8)))
    return out


AR_SHEET_COLUMNS = 2
AR_SHEET_TILE_HEIGHT = 360


def ar_sheet(renders: list[str], labels: list[str], out: Path) -> Path | None:
    """The AR views cropped to their union content box, ``AR_SHEET_COLUMNS`` per row (five views in one 480-px strip
    were 3444 px wide: each tile reached the author at about 430 px after the 2048-px image cap)."""
    pairs = [(p, lab) for p, lab in zip(renders, labels) if p and Path(p).is_file()]
    if not pairs:
        return None
    box = archeck.content_box([p for p, _ in pairs])
    tiles = []
    for p, lab in pairs:
        im = Image.open(p).convert("RGB").crop(box)
        im = im.resize((max(1, int(im.size[0] * AR_SHEET_TILE_HEIGHT / max(im.size[1], 1))), AR_SHEET_TILE_HEIGHT), Image.LANCZOS)
        tiles.append(_label(im, lab))
    rows = []
    for k in range(0, len(tiles), AR_SHEET_COLUMNS):
        chunk = tiles[k:k + AR_SHEET_COLUMNS]
        row = Image.new("RGB", (sum(t.size[0] for t in chunk) + 8 * (len(chunk) - 1), AR_SHEET_TILE_HEIGHT), (255, 255, 255))
        x = 0
        for t in chunk:
            row.paste(t, (x, 0))
            x += t.size[0] + 8
        rows.append(row)
    stack(rows).save(out)
    return out


def _content_crop(im: Image.Image, backdrop, margin: int = 12, threshold: int = 14) -> Image.Image:
    """``im`` cropped to the pixels that differ from a flat ``backdrop`` colour (the whole image when none does)."""
    a = np.asarray(im.convert("RGB")).astype(int)
    ys, xs = np.nonzero(np.abs(a - np.asarray(backdrop, float).round().astype(int)).max(-1) > threshold)
    if not len(xs):
        return im
    return im.crop((max(0, xs.min() - margin), max(0, ys.min() - margin), min(im.size[0], xs.max() + 1 + margin),
                    min(im.size[1], ys.max() + 1 + margin)))


def _front_photo_lens_crop(evidence: dict, margin: float = 0.25) -> Image.Image | None:
    """The front photo cropped to its measured lens outlines plus ``margin`` of their span: the photo's lens over the
    photo's own backdrop. From the native photo (``inputs``) when present, else the author crop mapped through its scale;
    the whole author photo when the front has no measured lens outline."""
    lenses = (evidence.get("front") or {}).get("lenses") or []
    pts = np.concatenate([np.asarray(l["outline_px"], float) for l in lenses]) if lenses else None
    if pts is None or not len(pts):
        ap = next((e.get("author_photo") for e in evidence.get("views", {}).values() if e.get("view") == "front" and e.get("author_photo")), None)
        return Image.open(ap["path"]).convert("RGB") if ap is not None and Path(ap["path"]).is_file() else None
    row = next((r for r in evidence.get("inputs", []) if r.get("view") == "front" and not r.get("held_out") and Path(r["path"]).is_file()), None)
    if row is not None:
        photo = Image.open(row["path"]).convert("RGB")
    else:
        ap = next((e.get("author_photo") for e in evidence.get("views", {}).values() if e.get("view") == "front" and e.get("author_photo")), None)
        if ap is None or not Path(ap["path"]).is_file():
            return None
        photo = Image.open(ap["path"]).convert("RGB")
        pts = (pts - np.asarray(ap["crop_xyxy"][:2], float)) * float(ap["scale"])
    (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
    mx, my = margin * (x1 - x0), margin * (y1 - y0)
    return photo.crop((int(max(0, x0 - mx)), int(max(0, y0 - my)), int(min(photo.size[0], x1 + mx + 1)), int(min(photo.size[1], y1 + my + 1))))


def lens_backdrop_sheet(evidence: dict, lens_colour: dict, out: Path, height: int = 360) -> Path | None:
    """The ``lens_backdrop`` sheet: [the photo's lenses over the photo's own backdrop | the runtime's front re-seen over
    that backdrop (the two solid-fixture renders) | the two lens-core colours as swatches]. The only image on which a
    tint can be judged: the skin-toned stand-in of the wearer renders has the hue of a brown lens (test-pilot-002's final
    evaluator read a lens the owner called too light as 'closely matching'), the checker shows through a clear one."""
    photo = _front_photo_lens_crop(evidence)
    if photo is None or not lens_colour.get("render_over_backdrop") or not Path(lens_colour["render_over_backdrop"]).is_file():
        return None
    runtime = _content_crop(Image.open(lens_colour["render_over_backdrop"]).convert("RGB"), lens_colour["backdrop_rgb"])
    tiles = []
    for im, text in ((photo, "front photo: the lenses on the photo's backdrop"),
                     (runtime, f"runtime over the photo's backdrop {tuple(int(round(x)) for x in lens_colour['backdrop_rgb'])}")):
        tiles.append(_label(im.resize((max(1, int(im.size[0] * height / max(im.size[1], 1))), height), Image.LANCZOS), text))
    swatch = Image.new("RGB", (200, height), (255, 255, 255))
    d = ImageDraw.Draw(swatch)
    half = height // 2
    for y0, key, text in ((0, "photo_rgb", "photo lens core"), (half, "predicted_rgb", "runtime lens core")):
        d.rectangle([0, y0, 199, y0 + half - 1], fill=tuple(int(round(x)) for x in lens_colour[key]))
        d.rectangle([0, y0, 8 + 7 * len(text), y0 + 18], fill=(0, 0, 0))
        d.text((4, y0 + 2), text, fill=(255, 255, 255), font=_font(13))
    tiles.append(swatch)
    sheet = Image.new("RGB", (sum(t.size[0] for t in tiles) + 8 * (len(tiles) - 1), height), (255, 255, 255))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.size[0] + 8
    sheet.save(out)
    return Path(out)


# --------------------------------------------------------------------------- the observation
def canonical_render_specs(px_per_mm_canon="fit", width: int = CANONICAL_RENDER_SIZE[0], height: int = CANONICAL_RENDER_SIZE[1]) -> list[dict]:
    specs = []
    for v in CANONICAL_VIEWS:
        cam = {"type": "orbit", "yaw": v["yaw"], "pitch": v["pitch"], "roll": 0, "ortho": v["ortho"], "px_per_mm": px_per_mm_canon,
               "target": "bbox", "distance_mm": 700}
        specs.append({"id": v["id"], "kind": v["kind"], "width": width, "height": height, "transparent": True, "camera": cam})
    return specs


def observe_candidate(cand_dir: Path, build: dict, evidence: dict, evidence_dir: Path, *, held_out_ids: set[str],
                      previous_cameras: dict | None = None, ar: bool = True, glb_path: Path | None = None,
                      render_scale: float = 1.0, time_limit_s: int = 300, render_harness=run_harness, heldout: bool = True) -> dict:
    """Fit cameras, measure, render photo-matched and canonical views, load the GLB in the AR renderer.
    Writes cand_dir/observe/observation.json (author-visible part) and cand_dir/heldout/heldout.json.
    ``render_harness``: the function that renders the saved scene (``worker.run_harness`` signature); the agentic
    route passes one that renders inside its isolated worker so no host Blender opens a generated scene.
    ``heldout``: False skips the held-out views entirely (no camera fit, no sealed render spec) and records
    ``heldout_skipped`` in observation.json and heldout.json; the caller passes False for a revision that failed the
    contract, whose sealed evaluation never happens. With True nothing else in the observation changes.
    ``previous_cameras``: the parent revision's view records. Their ``frame`` keeps the parent NormFrame
    (``sticky_frame``), so each parent camera is evaluated as it is (``fit_view``) and the parent's lens camera carried
    (``lens_camera_for``); records without one (before 2026-09-28) only seed the angles, as before.
    Every view record carries its photo matte's coverage of the glasses silhouette (``modeler.matte_coverage``) and,
    below ``COVERAGE_MIN``, ``reliable: false`` with the ``reason``: the contour/IoU then measure matte holes and the
    status never gates on them (``summary.unreliable_metrics``)."""
    t0 = time.time()
    obs_dir = cand_dir / "observe"
    held_dir = cand_dir / "heldout"
    obs_dir.mkdir(parents=True, exist_ok=True)
    held_dir.mkdir(parents=True, exist_ok=True)
    objects, materials, _ = mexport.load_parts(Path(build["parts_npz"]), Path(build["materials_json"]))
    V, F, part = mesh_of(objects)
    frame, frame_origin = sticky_frame(previous_cameras, V)
    scene = raster.RasterScene(V, F, frame)
    lens_sel = np.isin(part, LENS_PARTS)
    face_obj = face_objects(objects)
    see_through_sel = see_through_faces(objects, materials)
    pivot = front_piece_centre(V, F, part)
    fp_bbox = front_piece_bbox(V, F, part)
    masks = np.load(evidence_dir / "masks.npz")
    photo_paths = {r["id"]: r["path"] for r in evidence.get("inputs", [])}
    front_guard = [f for f in ((evidence.get("front") or {}).get("flags") or []) if f in LENS_GUARD_FLAGS]
    views = {}
    held = {}
    unsupported = {}
    render_specs = []
    held_specs = []
    held_renders = {}
    photo_tiles = []
    for vid, entry in list(evidence["views"].items()) + [(k, {"view": v["view"], "id": k, "held": True}) for k, v in evidence["held_out"].items()]:
        view = entry["view"]
        is_held = bool(entry.get("held") or vid in held_out_ids)
        if view not in FITTED_VIEWS:
            if not is_held:
                unsupported[vid] = UNSUPPORTED_VIEW_REASON      # said, not silently skipped (rear_angled, test-pilot-002)
            continue
        if not heldout and is_held:
            continue           # heldout=False: no camera fit and no sealed render for the held-out views
        fg = masks[f"fg_{vid}"]
        warm, warm_record = None, None
        if previous_cameras and vid in previous_cameras and "camera" in previous_cameras[vid]:
            warm = camera_from_dict(previous_cameras[vid]["camera"])
            if previous_cameras[vid].get("frame"):
                warm_record = previous_cameras[vid]
        try:
            fit, mask = fit_view(view, fg, scene, frame, pivot, warm, **({"warm_record": warm_record} if warm_record else {}))
        except Exception as e:  # noqa: BLE001 - a failed fit is a measured failure of this view
            fit, mask = {"status": "fit_failed", "error": f"{type(e).__name__}: {e}"}, None
        fit["view"] = view
        if "camera" in fit:
            fit["frame"] = frame.to_dict()
        cov = _matte_coverage(photo_paths.get(vid), fg, mirror=view == "front")
        if cov is not None:
            fit["matte_coverage"] = cov
            if cov.get("reliable") is False and "contour_mean_mm" in fit:
                from .matte_coverage import unreliable_reason
                fit["reliable"] = False
                fit["reason"] = unreliable_reason(view, cov)
        target = held if is_held else views
        if view == "front" and mask is not None and evidence.get("front"):
            lc = lens_camera_for((previous_cameras or {}).get(vid), fit["camera"], frame, fp_bbox)
            fit["lens_camera"] = lc
            fit["lens_outline"] = _lens_outline(V, F, scene, frame, lens_sel, face_obj, see_through_sel, lc, fit, evidence["front"], fg.shape,
                                                front_guard)
        target[vid] = fit
        if target is held and mask is not None and "camera" in fit:
            # evaluator-only photo-matched render of the held-out view: crop made here, never listed in observation.json
            photo_path = next((Path(r["path"]) for r in evidence["inputs"] if r["id"] == vid), None)
            if photo_path is not None and photo_path.is_file():
                from .intake import author_photo
                rgb = np.asarray(Image.open(photo_path).convert("RGB"))
                ap = author_photo(rgb, fg, held_dir / f"{vid}_photo.jpg")
                x0, y0, x1, y1 = ap["crop_xyxy"]
                s = ap["scale"]
                W, H = ap["size"]
                fg_crop = fg[y0:y1, x0:x1]
                if s != 1.0:
                    fg_crop = np.asarray(Image.fromarray(fg_crop.astype(np.uint8) * 255).resize((W, H), Image.BILINEAR)) > 127
                np.savez_compressed(held_dir / f"{vid}_fg.npz", fg=fg_crop)
                cam_h = bcam.resized_camera(camera_from_dict(fit["camera"]), s, x0, y0)
                held_specs.append({"id": f"heldout_{vid}", "kind": "textured", "width": int(W), "height": int(H), "transparent": True,
                                   "camera": {"type": "photo", "camera": cam_h.to_dict(), "frame": frame.to_dict()}})
                held_renders[vid] = {"photo_crop": str(held_dir / f"{vid}_photo.jpg"), "fg_crop": str(held_dir / f"{vid}_fg.npz"),
                                     "render": str(obs_dir / "renders" / f"heldout_{vid}.png")}
        if target is views and mask is not None and entry.get("author_photo"):
            ap = entry["author_photo"]
            x0, y0, x1, y1 = ap["crop_xyxy"]
            s = ap["scale"]
            cam = bcam.resized_camera(camera_from_dict(fit["camera"]), s, x0, y0)
            W, H = ap["size"]
            render_specs.append({"id": f"match_{vid}", "kind": "textured", "width": int(W), "height": int(H), "transparent": True,
                                 "camera": {"type": "photo", "camera": cam.to_dict(), "frame": frame.to_dict()}})
            fg_crop = fg[y0:y1, x0:x1]
            if s != 1.0:
                fg_crop = np.asarray(Image.fromarray(fg_crop.astype(np.uint8) * 255).resize((W, H), Image.BILINEAR)) > 127
            photo_tiles.append((vid, Path(ap["path"]), fg_crop))
    render_specs += canonical_render_specs()
    renders = {}
    render_result = None
    if build.get("blend"):
        render_result = render_harness({"mode": "render_only", "blend_path": build["blend"], "renders": render_specs + held_specs, "samples": RENDER_SAMPLES},
                                       obs_dir / "renders", time_limit_s=time_limit_s)
        for row in render_result.get("renders", []):
            if row.get("path") and not row["id"].startswith("heldout_"):
                renders[row["id"]] = row["path"]
        # move held-out renders out of the author-visible folder
        for vid, hr in held_renders.items():
            src = Path(hr["render"])
            if src.is_file():
                dst = held_dir / src.name
                src.replace(dst)
                hr["render"] = str(dst)
    sheets = {}
    match_tiles = []
    for vid, photo_path, fg_crop in photo_tiles:
        rp = renders.get(f"match_{vid}")
        if rp:
            match_tiles.append(match_tile(photo_path, Path(rp), fg_crop, vid))
    if match_tiles:
        p = obs_dir / "sheet_photo_match.png"
        stack(match_tiles).save(p)
        sheets["photo_match"] = str(p)
    clay = [(v["id"], Path(renders[v["id"]])) for v in CANONICAL_VIEWS if v["kind"] == "clay" and v["id"] in renders]
    if clay:
        p = obs_dir / "sheet_clay.png"
        grid(clay, cols=3).save(p)
        sheets["clay"] = str(p)
    tex = [(v["id"], Path(renders[v["id"]])) for v in CANONICAL_VIEWS if v["kind"] == "textured" and v["id"] in renders]
    if tex:
        p = obs_dir / "sheet_textured.png"
        grid(tex, cols=2, cell=480).save(p)
        sheets["textured"] = str(p)
    ar_result = None
    lens_reflection = None
    from . import pose_sweep as mps
    ar_views = tuple(AR_VIEWS) + tuple(mps.POSE_SWEEP_VIEWS)
    if ar and glb_path is not None and Path(glb_path).is_file():
        # one harness run: the AR sheet's views and the pose sweep (15 extra renders took 18 s on r0006)
        ar_result = archeck.run({"candidate": glb_path}, obs_dir / "ar", ar_views=ar_views, width_mm={"candidate": float(np.ptp(V[:, 0]))})
        m = ar_result["models"].get("candidate", {})
        by_view = _renders_by_view(m)
        ids = [v["id"] for v in AR_VIEWS if v["id"] in by_view]
        if ids:
            p = ar_sheet([by_view[i] for i in ids], ids, obs_dir / "sheet_ar.png")
            if p:
                sheets["ar"] = str(p)
        try:
            lens_reflection = mps.lens_reflection_metric(Path(glb_path), obs_dir / "ar")
            if lens_reflection.get("status") == "measured":
                p = mps.sweep_sheet(by_view, obs_dir / "sheet_pose_sweep.png", lens_reflection)
                if p:
                    sheets["pose_sweep"] = str(p)
        except Exception as e:  # noqa: BLE001 - recorded, never fatal
            lens_reflection = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
        ar_result = {k: v for k, v in ar_result.items() if k not in ("stdout_tail", "stderr_tail", "command")}
    summary = summarize(views, held, ar_result, glb_sha256=sha256_file(Path(glb_path)) if ar_result is not None else None,
                        expected_views=[v["id"] for v in ar_views])
    if unsupported:
        summary["fit_views_unsupported"] = unsupported
    summary["frame_origin"] = frame_origin
    if lens_reflection is not None:
        summary["lens_reflection"] = ({k: lens_reflection[k] for k in ("max_jump_px", "max_jump_share", "max_saturated_share", "flag", "threshold_jump_share")}
                                      if lens_reflection.get("status") == "measured" else {k: lens_reflection.get(k) for k in ("status", "reason", "error")})
    # the two solid-fixture runs of the front (skin, dark blue), made once: the lens colour is fitted from them for every
    # lens, the see-through measure reads them for a translucent frame; no run without a lens or a translucent material.
    # The checker render above stays for the AR sheet only: a transmissive lens over it reads as the checker
    fixture_runs = None
    if ar_result is not None and glb_path is not None:
        from . import see_through as mst
        try:
            fixture_runs = mst.render_fixtures(Path(glb_path), obs_dir, Path(build["materials_json"]), float(np.ptp(V[:, 0])))
        except Exception as e:  # noqa: BLE001 - recorded in both metrics, never fatal
            fixture_runs = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
    lens_colour = None
    if ar_result is not None and glb_path is not None:
        from . import lens_colour as mlc
        try:
            lens_colour = mlc.lens_colour_metric(Path(glb_path), fixture_runs, evidence, Path(build["materials_json"]),
                                                 out_png=obs_dir / "lens_over_photo_backdrop.png")
        except Exception as e:  # noqa: BLE001 - a colour metric failure is recorded, never fatal
            lens_colour = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
    if lens_colour and lens_colour.get("status") == "measured":
        summary["lens_colour"] = {k: lens_colour[k] for k in LENS_COLOUR_SUMMARY_KEYS if k in lens_colour}
        if lens_colour.get("lens_env_intensity_recommended") is not None:
            summary["lens_env_intensity_recommended"] = lens_colour["lens_env_intensity_recommended"]
        if lens_colour.get("render_over_backdrop"):
            # its own sheet (the final evaluator gets it too; until 2026-09-28 a row under the author's AR sheet only)
            try:
                p = lens_backdrop_sheet(evidence, lens_colour, obs_dir / "sheet_lens_backdrop.png")
                if p is not None:
                    sheets["lens_backdrop"] = str(p)
                    lens_colour["sheet"] = "lens_backdrop"
            except Exception as e:  # noqa: BLE001 - recorded, never fatal
                lens_colour["sheet_error"] = f"{type(e).__name__}: {e}"
    # translucent (crystal) fronts: the share of the background showing through the front's rim in the runtime, from
    # the two solid-fixture renders; not_applicable unless a gl.material_translucent is present
    see_through = None
    if ar_result is not None and glb_path is not None:
        from .see_through import frame_see_through_metric
        try:
            see_through = frame_see_through_metric(Path(glb_path), obs_dir, Path(build["materials_json"]), float(np.ptp(V[:, 0])),
                                                   rendered=fixture_runs)
        except Exception as e:  # noqa: BLE001 - recorded, never fatal
            see_through = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
    if see_through and see_through.get("status") == "measured":
        summary["frame_see_through"] = {k: see_through[k] for k in ("see_through", "per_channel", "frame_rgb_on_skin", "frame_pixels")}
    elif see_through and see_through.get("status") not in (None, "not_applicable"):
        # a translucent material was present but the measurement failed: the author must see that, not an absent key
        summary["frame_see_through"] = {"status": see_through.get("status"), "error": see_through.get("error")}
    # translucent temples: measured in the angled view of the same two runs, whatever became of the front's number
    # (no_frame_pixels / no_frame_projection results still carry it); absent for opaque temples (author RULE 59)
    if see_through:
        from .see_through import temple_summary
        ts = temple_summary(see_through)
        if ts:
            summary["temple_see_through"] = ts
        temple = see_through.get("temple_see_through") or {}
        shown = [(f"see-through fixture {k}", p) for k, p in (see_through.get("renders") or {}).items()
                 if see_through.get("status") == "measured"]
        shown += [(f"see-through temples {k}", p) for k, p in (temple.get("renders") or {}).items() if temple.get("status") == "measured"]
        if shown:
            try:
                tiles = [_label(Image.open(p).convert("RGB"), label) for label, p in shown]
                p = obs_dir / "sheet_see_through.png"
                stack(tiles).save(p)
                sheets["see_through"] = str(p)
            except Exception as e:  # noqa: BLE001
                see_through["sheet_error"] = f"{type(e).__name__}: {e}"
    # every metal and crystal material against the author photos at matched poses over the photos' own backdrop (one more
    # harness run, about 20 s): its colour, the crystal's visibility, the hardware's size (modeler.appearance)
    appearance = None
    if ar_result is not None and glb_path is not None:
        from . import appearance as mapp
        try:
            appearance = mapp.appearance_metric(obs_dir, Path(glb_path), views=views, frame=frame, V=V, F=F, part=part, objects=objects,
                                                materials=materials, evidence=evidence, width_mm=float(np.ptp(V[:, 0])),
                                                sheet_png=obs_dir / "sheet_material_match.png")
        except Exception as e:  # noqa: BLE001 - recorded, never fatal
            appearance = {"status": "failed", "reliable": False, "reason": f"{type(e).__name__}: {e}"}
        if appearance.get("status") != "not_applicable":
            summary["appearance"] = mapp.summary_of(appearance)
        if appearance.get("sheet"):
            sheets["material_match"] = appearance["sheet"]
    observation = {"seconds": round(time.time() - t0, 1), "frame": frame.to_dict(), "frame_origin": frame_origin, "triangles": int(len(F)),
                   "bbox_mm": [V.min(0).round(2).tolist(), V.max(0).round(2).tolist()],
                   "views": views, "sheets": sheets, "renders": renders, "ar": ar_result, "summary": summary, "lens_colour": lens_colour,
                   "frame_see_through": see_through, "lens_reflection": lens_reflection, "appearance": appearance,
                   "render_result": {k: render_result.get(k) for k in ("ok", "error", "process")} if render_result else None}
    heldout_record = {"views": held, "summary": summarize(held, {}, None), "renders": held_renders}
    if not heldout:
        observation["heldout_skipped"] = True
        heldout_record["heldout_skipped"] = True
    (obs_dir / "observation.json").write_text(json.dumps(observation, indent=1, default=_json_default), encoding="utf-8")
    (held_dir / "heldout.json").write_text(json.dumps(heldout_record, indent=1, default=_json_default), encoding="utf-8")
    return observation


def summarize(views: dict, held: dict, ar_result: dict | None, *, glb_sha256: str | None = None,
              expected_views: list[str] | None = None) -> dict:
    """The numbers the incumbent rule and the report use. With an AR result, ``ar_report_valid`` is the shared
    validator's verdict on it (the run succeeded, the candidate row is compatible for the GLB whose sha256 is
    ``glb_sha256``, every render of ``expected_views`` (default: AR_VIEWS) exists and hashes): ``Candidate.valid``
    requires it True.

    ``unreliable_metrics`` names each summary metric measured on a matte that covers too little of the silhouette (or,
    for the lens outline, under a front the intake flags invisible to its matte) with the reason: ``evaluate.decide_status``
    reports such a metric and never gates on it. ``mean_contour_mm_reliable_views`` averages the reliable views only;
    ``matte_coverage`` and ``mirror_iou`` (front matte vs complete silhouette) say why."""
    fit = {k: v for k, v in views.items() if "contour_mean_mm" in v}
    front = next((v for v in fit.values() if v["view"] == "front"), None)
    sides = [v for v in fit.values() if v["view"] in ("left", "right")]
    back = next((v for v in fit.values() if v["view"] == "back"), None)
    out = {"fit_views_measured": sorted(fit), "fit_views_failed": sorted(k for k, v in views.items() if "contour_mean_mm" not in v)}
    unreliable = {}
    if front:
        out["front_contour_mean_mm"] = round(front["contour_mean_mm"], 3)
        out["front_contour_p95_mm"] = round(front["contour_p95_mm"], 3)
        out["front_iou"] = front["iou"]
        if front.get("reliable") is False:
            unreliable["front_contour_mean_mm"] = front["reason"]
        lo = front.get("lens_outline") or {}
        if lo.get("status") == "measured":
            out["lens_outline_mean_mm"] = round(lo["contour_mean_mm"], 3)
            out["lens_outline_p95_mm"] = round(lo["contour_p95_mm"], 3)
            if lo.get("as_drawn"):
                out["lens_outline_as_drawn_mean_mm"] = round(lo["as_drawn"]["contour_mean_mm"], 3)
            if lo.get("reliable") is False:
                unreliable["lens_outline_mean_mm"] = lo["reason"]
        mc = front.get("matte_coverage") or {}
        if mc.get("mirror_iou_complete") is not None:
            from .matte_coverage import MIRROR_GAIN_MIN, MIRROR_IOU_LOW
            # an artefact of the matte when the complete silhouette is symmetric, or when the matte is unreliable and filling its
            # holes gains MIRROR_GAIN_MIN (test-pilot-002: 0.844 -> 0.941, a knife-edge 0.001 above the flag's 0.94; the
            # complete silhouette under-fills clear crystal, so the gain is the robust evidence)
            matte_mi, full_mi = (mc.get("mirror_iou_matte") or 0.0), mc["mirror_iou_complete"]
            artefact = matte_mi < MIRROR_IOU_LOW and (full_mi >= MIRROR_IOU_LOW or (mc.get("reliable") is False and full_mi - matte_mi >= MIRROR_GAIN_MIN))
            out["mirror_iou"] = {"matte": mc.get("mirror_iou_matte"), "complete": full_mi, "threshold": MIRROR_IOU_LOW, "matte_artefact": bool(artefact)}
    if sides:
        out["side_contour_mean_mm"] = round(float(np.mean([v["contour_mean_mm"] for v in sides])), 3)
        bad = [v["reason"] for v in sides if v.get("reliable") is False]
        if bad:
            unreliable["side_contour_mean_mm"] = bad[0]
    if back:
        out["back_contour_mean_mm"] = round(back["contour_mean_mm"], 3)
        if back.get("reliable") is False:
            unreliable["back_contour_mean_mm"] = back["reason"]
    if fit:
        out["mean_contour_mm_all_fit_views"] = round(float(np.mean([v["contour_mean_mm"] for v in fit.values()])), 3)
        ok = [v["contour_mean_mm"] for v in fit.values() if v.get("reliable") is not False]
        out["mean_contour_mm_reliable_views"] = round(float(np.mean(ok)), 3) if ok else None
        bad = sorted(k for k, v in fit.items() if v.get("reliable") is False)
        if bad:
            unreliable["mean_contour_mm_all_fit_views"] = f"includes views whose photo matte is unreliable: {bad}"
    cov = {k: {"view": v["view"], "coverage": v["matte_coverage"].get("coverage"), "reliable": v["matte_coverage"].get("reliable")}
           for k, v in views.items() if isinstance(v.get("matte_coverage"), dict) and "coverage" in v["matte_coverage"]}
    if cov:
        out["matte_coverage"] = cov
    if unreliable:
        out["unreliable_metrics"] = unreliable
    if ar_result is not None:
        m = ar_result.get("models", {}).get("candidate", {})
        out["ar_runtime_compatible"] = bool(m.get("runtime_compatible"))
        out["ar_optical_meshes"] = m.get("optical_meshes_detected")
        out["ar_error"] = m.get("error")
        out["ar_continuity_failure"] = m.get("continuity_failure")
        out["ar_continuity_measured"] = m.get("continuity_measured")
        val = archeck.validate_ar_result(ar_result, expected_models={"candidate": glb_sha256},
                                         expected_views=expected_views or [v["id"] for v in AR_VIEWS])
        out["ar_report_valid"] = bool(val["ok"])
        if not val["ok"]:
            out["ar_report_reasons"] = (val["reasons"] + (val["models"].get("candidate") or {}).get("reasons", []))[:8]
    return out


def _renders_by_view(m: dict) -> dict:
    """{view id: render path} of one harness row (``render_files`` when present, else the file names' last field)."""
    if m.get("render_files"):
        return {str(r["view"]): r["path"] for r in m["render_files"] if r.get("path")}
    return {Path(r).stem.split("__")[-1]: r for r in (m.get("renders") or [])}


def _matte_coverage(photo_path: str | None, fg: np.ndarray, *, mirror: bool = False) -> dict | None:
    if not photo_path or not Path(photo_path).is_file():
        return None
    from .matte_coverage import view_coverage
    try:
        return view_coverage(photo_path, fg, mirror=mirror)
    except Exception as e:  # noqa: BLE001 - recorded, never fatal
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


def _lens_outline(V, F, scene, frame, lens_sel, face_obj, see_through_sel, lc: dict, fit: dict, evidence_front: dict, shape,
                  guard_flags: list[str]) -> dict:
    """The front's lens outline under the lens camera (``lens_camera_for``), rendered in that camera's own frame; with a
    carried camera also this revision's own front camera (``current_camera``). ``reliable`` is false when the front
    photo's matte is unreliable (the camera was fitted on matte holes) or the intake flags the rim invisible to it."""
    lframe = NormFrame.from_dict(lc["frame"])
    lscene = scene if lframe.to_dict() == frame.to_dict() else raster.RasterScene(V, F, lframe)
    kw = {"face_object": face_obj, "see_through_faces": see_through_sel}
    lo = lens_front_metrics(lscene, lens_sel, camera_from_dict(lc["camera"]), lframe, evidence_front, shape, **kw)
    if lo.get("status") != "measured":
        return lo
    lo["camera"] = {"origin": lc["origin"], "pitch_deg": float(lc["camera"]["pitch"])}
    if lc["origin"] == "carried":
        cur = lens_front_metrics(scene, lens_sel, camera_from_dict(fit["camera"]), frame, evidence_front, shape, **kw)
        lo["current_camera"] = {k: cur.get(k) for k in ("contour_mean_mm", "contour_p95_mm", "iou")}
        lo["current_camera"]["as_drawn_mean_mm"] = (cur.get("as_drawn") or {}).get("contour_mean_mm")
    reasons = []
    if fit.get("reliable") is False:
        reasons.append(f"its camera was fitted on an unreliable matte ({fit['reason']})")
    if guard_flags:
        reasons.append(f"the intake flags the front {guard_flags}: the matte cannot see the rim")
    lo["reliable"] = not reasons
    if reasons:
        lo["reason"] = "; ".join(reasons)
    return lo


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    return str(o)
