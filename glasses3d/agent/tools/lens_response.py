"""Informational display-RGB lens background response, from hash-verified native AR captures.

This is the review's skin-minus-dark method, not physical transmission/VLT or a finish check. No threshold.
"""
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_erosion

BACKGROUND_RGB = {"skin": [203, 166, 141], "dark": [43, 37, 34]}


def response_summary(response: dict) -> str:
    """Keep the actual per-view numbers visible when the larger capture report is paged for Astra."""
    values = [row["view"] + " " + (f"{row['mean_channel_background_response_gain']:.3f}" if row["status"] == "measured"
                                    else "unavailable") for row in response["views"]]
    return "Lens background response (display RGB, not VLT; information only): " + ", ".join(values)


def background_response(out: Path, report: dict) -> dict:
    records = report.get("images", [])
    palette = [entry for entry in report.get("id_palette", []) if entry.get("class") == "lens"]
    poses = dict.fromkeys(rec.get("pose") for rec in records if rec.get("kind") == "pose")
    results = []
    for pose in poses:
        images = [rec for rec in records if rec.get("pose") == pose and rec.get("kind") == "pose"]
        pair = {bg: next((rec for rec in images if rec.get("variant") == "beauty" and
                         rec.get("background") == bg), None) for bg in BACKGROUND_RGB}
        ids = next((rec for rec in images if rec.get("variant") == "part-id"), None)
        row = {"view": pose, "status": "unavailable"}
        results.append(row)
        if not ids or not all(pair.values()):
            row["reason"] = "no controlled skin/dark pair and part-id image"
            continue
        sources = {"part_id": ids, **pair}
        row["sources"] = {name: {"file": rec["file"], "sha256": rec["sha256"]}
                          for name, rec in sources.items()}
        pixels = {}
        for name, rec in sources.items():
            with Image.open(out / rec["file"]) as opened:
                pixels[name] = np.asarray(opened.convert("RGB"), dtype=np.float64)
        if len({array.shape for array in pixels.values()}) != 1:
            row["reason"] = "background pair and part-id image dimensions differ"
            continue
        mask = np.zeros(pixels["part_id"].shape[:2], dtype=bool)
        for entry in palette:
            mask |= np.max(np.abs(pixels["part_id"] - np.asarray(entry["rgb"])), axis=2) <= entry["decode_tolerance"]
        mask = binary_erosion(mask, structure=np.ones((7, 7), bool))
        row["pixels"] = int(mask.sum())
        if not mask.any():
            row["reason"] = "no lens interior pixels after three-pixel erosion"
            continue
        delta = (pixels["skin"] - pixels["dark"])[mask]
        gain = delta / (np.asarray(BACKGROUND_RGB["skin"]) - BACKGROUND_RGB["dark"])
        row.update(status="measured", mean_rgb_difference_8bit=delta.mean(axis=0).tolist(),
                   mean_rgb_background_response_gain=gain.mean(axis=0).tolist(),
                   mean_channel_background_response_gain=float(gain.mean()),
                   median_channel_background_response_gain=float(np.median(gain)))
    return {"schema": "g3d.lens_background_response/1", "informational_only": True,
            "method": "Decode lens part-id palette with its tolerance; erode three pixels (7x7 square); "
                      "subtract beauty(dark) from beauty(skin) and divide RGB by [160,129,107]. No clipping.",
            "interpretation": "Rendered display-RGB background response, not physical transmission or VLT. "
                              "Reflection, refraction, tone mapping, shadows and multiple surfaces contribute. "
                              "Judge how see-through the worn lenses look against the photos; this is not a gate.",
            "background_rgb": BACKGROUND_RGB, "views": results,
            "portrait": {"status": "unavailable", "reason": "portrait has no controlled skin/dark background pair; inspect its eyes visually"}}
