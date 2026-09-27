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
from bsa.core import NormFrame, camera_from_dict, px_per_mm
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
AR_VIEWS = ({"id": "front", "yaw_degrees": 0}, {"id": "angled", "yaw_degrees": 35},
            {"id": "rolled", "roll_degrees": 25}, {"id": "back", "type": "asset-back"})
FIT_SEEDS_REFINE = 2
WARM_START_OFFSETS = ((0, 0), (0, 8), (0, -8), (6, 0), (-6, 0))


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


# --------------------------------------------------------------------------- camera fitting
def fit_view(view: str, fg: np.ndarray, scene: raster.RasterScene, frame: NormFrame, pivot_mm: np.ndarray,
             warm: Camera | None = None) -> dict:
    """One frozen camera for a photo: BSA's multi-start silhouette fit (coarse -> fit -> final levels) or, with a
    previous candidate's camera, a warm start around it plus the prior seeds nearest to it."""
    t0 = time.time()
    fit = bcam.ViewFit(view, fg, scene, pivot=frame.to_norm(np.asarray(pivot_mm, float)[None])[0])
    seeds = bcam.prior_seeds(view)
    if warm is not None:
        seeds = [(warm.yaw + dy, warm.pitch + dp, warm.roll, "warm") for dy, dp in WARM_START_OFFSETS] + seeds[:12]
    fit.run_starts(seeds, FIT_SEEDS_REFINE)
    res = fit.refine()
    cam = res["camera"]
    ys, xs = np.nonzero(fg)
    w = xs.max() - xs.min() + 1
    m = int(0.15 * w) + 8
    roi = (max(0, xs.min() - m), max(0, ys.min() - m), min(fg.shape[1], xs.max() + m + 1), min(fg.shape[0], ys.max() + m + 1))
    metrics = bcam.native_metrics(scene, cam, fg, roi)
    mask = metrics.pop("mask")
    metrics.pop("face_id", None)
    ppm = px_per_mm(cam, frame)
    out = {"camera": cam.to_dict(), "loss": float(res.get("loss_final_level", res.get("loss_fit_level", float("nan")))),
           "px_per_mm": ppm, "roi": [int(v) for v in roi], "evaluations": int(fit.evals), "seconds": round(time.time() - t0, 1),
           "iou": round(float(metrics["iou"]), 4),
           "contour_mean_px": float(metrics["contour_mean_px"]), "contour_p95_px": float(metrics["contour_p95_px"]),
           "contour_mean_mm": float(metrics["contour_mean_px"]) / ppm, "contour_p95_mm": float(metrics["contour_p95_px"]) / ppm,
           "contour_mean_pct_width": 100.0 * float(metrics["contour_mean_px"]) / max(float(w), 1.0),
           "photo_only_px": metrics["photo_only_px"], "render_only_px": metrics["render_only_px"], "extent": metrics["extent"]}
    return out, mask


def lens_front_metrics(scene: raster.RasterScene, lens_faces: np.ndarray, cam: Camera, frame: NormFrame, evidence_front: dict,
                       shape: tuple[int, int]) -> dict:
    """VISIBLE lens region (pixels whose first hit is a lens face, so a lens edge tucked under the rim does not
    count) vs the measured lens outlines of the front photo, in mm."""
    if not lens_faces.any() or evidence_front is None:
        return {"status": "unmeasured", "reason": "no lens parts or no front measurement"}
    polys = [np.asarray(l["outline_px"], float) for l in evidence_front["lenses"]]
    ref = raster_polys(polys, shape) > 0
    r = scene.render(cam, shape, 1, None)
    fid = r["face_id"]
    m = np.zeros(fid.shape, bool)
    hit = fid >= 0
    m[hit] = lens_faces[fid[hit]]
    ppm = px_per_mm(cam, frame)
    mean, p95 = bcam.contour_stats(m, ref)
    inter, union = int((m & ref).sum()), int((m | ref).sum())
    return {"status": "measured", "iou": round(inter / max(union, 1), 4), "contour_mean_px": float(mean), "contour_p95_px": float(p95),
            "contour_mean_mm": float(mean) / ppm, "contour_p95_mm": float(p95) / ppm, "lens_pixels_rendered": int(m.sum()),
            "lens_pixels_measured": int(ref.sum())}


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


def ar_sheet(renders: list[str], labels: list[str], out: Path) -> Path | None:
    paths = [p for p in renders if p and Path(p).is_file()]
    if not paths:
        return None
    box = archeck.content_box(paths)
    tiles = []
    for p, lab in zip(paths, labels):
        im = Image.open(p).convert("RGB").crop(box)
        im = im.resize((max(1, int(im.size[0] * 480 / max(im.size[1], 1))), 480), Image.LANCZOS)
        tiles.append(_label(im, lab))
    sheet = Image.new("RGB", (sum(t.size[0] for t in tiles) + 8 * (len(tiles) - 1), 480), (255, 255, 255))
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.size[0] + 8
    sheet.save(out)
    return out


# --------------------------------------------------------------------------- the observation
def canonical_render_specs(px_per_mm_canon="fit", width: int = 720, height: int = 480) -> list[dict]:
    specs = []
    for v in CANONICAL_VIEWS:
        cam = {"type": "orbit", "yaw": v["yaw"], "pitch": v["pitch"], "roll": 0, "ortho": v["ortho"], "px_per_mm": px_per_mm_canon,
               "target": "bbox", "distance_mm": 700}
        specs.append({"id": v["id"], "kind": v["kind"], "width": width, "height": height, "transparent": True, "camera": cam})
    return specs


def observe_candidate(cand_dir: Path, build: dict, evidence: dict, evidence_dir: Path, *, held_out_ids: set[str],
                      previous_cameras: dict | None = None, ar: bool = True, glb_path: Path | None = None,
                      render_scale: float = 1.0, time_limit_s: int = 300) -> dict:
    """Fit cameras, measure, render photo-matched and canonical views, load the GLB in the AR renderer.
    Writes cand_dir/observe/observation.json (author-visible part) and cand_dir/heldout/heldout.json."""
    t0 = time.time()
    obs_dir = cand_dir / "observe"
    held_dir = cand_dir / "heldout"
    obs_dir.mkdir(parents=True, exist_ok=True)
    held_dir.mkdir(parents=True, exist_ok=True)
    objects, _, _ = mexport.load_parts(Path(build["parts_npz"]), Path(build["materials_json"]))
    V, F, part = mesh_of(objects)
    frame = norm_frame(V)
    scene = raster.RasterScene(V, F, frame)
    lens_sel = np.isin(part, LENS_PARTS)
    pivot = front_piece_centre(V, F, part)
    masks = np.load(evidence_dir / "masks.npz")
    views = {}
    held = {}
    render_specs = []
    held_specs = []
    held_renders = {}
    photo_tiles = []
    for vid, entry in list(evidence["views"].items()) + [(k, {"view": v["view"], "id": k, "held": True}) for k, v in evidence["held_out"].items()]:
        view = entry["view"]
        if view not in ("front", "back", "left", "right", "angled"):
            continue
        fg = masks[f"fg_{vid}"]
        warm = None
        if previous_cameras and vid in previous_cameras and "camera" in previous_cameras[vid]:
            warm = camera_from_dict(previous_cameras[vid]["camera"])
        try:
            fit, mask = fit_view(view, fg, scene, frame, pivot, warm)
        except Exception as e:  # noqa: BLE001 - a failed fit is a measured failure of this view
            fit, mask = {"status": "fit_failed", "error": f"{type(e).__name__}: {e}"}, None
        fit["view"] = view
        target = held if entry.get("held") or vid in held_out_ids else views
        if view == "front" and mask is not None and evidence.get("front"):
            fit["lens_outline"] = lens_front_metrics(scene, lens_sel, camera_from_dict(fit["camera"]), frame, evidence["front"], fg.shape)
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
        render_result = run_harness({"mode": "render_only", "blend_path": build["blend"], "renders": render_specs + held_specs, "samples": 32},
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
    if ar and glb_path is not None and Path(glb_path).is_file():
        ar_result = archeck.run({"candidate": glb_path}, obs_dir / "ar", ar_views=AR_VIEWS, width_mm={"candidate": float(np.ptp(V[:, 0]))})
        m = ar_result["models"].get("candidate", {})
        if m.get("renders"):
            p = ar_sheet(m["renders"], [Path(r).stem.split("__")[-1] for r in m["renders"]], obs_dir / "sheet_ar.png")
            if p:
                sheets["ar"] = str(p)
        ar_result = {k: v for k, v in ar_result.items() if k not in ("stdout_tail", "stderr_tail", "command")}
    summary = summarize(views, held, ar_result)
    lens_colour = None
    if ar_result is not None and glb_path is not None:
        from .lens_colour import lens_colour_metric
        try:
            lens_colour = lens_colour_metric(Path(glb_path), obs_dir / "ar", evidence, Path(build["materials_json"]))
        except Exception as e:  # noqa: BLE001 - a colour metric failure is recorded, never fatal
            lens_colour = {"status": "failed", "error": f"{type(e).__name__}: {e}"}
    if lens_colour and lens_colour.get("status") == "measured":
        summary["lens_colour"] = {k: lens_colour[k] for k in ("hue_error", "saturation_ratio", "value_ratio", "photo_hsv", "render_hsv", "mirrored")}
        if lens_colour.get("lens_env_intensity_recommended") is not None:
            summary["lens_env_intensity_recommended"] = lens_colour["lens_env_intensity_recommended"]
    observation = {"seconds": round(time.time() - t0, 1), "frame": frame.to_dict(), "triangles": int(len(F)),
                   "bbox_mm": [V.min(0).round(2).tolist(), V.max(0).round(2).tolist()],
                   "views": views, "sheets": sheets, "renders": renders, "ar": ar_result, "summary": summary, "lens_colour": lens_colour,
                   "render_result": {k: render_result.get(k) for k in ("ok", "error", "process")} if render_result else None}
    (obs_dir / "observation.json").write_text(json.dumps(observation, indent=1, default=_json_default), encoding="utf-8")
    (held_dir / "heldout.json").write_text(json.dumps({"views": held, "summary": summarize(held, {}, None), "renders": held_renders},
                                                      indent=1, default=_json_default), encoding="utf-8")
    return observation


def summarize(views: dict, held: dict, ar_result: dict | None) -> dict:
    """The numbers the incumbent rule and the report use."""
    fit = {k: v for k, v in views.items() if "contour_mean_mm" in v}
    front = next((v for v in fit.values() if v["view"] == "front"), None)
    sides = [v for v in fit.values() if v["view"] in ("left", "right")]
    back = next((v for v in fit.values() if v["view"] == "back"), None)
    out = {"fit_views_measured": sorted(fit), "fit_views_failed": sorted(k for k, v in views.items() if "contour_mean_mm" not in v)}
    if front:
        out["front_contour_mean_mm"] = round(front["contour_mean_mm"], 3)
        out["front_contour_p95_mm"] = round(front["contour_p95_mm"], 3)
        out["front_iou"] = front["iou"]
        lo = front.get("lens_outline") or {}
        if lo.get("status") == "measured":
            out["lens_outline_mean_mm"] = round(lo["contour_mean_mm"], 3)
            out["lens_outline_p95_mm"] = round(lo["contour_p95_mm"], 3)
    if sides:
        out["side_contour_mean_mm"] = round(float(np.mean([v["contour_mean_mm"] for v in sides])), 3)
    if back:
        out["back_contour_mean_mm"] = round(back["contour_mean_mm"], 3)
    if fit:
        out["mean_contour_mm_all_fit_views"] = round(float(np.mean([v["contour_mean_mm"] for v in fit.values()])), 3)
    if ar_result is not None:
        m = ar_result.get("models", {}).get("candidate", {})
        out["ar_runtime_compatible"] = bool(m.get("runtime_compatible"))
        out["ar_optical_meshes"] = m.get("optical_meshes_detected")
        out["ar_error"] = m.get("error")
        out["ar_continuity_failure"] = m.get("continuity_failure")
    return out


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
