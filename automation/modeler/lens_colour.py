"""Lens colour: the lens core of the runtime's actual-AR front render against the front photo's lens core.

The runtime render is lit by the app's room environment over the harness's checker fixture; the catalog photo by a
studio. The metric therefore reports the hue error (0 = same hue, 0.5 = opposite) first, then saturation and value
RATIOS (render / photo). For a transmissive lens both cores include their background; for a mirrored lens the
reflection dominates and the value ratio gives the lens environment intensity the live mirror would need to read as
bright as the photo (``lens_env_intensity_recommended``, the AR app's ``lensenv`` handover, 0.3..4).

The lens pixels of the render come from the harness's recorded per-lens ``mesh_to_world`` and the render's camera
matrices (column-major; pixel x = (ndc.x + 1) W / 2, y = (1 - ndc.y) H / 2), so no colour segmentation is involved.
"""
from __future__ import annotations

import colorsys
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .paths import AUTOMATION

CORE_FRACTION = 0.6      # the inner part of each lens: no rim, no edge highlight
NEUTRAL_SATURATION = 0.08   # below this HSV saturation a colour has no meaningful hue (grey, clear, near-black lenses)


def _mat4(values) -> np.ndarray:
    """A 4x4 from the harness's column-major 16-list, as a row-major numpy matrix."""
    return np.asarray(values, float).reshape(4, 4).T


def project_pixels(points: np.ndarray, matrix: np.ndarray, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Homogeneous projection of Nx3 points through a 4x4 (row-major) clip matrix to pixel coordinates; (px, valid)."""
    hom = np.c_[points, np.ones(len(points))] @ matrix.T
    w = hom[:, 3]
    ok = w > 1e-9
    ndc = np.zeros((len(hom), 2))
    ndc[ok] = hom[ok, :2] / w[ok, None]
    return np.c_[(ndc[:, 0] + 1.0) * width / 2.0, (1.0 - ndc[:, 1]) * height / 2.0], ok


def lens_masks_in_render(glb_path: Path, render: dict, shape: tuple[int, int]) -> dict[str, np.ndarray]:
    """Pixel masks of the lens parts in one actual-AR render."""
    from reconstruction.mesh import load_glb_bytes
    spatial = render.get("spatial") or {}
    cam = render.get("camera") or {}
    if not spatial.get("lenses") or not cam.get("projection_matrix"):
        return {}
    mesh = load_glb_bytes(Path(glb_path).read_bytes())
    view = _mat4(cam.get("view_matrix") or [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
    proj = _mat4(cam["projection_matrix"])
    W, H = int(cam.get("width", shape[1])), int(cam.get("height", shape[0]))
    by_name: dict[str, list] = {}
    for p in mesh.parts or []:
        name = str(p.get("name") or "")
        if name.startswith("lens"):
            by_name.setdefault(name, []).append(p)
    masks = {}
    for lens in spatial["lenses"]:
        parts = by_name.get(lens.get("name"))
        if not parts:
            continue
        px, ok = project_pixels(mesh.vertices, proj @ view @ _mat4(lens["mesh_to_world"]), W, H)
        img = Image.new("L", (W, H), 0)
        draw = ImageDraw.Draw(img)
        for p in parts:
            for tri in mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]]:
                if ok[tri].all():
                    draw.polygon([tuple(px[i]) for i in tri], fill=255)
        masks[lens["name"]] = np.asarray(img) > 0
    return masks


def core_of(mask: np.ndarray, frac: float = CORE_FRACTION) -> np.ndarray:
    """The inner part of a lens mask: eroded away from its edge (12 % of its height, at least 2 px, so no rim or
    edge highlight) and within ``frac`` of the largest radius from the centroid."""
    from scipy import ndimage
    ys, xs = np.nonzero(mask)
    core = np.zeros_like(mask, dtype=bool)
    if len(xs) < 50:
        return core
    margin = max(2, int(round(0.12 * (ys.max() - ys.min() + 1))))
    eroded = ndimage.binary_erosion(mask, iterations=margin)
    cx, cy = xs.mean(), ys.mean()
    r = np.hypot(xs - cx, ys - cy)
    keep = r < frac * r.max()
    core[ys[keep], xs[keep]] = True
    return core & eroded


def photo_lens_core(evidence: dict, image: np.ndarray | None = None) -> tuple[np.ndarray, int] | None:
    """Mean sRGB of the lens cores in the front photo, from the measured lens outlines (mm -> photo pixels)."""
    front = evidence.get("front") or {}
    row = next((r for r in evidence.get("inputs", []) if r.get("view") == "front"), None)
    if not front.get("lenses") or (row is None and image is None):
        return None
    if image is None:
        path = Path(row["path"])
        path = path if path.is_absolute() else AUTOMATION / path
        image = np.asarray(Image.open(path).convert("RGB")).astype(float)
    H, W = image.shape[:2]
    mmpx, ax, ymid = float(front["mm_per_px"]), float(front["axis_x_px"]), float(front["y_mid_px"])
    core_all = np.zeros((H, W), bool)
    for lens in front["lenses"]:
        poly = np.asarray(lens["outline_mm"], float)
        c = poly.mean(0)
        shrunk = c + CORE_FRACTION * (poly - c)
        pts = [(ax + x / mmpx, ymid - y / mmpx) for x, y in shrunk]
        img = Image.new("L", (W, H), 0)
        ImageDraw.Draw(img).polygon(pts, fill=255)
        core_all |= np.asarray(img) > 0
    if core_all.sum() < 20:
        return None
    return image[core_all].mean(0), int(core_all.sum())


def compare(photo_rgb: np.ndarray, render_rgb: np.ndarray, *, mirrored: bool) -> dict:
    ph = colorsys.rgb_to_hsv(*(np.asarray(photo_rgb, float) / 255.0))
    rh = colorsys.rgb_to_hsv(*(np.asarray(render_rgb, float) / 255.0))
    # hue is undefined for a neutral colour: a grey or clear lens gets no hue error and a saturation DIFFERENCE instead
    neutral = ph[1] < NEUTRAL_SATURATION or rh[1] < NEUTRAL_SATURATION
    if neutral:
        dh = None
    else:
        dh = abs(ph[0] - rh[0])
        dh = min(dh, 1.0 - dh)
    rec = round(float(np.clip(ph[2] / max(rh[2], 1e-3), 0.3, 4.0)), 2) if mirrored else None
    return {"photo_rgb": [round(float(x), 1) for x in photo_rgb], "render_rgb": [round(float(x), 1) for x in render_rgb],
            "photo_hsv": [round(float(x), 3) for x in ph], "render_hsv": [round(float(x), 3) for x in rh],
            "hue_error": None if dh is None else round(float(dh), 3),
            "saturation_ratio": None if neutral else round(float(rh[1] / max(ph[1], 1e-3)), 2),
            "saturation_difference": round(float(rh[1] - ph[1]), 3),
            "value_ratio": round(float(rh[2] / max(ph[2], 1e-3)), 2), "mirrored": bool(mirrored), "neutral": bool(neutral),
            "lens_env_intensity_recommended": rec}


def lens_colour_metric(glb_path: Path, ar_dir: Path, evidence: dict, materials_json: Path) -> dict:
    """The metric for one observed candidate: its actual-AR front render under ``ar_dir`` vs the front photo."""
    ar_dir = Path(ar_dir)
    rp = ar_dir / "report.json"
    if not rp.exists():
        return {"status": "no_report"}
    report = json.loads(rp.read_text(encoding="utf-8"))
    case = (report.get("cases") or [None])[0]
    render = next((r for r in (case or {}).get("renders", []) if r.get("mode") == "actual-ar" and r.get("view") == "front"), None)
    if render is None:
        return {"status": "no_front_render"}
    img = np.asarray(Image.open(ar_dir / render["filename"]).convert("RGB")).astype(float)
    masks = lens_masks_in_render(glb_path, render, img.shape[:2])
    core = np.zeros(img.shape[:2], bool)
    for m in masks.values():
        core |= core_of(m)
    if core.sum() < 50:
        return {"status": "no_lens_pixels", "lenses": sorted(masks)}
    photo = photo_lens_core(evidence)
    if photo is None:
        return {"status": "no_photo_lens"}
    prgb, pn = photo
    mats = json.loads(Path(materials_json).read_text(encoding="utf-8")).get("materials", {})
    mirrored = any(bool((s.get("lens") or {}).get("mirror")) for s in mats.values() if s.get("kind") == "lens")
    out = compare(prgb, img[core].mean(0), mirrored=mirrored)
    out.update({"status": "measured", "photo_pixels": int(pn), "render_pixels": int(core.sum()), "render": render["filename"],
                "note": "lens core of the runtime's front render (room lighting, checker fixture) vs the front photo's lens core (studio); "
                        "a transmissive lens includes its background in both; match the hue first, then saturation and relative brightness"})
    return out
