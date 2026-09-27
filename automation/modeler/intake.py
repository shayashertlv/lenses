"""Intake and evidence: immutable source copies, mattes, lens proposals, measured outlines in millimetres.

Reuses the numeric image tools of ``bsa.intake`` (backdrop-model matte, offline lens detector) and ``bsa.front``
(point-typed lens outlines, symmetrisation, rim widths). Everything the author receives is derived here and
recorded with its transform; the held-out photos are copied but never placed in the author's evidence.

Millimetre conventions of the evidence (the MODEL frame the construction programs use):
  x = 0 on the front photo's symmetry axis, +x = viewer's right; y = 0 at the vertical middle of the front
  silhouette, +y up; z is not observable in the front photo. Side outlines: z = 0 at the front-most silhouette
  point, negative toward the back; y as above (approximate: the side photo is scaled by the front piece height).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .request import Request

AUTHOR_MAX_PX = 1024
LENS_VIEWS = ("front", "back", "angled")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_inputs(request: Request, job_dir: Path) -> list[dict]:
    """Immutable copies under job/inputs/<view or index>.<ext>, with digests."""
    inputs = job_dir / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, p in enumerate(request.photos):
        name = p.view if p.view != "unknown" else f"photo{i:02d}"
        dst = inputs / f"{name}{p.path.suffix.lower()}"
        if not dst.exists():
            shutil.copy2(p.path, dst)
        with Image.open(dst) as im:
            size = im.size
        rows.append({"id": name, "view": p.view, "held_out": p.held_out, "source_path": str(p.path),
                     "path": str(dst), "sha256": sha256_file(dst), "size": list(size), "bytes": dst.stat().st_size})
    return rows


def largest_contour_mm(mask: np.ndarray, to_mm, n: int = 256) -> list | None:
    """The outer contour of the largest component as an (n,2) mm polygon (via ``to_mm(px_points)``)."""
    m = np.ascontiguousarray(mask.astype(np.uint8))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(float)
    if len(c) < 8:
        return None
    from bsa.front import resample_closed
    c = resample_closed(c, n)
    return np.round(to_mm(c), 3).tolist()


def measure_front(rgb: np.ndarray, fg: np.ndarray, lens: np.ndarray, matte: np.ndarray, info: dict,
                  front_width_mm: float) -> dict:
    """Lens outlines, rim widths and frame measurements of the front photo in mm (MODEL frame)."""
    from bsa import front as bfront
    from bsa import intake as bintake
    bg_lab = bintake.backdrop_lab(np.asarray(info["backdrop_lab_coef"], float), fg.shape)
    part = bfront.partition(rgb, fg, lens, front_width_mm, matte=matte, bg_lab=bg_lab, band_rule=True)
    a = part["arrays"]
    mm_px = part["mm_per_px"]
    x0 = part["axis_x_px"]
    rows = np.nonzero(a["fg_sym"].any(1))[0]
    y_mid = (rows.min() + rows.max()) / 2.0 if len(rows) else fg.shape[0] / 2.0

    def to_mm(px):
        px = np.asarray(px, float)
        return np.column_stack([(px[:, 0] - x0) * mm_px, (y_mid - px[:, 1]) * mm_px])

    lenses = []
    type_names = {0: "frame_bounded", 1: "free_edge", 2: "rimless_edge"}
    for i, li in enumerate(part["lenses"], start=1):
        poly = a[f"lens{i}_poly"]
        t = a[f"lens{i}_type"]
        w = a[f"lens{i}_rimw_px"] * mm_px
        mm = to_mm(poly)
        lo, hi = mm.min(0), mm.max(0)
        lenses.append({"side": li["side"], "outline_mm": np.round(mm, 3).tolist(), "outline_px": np.round(poly, 2).tolist(),
                       "edge_type": [type_names[int(k)] for k in t], "rim_width_mm": np.round(w, 2).tolist(),
                       "box_mm": {"width_A": round(float(hi[0] - lo[0]), 2), "height_B": round(float(hi[1] - lo[1]), 2),
                                  "x_range": [round(float(lo[0]), 2), round(float(hi[0]), 2)],
                                  "y_range": [round(float(lo[1]), 2), round(float(hi[1]), 2)]},
                       "rim_class": li["rim_class"], "rim_w_median_mm": li["rim_w_median_mm"],
                       "type_fractions": li["type_fractions"]})
    sil = largest_contour_mm(a["fg_sym"], to_mm)
    frame_only = largest_contour_mm(a["frame_mask"], to_mm)
    cols = np.nonzero(a["fg_sym"].any(0))[0]
    height_mm = (rows.max() - rows.min() + 1) * mm_px if len(rows) else None
    dbl = None
    if len(lenses) == 2:
        right = next((l for l in lenses if l["side"] == "R"), lenses[0])
        left = next((l for l in lenses if l["side"] == "L"), lenses[1])
        dbl = round(right["box_mm"]["x_range"][0] - left["box_mm"]["x_range"][1], 2)
    # brow / bottom rim thickness at each lens centroid column and endpiece width at the centroid row
    thick = []
    for l in lenses:
        outline = np.asarray(l["outline_px"])
        cx, cy = outline.mean(0)
        col = int(round(cx))
        colmask = a["fg_sym"][:, col]
        r = np.nonzero(colmask)[0]
        top_px = outline[:, 1].min()
        bot_px = outline[:, 1].max()
        brow = (top_px - r.min()) * mm_px if len(r) else None
        bottom = (r.max() - bot_px) * mm_px if len(r) else None
        rowmask = a["fg_sym"][int(round(cy)), :]
        c = np.nonzero(rowmask)[0]
        if l["side"] == "R":
            end = (c.max() - outline[:, 0].max()) * mm_px if len(c) else None
        else:
            end = (outline[:, 0].min() - c.min()) * mm_px if len(c) else None
        thick.append({"side": l["side"], "brow_mm": None if brow is None else round(float(brow), 2),
                      "bottom_rim_mm": None if bottom is None else round(float(bottom), 2),
                      "endpiece_mm": None if end is None else round(float(end), 2)})
    return {"mm_per_px": mm_px, "axis_x_px": x0, "y_mid_px": y_mid, "width_px": part["width_px"],
            "front_width_mm": front_width_mm, "front_height_mm": None if height_mm is None else round(float(height_mm), 2),
            "layout": part["layout"], "rim_class": part["rim_class"], "lens_share": part["lens_share"],
            "fg_mirror_iou": part["fg_mirror_iou"], "lens_mirror_iou": part["lens_mirror_iou"],
            "bridge_dbl_mm": dbl, "thickness_mm": thick, "lenses": lenses, "silhouette_mm": sil,
            "frame_without_lenses_mm": frame_only, "flags": part["flags"],
            "refinement": {k: part["refinement"].get(k) for k in ("median_bevel_offset_mm", "low_contrast_share",
                                                                  "point_type_fractions", "offset_mm")},
            "arrays": {"frame_mask": a["frame_mask"], "fg_sym": a["fg_sym"], "lens_label": a["lens_label"]}}


def measure_side(view: str, fg: np.ndarray, front_height_mm: float | None) -> dict:
    """Side silhouette in (z, y) mm scaled by the front height; z = 0 at the front-most point (the front piece is
    at the image edge the temples point away from)."""
    ys, xs = np.nonzero(fg)
    if not len(xs):
        return {"error": "empty matte"}
    h_px = ys.max() - ys.min() + 1
    w_px = xs.max() - xs.min() + 1
    mm_px = (front_height_mm / h_px) if front_height_mm else None
    # the front piece is the thick end: compare the matte column density at both ends
    left_density = fg[:, xs.min():xs.min() + max(1, w_px // 10)].mean()
    right_density = fg[:, xs.max() - max(1, w_px // 10):xs.max() + 1].mean()
    front_at_right = right_density > left_density
    y_mid = (ys.min() + ys.max()) / 2.0
    out = {"view": view, "bbox_px": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
           "front_at_image_right": bool(front_at_right), "mm_per_px": mm_px,
           "scale_note": "side scale = front piece height from the front photo / this matte's height (approximate)"}
    if mm_px:
        x_front = xs.max() if front_at_right else xs.min()

        def to_mm(px):
            px = np.asarray(px, float)
            z = -(np.abs(px[:, 0] - x_front)) * mm_px
            return np.column_stack([z, (y_mid - px[:, 1]) * mm_px])

        out["outline_zy_mm"] = largest_contour_mm(fg, to_mm)
        out["length_mm"] = round(float(w_px * mm_px), 2)
        out["height_mm"] = round(float(h_px * mm_px), 2)
    return out


def author_photo(rgb: np.ndarray, fg: np.ndarray, dst: Path, margin: float = 0.06) -> dict:
    """A cropped, downscaled copy for the author with the recorded transform (crop origin, scale)."""
    H, W = fg.shape
    ys, xs = np.nonzero(fg)
    if len(xs):
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        mx, my = int((x1 - x0) * margin) + 4, int((y1 - y0) * margin) + 4
        x0, x1, y0, y1 = max(0, x0 - mx), min(W, x1 + mx), max(0, y0 - my), min(H, y1 + my)
    else:
        x0, x1, y0, y1 = 0, W, 0, H
    crop = rgb[y0:y1, x0:x1]
    s = min(1.0, AUTHOR_MAX_PX / max(crop.shape[:2]))
    im = Image.fromarray(crop)
    if s < 1.0:
        im = im.resize((max(1, int(round(crop.shape[1] * s))), max(1, int(round(crop.shape[0] * s)))), Image.LANCZOS)
    im.save(dst, quality=90)
    return {"path": str(dst), "crop_xyxy": [int(x0), int(y0), int(x1), int(y1)], "scale": s, "size": list(im.size)}


def overlay_front(rgb: np.ndarray, front: dict, dst: Path, transform: dict) -> None:
    """The author's front photo with the measured lens outlines and the symmetry axis drawn."""
    im = Image.open(transform["path"]).convert("RGB")
    d = ImageDraw.Draw(im)
    x0, y0 = transform["crop_xyxy"][:2]
    s = transform["scale"]
    for l in front["lenses"]:
        pts = [((u - x0) * s, (v - y0) * s) for u, v in l["outline_px"]]
        d.line(pts + pts[:1], fill=(0, 255, 90), width=2)
    ax = (front["axis_x_px"] - x0) * s
    d.line([(ax, 0), (ax, im.size[1])], fill=(255, 80, 80), width=1)
    ym = (front["y_mid_px"] - y0) * s
    d.line([(0, ym), (im.size[0], ym)], fill=(80, 140, 255), width=1)
    im.save(dst, quality=90)


def run_intake(request: Request, job_dir: Path, *, front_width_mm: float, width_provenance: dict) -> dict:
    """Build job/inputs and job/evidence. Returns the evidence dict (also written to evidence/evidence.json)."""
    from bsa import intake as bintake
    t0 = time.time()
    inputs = copy_inputs(request, job_dir)
    ev_dir = job_dir / "evidence"
    photos_dir = ev_dir / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    masks = {}
    views = {}
    held = {}
    front_meas = None
    front_rgb = None
    for row in inputs:
        rgb = np.asarray(Image.open(row["path"]).convert("RGB"))
        view = row["view"]
        fg, lens, info = bintake.intake_view(rgb, view if view in LENS_VIEWS or view in ("left", "right") else "left",
                                             front_width_mm=front_width_mm)
        pure = info.pop("_matte")
        info.pop("_openings", None)
        masks[f"fg_{row['id']}"] = fg
        masks[f"lens_{row['id']}"] = lens
        masks[f"matte_{row['id']}"] = pure
        entry = {"id": row["id"], "view": view, "sha256": row["sha256"], "size": row["size"], "held_out": row["held_out"],
                 "backdrop_rgb": info["backdrop_rgb"], "bbox_xyxy": info["bbox_xyxy"], "fg_pixels": info["fg_pixels"],
                 "lens_pixels": info["lens_pixels"], "flags": info["flags"], "mirror_iou": info.get("mirror_iou")}
        if row["held_out"]:
            held[row["id"]] = entry            # evaluator only: no author photo, no measurement
            continue
        entry["author_photo"] = author_photo(rgb, fg, photos_dir / f"{row['id']}.jpg")
        views[row["id"]] = entry
        if view == "front":
            front_rgb = rgb
            front_meas = measure_front(rgb, fg, lens, pure, info, front_width_mm)
            masks["front_frame_mask"] = front_meas["arrays"]["frame_mask"]
            masks["front_fg_sym"] = front_meas["arrays"]["fg_sym"]
            masks["front_lens_label"] = front_meas["arrays"]["lens_label"]
            front_meas.pop("arrays")
    sides = {}
    fh = front_meas["front_height_mm"] if front_meas else None
    for vid, entry in views.items():
        if entry["view"] in ("left", "right"):
            sides[vid] = measure_side(entry["view"], masks[f"fg_{vid}"], fh)
    if front_meas is not None and front_rgb is not None:
        overlay_front(front_rgb, front_meas, photos_dir / "front_measured.jpg", views["front"]["author_photo"])
        views["front"]["measured_overlay"] = str(photos_dir / "front_measured.jpg")
    np.savez_compressed(ev_dir / "masks.npz", **masks)
    evidence = {"schema_version": 1, "product_id": request.product_id, "notes": request.notes,
                "scale": {"front_width_mm": front_width_mm, **width_provenance},
                "dimensions_stated": request.dimensions,
                "conventions": {"frame": "MODEL frame, mm: +x viewer's right in the front photo, +y up, +z toward the front camera",
                                "front_origin": "x = 0 on the symmetry axis; y = 0 at the vertical middle of the front silhouette",
                                "side_origin": "z = 0 at the front-most silhouette point, negative toward the back"},
                "views": views, "front": front_meas, "sides": sides, "held_out": {k: {"id": v["id"], "view": v["view"]} for k, v in held.items()},
                "inputs": inputs, "seconds": round(time.time() - t0, 1)}
    augment_evidence(evidence)
    (ev_dir / "evidence.json").write_text(json.dumps(evidence, indent=1), encoding="utf-8")
    (ev_dir / "held_out.json").write_text(json.dumps(held, indent=1), encoding="utf-8")
    return evidence


def augment_evidence(evidence: dict, n_outline: int = 64, n_side: int = 48) -> dict:
    """The downsampled outlines the author package shows (``outline_mm_64``, ``silhouette_mm_64``,
    ``outline_zy_mm_48``) are added to the evidence itself, so the dict a program reads inside Blender (E) carries
    every key the package names. Idempotent."""
    from bsa.front import resample_closed
    front = evidence.get("front")
    if front:
        for l in front.get("lenses", []):
            if l.get("outline_mm") and "outline_mm_64" not in l:
                l["outline_mm_64"] = np.round(resample_closed(np.asarray(l["outline_mm"], float), n_outline), 3).tolist()
        if front.get("silhouette_mm") and "silhouette_mm_64" not in front:
            front["silhouette_mm_64"] = np.round(resample_closed(np.asarray(front["silhouette_mm"], float), n_outline), 3).tolist()
    for s in (evidence.get("sides") or {}).values():
        if s.get("outline_zy_mm") and "outline_zy_mm_48" not in s:
            s["outline_zy_mm_48"] = np.round(resample_closed(np.asarray(s["outline_zy_mm"], float), n_side), 3).tolist()
    return evidence
