"""Offline registration diagnostic, not a reconstruction-quality acceptance gate.

Example: python -m reconstruction.register --model asset.glb --photo front.jpg
         --view front --output data/registration/front
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy import ndimage

from .camera import Camera, fit_camera, render_mask
from .mesh import load_glb
from .metrics import score_masks
from .observations import observe_image_path


def run(model_path, photo_path, output, *, view="front", resolution=256, evaluations=140):
    model_path, photo_path, output = Path(model_path), Path(photo_path), Path(output)
    if output.resolve() in (model_path.resolve(), photo_path.resolve()):
        raise ValueError("Output directory must not replace an input")
    observation = observe_image_path(photo_path)
    report = {"schema_version": 1, "status": "diagnostic_only", "quality_verdict": "unmeasured",
              "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
              "photo_sha256": hashlib.sha256(photo_path.read_bytes()).hexdigest(),
              "view_label": view, "observation": observation.to_report(),
              "limitations": ["Automatic contrast pixels are not a verified geometric silhouette.",
                              "The triangle union includes optical surfaces irrespective of transparency.",
                              "A fitted match cannot prove product fidelity or material accuracy.",
                              "The reduced AR mesh is evaluated; its full-resolution master is not checked."]}
    output.mkdir(parents=True, exist_ok=True)
    if observation.usable_mask is None:
        report["status"] = "insufficient_contrast_evidence"
    else:
        image = ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB")
        scale = min(1.0, resolution / max(image.size))
        size = (max(2, round(image.width*scale)), max(2, round(image.height*scale)))
        image = image.resize(size, Image.Resampling.LANCZOS)
        reference = np.asarray(Image.fromarray(observation.mask).resize(size, Image.Resampling.NEAREST), dtype=bool)
        mesh = load_glb(model_path).normalized()
        fit = fit_camera(mesh, reference, view=view, max_evaluations=evaluations)
        camera = Camera(**fit["camera"])
        candidate = render_mask(mesh, camera, reference.shape)
        initial = render_mask(mesh, Camera(**fit["initial_camera"]), reference.shape)
        report.update(camera_fit=fit, rendered_triangles=len(mesh.faces), model_parts=mesh.parts,
                      render_size=list(size), downsample_scale=scale,
                      initial_metrics=score_masks(reference, initial), fitted_metrics=score_masks(reference, candidate))
        overlay = np.array(image)
        ref_edge = reference & ~ndimage.binary_erosion(reference)
        candidate_edge = candidate & ~ndimage.binary_erosion(candidate)
        overlay[ref_edge] = [0, 170, 40]
        overlay[candidate_edge] = [230, 35, 40]
        canvas = Image.new("RGB", (size[0]*3, size[1]+54), "white")
        canvas.paste(image, (0, 36))
        canvas.paste(Image.fromarray(np.uint8(candidate)*255).convert("RGB"), (size[0], 36))
        canvas.paste(Image.fromarray(overlay), (size[0]*2, 36))
        draw = ImageDraw.Draw(canvas)
        draw.text((4, 4), "Photo | fitted geometry footprint | overlay", fill="black")
        draw.text((4, 19), "Green: observed contrast. Red: geometry. NOT a quality pass.", fill="black")
        canvas.save(output/"comparison.png")
        Image.fromarray(reference).save(output/"reference-contrast.png")
        Image.fromarray(candidate).save(output/"candidate-footprint.png")
    (output/"report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--photo", type=Path, required=True)
    parser.add_argument("--view", choices=("front", "back", "left", "right", "angled"), default="front")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=256)
    parser.add_argument("--evaluations", type=int, default=140)
    args = parser.parse_args()
    if not 64 <= args.resolution <= 1024:
        parser.error("resolution must be 64–1024")
    result = run(args.model, args.photo, args.output, view=args.view, resolution=args.resolution, evaluations=args.evaluations)
    print(json.dumps({"status": result["status"], "quality_verdict": result["quality_verdict"],
                      "initial_iou": result.get("initial_metrics", {}).get("foreground_iou"),
                      "fitted_iou": result.get("fitted_metrics", {}).get("foreground_iou")}))


if __name__ == "__main__":
    main()
