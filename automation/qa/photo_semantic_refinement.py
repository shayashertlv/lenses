"""QA-only fixed photo-semantic proposals refined by the existing offline SAM2.

The upstream two unflipped lens-logit grids supply boxes and one positive point
per connected component. Every SAM decoder mask survives. Agreement, composites
and model scores do not establish lens identity or improved segmentation quality.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import itertools
import json
from pathlib import Path
import re
import sys
import time

import numpy as np
from PIL import Image, ImageDraw
import PIL
from scipy import ndimage
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reconstruction.photo_lens_observations import _child, _read_pinned
from reconstruction.region_engine import OfflineSAM2RegionEngine


POLICY = {
    "upstream_variants": ["full", "contrast_crop"],
    "upstream_head": "lenses_logits; original unflipped prediction only",
    "threshold": "logit > 0; uncalibrated semantic proposal",
    "component_connectivity": 4,
    "minimum_component_grid_pixels": 16,
    "maximum_retained_components_per_variant": 32,
    "over_capacity_behavior": "entire variant unsupported; no top-N truncation",
    "prompt_bbox_padding_fraction_each_side_per_axis": .10,
    "prompt_bbox_minimum_native_span": 1.,
    "positive_point": "farthest interior 256-grid pixel under Euclidean distance to padded zero exterior; ties first row then column",
    "point_mapping": "recorded grid_to_native_pixel_center; clamp to normalized native pixel-center domain",
    "bbox_mapping": "component grid-cell bounds via recorded center transform plus half-pixel boundary conversion; expand 10 percent each side and clamp",
    "negative_points": 0,
    "sam_call_scope": "one predict call containing every retained component from both variants per nonempty photo; one image embedding",
    "sam_decoder_alternatives": 3,
    "coarse_component_native_mapping": "nearest source grid pixel under the recorded crop cell mapping; outside source crop unknown",
    "crossproposal_overlap": "exact native pixels for every pair of SAM masks belonging to distinct proposals, all decoder combinations",
    "maximum_native_pixels_per_photo": 16_000_000,
    "maximum_photos": 32,
    "display": "per-variant unions by decoder index are composites only; decoder index does not establish cross-prompt identity",
}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _receipt(path, root):
    return {"path": Path(path).relative_to(root).as_posix(), "sha256": _sha(path)}


def _mask_receipt(path, mask, root):
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)
    ys, xs = np.nonzero(mask)
    return {**_receipt(path, root), "pixels": int(mask.sum()),
            "bbox_xyxy_exclusive": [int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1] if len(xs) else None,
            "touches_image_border": bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())}


def _iou(left, right):
    intersection = int(np.count_nonzero(left & right))
    union = int(np.count_nonzero(left | right))
    return {"intersection_pixels": intersection, "union_pixels": union,
            "iou": intersection / union if union else None, "both_empty": union == 0}


def component_proposals(logits, variant, size_xy):
    """Return every component receipt and a bounded prompt set; no semantic IDs."""
    logits = np.asarray(logits)
    if logits.shape != (256, 256) or logits.dtype.kind != "f" or not np.isfinite(logits).all():
        raise ValueError("Expected one finite floating 256x256 unflipped lens-logit grid")
    width, height = size_xy
    box = variant["crop_xyxy_exclusive"]
    if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
            or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
        raise ValueError("Invalid recorded crop bounds")
    x0, y0, x1, y1 = box
    sx, sy = (x1-x0)/256, (y1-y0)/256
    matrix = np.asarray(variant["grid_to_native_pixel_center"], dtype=float)
    expected = np.array([[sx, 0., x0+.5*sx-.5], [0., sy, y0+.5*sy-.5], [0., 0., 1.]])
    if matrix.shape != (3, 3) or not np.array_equal(matrix, expected):
        raise ValueError("Recorded pixel-center transform differs from crop bounds")
    labels, count = ndimage.label(logits > 0, structure=ndimage.generate_binary_structure(2, 1))
    sizes = np.bincount(labels.ravel(), minlength=count+1)
    eligible = int(np.count_nonzero(sizes[1:] >= POLICY["minimum_component_grid_pixels"]))
    supported = eligible <= POLICY["maximum_retained_components_per_variant"]
    ledger, prompts = [], []
    slices = ndimage.find_objects(labels)
    for component, region in enumerate(slices, 1):
        ys, xs = region
        gy0, gy1, gx0, gx1 = ys.start, ys.stop, xs.start, xs.stop
        inside = labels[ys, xs] == component
        distance = ndimage.distance_transform_edt(np.pad(inside, 1))[1:-1, 1:-1]
        farthest = int(np.argmax(distance))
        local_y, local_x = np.unravel_index(farthest, inside.shape)
        grid_point = [int(gx0+local_x), int(gy0+local_y)]
        point = (matrix @ np.array([*grid_point, 1.]))[:2]
        clipped_point = np.clip(point, [0., 0.], [width-1., height-1.])
        lower = (matrix @ np.array([gx0-.5, gy0-.5, 1.]))[:2] + .5
        upper = (matrix @ np.array([gx1-.5, gy1-.5, 1.]))[:2] + .5
        padding = .10*(upper-lower)
        lower = np.maximum(0., lower-padding)
        upper = np.minimum([width, height], upper+padding)
        center = (lower+upper)/2
        span = np.maximum(1., upper-lower)
        lower = np.minimum(np.maximum(0., center-span/2), np.array([width, height])-span)
        upper = lower+span
        pixels = int(sizes[component])
        status = ("omitted_below_minimum_grid_support" if pixels < 16 else
                  "unsupported_variant_capacity" if not supported else "retained_prompt")
        proposal_id = f"{variant['name']}-component-{component:03d}"
        record = {"id": proposal_id, "component_label": component, "grid_pixels": pixels,
                  "grid_bbox_xyxy_exclusive": [gx0, gy0, gx1, gy1],
                  "grid_positive_point_xy": grid_point,
                  "interior_distance_grid_pixels": float(distance[local_y, local_x]),
                  "point_was_clamped": bool(np.any(point != clipped_point)),
                  "status": status, "semantic_identity": "unverified_photo_semantic_proposal"}
        if status == "retained_prompt":
            record["prompt"] = {"bbox_xyxy": [*lower.tolist(), *upper.tolist()],
                                "points_xy": [clipped_point.tolist()], "point_labels": [1]}
            prompts.append(record)
        ledger.append(record)
    return {"status": "proposals_recorded" if supported else "unsupported_component_capacity",
            "grid_positive_pixels": int(np.count_nonzero(logits > 0)), "component_count": count,
            "eligible_components": eligible, "retained_prompts": len(prompts),
            "omitted_small_components": int(np.count_nonzero(sizes[1:] < 16)),
            "omitted_small_grid_pixels": int(sizes[1:][sizes[1:] < 16].sum()),
            "components": ledger}, labels, prompts


def _coarse_native_crop(labels, component, box):
    x0, y0, x1, y1 = box
    rows = np.minimum(255, np.floor((np.arange(y1-y0)+.5)*256/(y1-y0)).astype(int))
    cols = np.minimum(255, np.floor((np.arange(x1-x0)+.5)*256/(x1-x0)).astype(int))
    return labels[np.ix_(rows, cols)] == component


def _panel(image, mask, title, display_box, color=(20, 140, 255)):
    x0, y0, x1, y1 = display_box
    rgb = np.asarray(image, dtype=np.uint8)[y0:y1, x0:x1].copy()
    if mask is not None:
        selected = mask[y0:y1, x0:x1]
        rgb[selected] = np.rint(.55*rgb[selected]+.45*np.asarray(color)).astype(np.uint8)
    picture = Image.fromarray(rgb)
    picture.thumbnail((304, 210))
    result = Image.new("RGB", (320, 246), "white")
    result.paste(picture, ((320-picture.width)//2, 30+(210-picture.height)//2))
    ImageDraw.Draw(result).text((5, 5), title, fill="black")
    return result


def run(baseline_report, output, *, weights_receipt):
    baseline_report, output, weights_receipt = (Path(value).resolve() for value in (baseline_report, output, weights_receipt))
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a fresh empty refinement output directory")
    pins = {}
    baseline = json.loads(_read_pinned(baseline_report, pins))
    if baseline.get("status") != "prediction_experiment_complete" or baseline.get("schema_version") != 1:
        raise ValueError("Upstream semantic experiment must be complete")
    rows = baseline.get("images", [])
    if not 1 <= len(rows) <= POLICY["maximum_photos"]:
        raise ValueError("Invalid or unsupported photo inventory")
    if len({row["id"] for row in rows}) != len(rows) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", row["id"]) for row in rows):
        raise ValueError("Unique safe photo IDs required")
    if baseline_report.is_relative_to(output) or weights_receipt.is_relative_to(output):
        raise ValueError("Upstream inputs must be outside output")
    for path, digest in {**baseline["input_sha256"], **baseline["implementation_sha256"]}.items():
        _read_pinned(Path(path), pins, digest)
    saved_engine = json.loads(_read_pinned(weights_receipt, pins))["engine"]
    weight_record = saved_engine["weights"]
    engine = OfflineSAM2RegionEngine(Path(weight_record["path"]), expected_sha256=weight_record["sha256"], runtime_dir=output/"offline-runtime")
    engine_identity = engine.describe()
    implementation = {str(Path(__file__).resolve()): _sha(__file__)}
    for name in ("region_engine.py", "photo_lens_observations.py"):
        path = ROOT/"reconstruction"/name
        implementation[str(path)] = _sha(path)
    captured = []
    for row in rows:
        folder = baseline_report.parent/row["id"]
        source = Path(row["source"]).resolve()
        if source.is_relative_to(output):
            raise ValueError("Original photo must be outside output")
        _read_pinned(source, pins, row["source_sha256"])
        normalized = row["normalization"]["normalized"]
        raw = _read_pinned(_child(folder, normalized["path"]), pins, normalized["sha256"])
        with Image.open(io.BytesIO(raw)) as decoded:
            decoded.load()
            if getattr(decoded, "n_frames", 1) != 1 or decoded.getexif().get(274, 1) != 1 or decoded.mode not in ("RGB", "RGBA"):
                raise ValueError("Expected a normalized single-frame RGB/RGBA photo")
            rgba = np.asarray(decoded.convert("RGBA"))
            if np.any(rgba[:, :, 3] != 255) or decoded.width*decoded.height > POLICY["maximum_native_pixels_per_photo"]:
                raise ValueError("Unsupported alpha or native image capacity")
            image = decoded.convert("RGB")
        if image.size != (normalized["width"], normalized["height"]):
            raise ValueError("Normalized photo dimensions mismatch")
        variants = row.get("variants", [])
        if [variant["name"] for variant in variants] != POLICY["upstream_variants"]:
            raise ValueError("Expected exactly the two frozen semantic variants")
        prepared = []
        for variant in variants:
            if variant["size_xy"] != list(image.size):
                raise ValueError("Semantic variant native dimensions mismatch")
            reference = variant["arrays"]
            arrays_raw = _read_pinned(_child(folder, reference["path"]), pins, reference["sha256"])
            with np.load(io.BytesIO(arrays_raw), allow_pickle=False) as arrays:
                logits = arrays["lenses_logits"].copy()
            result, labels, prompts = component_proposals(logits, variant, list(image.size))
            prepared.append((variant, result, labels, prompts))
        captured.append((row, raw, image, prepared))
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "method": "photo_semantic_to_offline_sam2_refinement_v1",
              "status": "running", "accepted": False, "semantic_accuracy": "unmeasured",
              "quality_verdict": "unmeasured", "baseline_report": str(baseline_report),
              "baseline_report_sha256": pins[str(baseline_report)], "policy": POLICY,
              "engine": engine_identity, "implementation_sha256": implementation,
              "packages": {"numpy": np.__version__, "scipy": scipy.__version__, "Pillow": PIL.__version__},
              "source_semantic_model": baseline["source_model"], "images": [],
              "limitations": [
                  "Upstream semantic predictions and downstream SAM masks are hypotheses; neither is semantic ground truth.",
                  "A false semantic lens proposal can yield a confident whole-object or mug mask; this stage does not reject such errors.",
                  "No source mesh, material name, role, camera, rendered mask or product-specific prompt is supplied.",
                  "All three decoder masks survive; no accepted mask, rank-based selection or optical material is inferred.",
                  "Coarse-versus-SAM IoU measures agreement of dependent predictions, not accuracy or improvement.",
                  "Crossproposal overlap may indicate duplicates, merges or unrelated regions; it does not verify physical lens count.",
                  "Outside each semantic crop is unknown; coarse-versus-SAM IoU is restricted to that crop and outside support is reported separately.",
                  "Per-decoder display unions are composites, not selected masks; decoder indices have no identity across prompts.",
                  "Horizontal-flip evidence is retained in the pinned upstream report and does not enter these prompts.",
                  "Catalog probes add single-photo input diversity, not unseen multiview reconstruction evidence or upstream-training holdouts.",
              ]}
    started = time.perf_counter()
    display_panels = []
    popcount = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1).sum(axis=1)
    for upstream, normalized_raw, image, prepared in captured:
        case_start = time.perf_counter()
        item = {"id": upstream["id"], "split": upstream["split"], "source": upstream["source"],
                "source_sha256": upstream["source_sha256"], "size_xy": list(image.size),
                "accepted": False, "semantic_identity": "unverified", "status": "running",
                "upstream_flip_diagnostics": {v["name"]: v["heads"]["lenses"]["flip_consistency"] for v in upstream["variants"]},
                "variants": [], "proposals": [], "crossproposal_overlap": []}
        report["images"].append(item)
        folder = output/item["id"]
        folder.mkdir()
        (folder/"normalized.png").write_bytes(normalized_raw)
        item["normalized_photo"] = _receipt(folder/"normalized.png", folder)
        try:
            all_prompts, lookup = [], {}
            for variant, summary, labels, prompts in prepared:
                label_path = folder/(variant["name"]+"-components.npz")
                np.savez_compressed(label_path, labels=labels)
                item["variants"].append({"name": variant["name"], "crop_xyxy_exclusive": variant["crop_xyxy_exclusive"],
                    "grid_to_native_pixel_center": variant["grid_to_native_pixel_center"],
                    "upstream_arrays": variant["arrays"], "labels": _receipt(label_path, folder), **summary})
                for proposal in prompts:
                    all_prompts.append(proposal["prompt"])
                    lookup[proposal["id"]] = (variant, labels, proposal)
            predictions = engine.predict(np.asarray(image), all_prompts) if all_prompts else []
            item["sam_predict_calls"] = int(bool(all_prompts))
            item["image_embeddings"] = int(bool(all_prompts))
            item["prompt_count"] = len(all_prompts)
            composites = {variant["name"]: [np.zeros((image.height, image.width), bool) for _ in range(3)] for variant, *_ in prepared}
            coarse_unions = {variant["name"]: np.zeros((image.height, image.width), bool) for variant, *_ in prepared}
            packed_masks = []
            for (proposal_id, (variant, labels, proposal)), alternatives in zip(lookup.items(), predictions, strict=True):
                pdir = folder/proposal_id
                pdir.mkdir()
                box = variant["crop_xyxy_exclusive"]
                x0, y0, x1, y1 = box
                coarse_crop = _coarse_native_crop(labels, proposal["component_label"], box)
                coarse_unions[variant["name"]][y0:y1, x0:x1] |= coarse_crop
                coarse = np.zeros((image.height, image.width), bool)
                coarse[y0:y1, x0:x1] = coarse_crop
                record = {**proposal, "variant": variant["name"], "source_crop_xyxy_exclusive": box,
                          "coarse_native_component": _mask_receipt(pdir/"coarse-component.png", coarse, folder), "alternatives": []}
                item["proposals"].append(record)
                for decoder, predicted in enumerate(alternatives):
                    mask = predicted["mask"]
                    if mask.dtype != np.bool_ or mask.shape != coarse.shape:
                        raise ValueError("SAM returned an invalid native mask")
                    count = int(mask.sum())
                    local = mask[y0:y1, x0:x1]
                    mask_record = _mask_receipt(pdir/f"decoder-{decoder}.png", mask, folder)
                    record["alternatives"].append({"decoder_index": decoder, "predicted_quality": predicted["predicted_quality"],
                        "quality_scope": "uncalibrated SAM mask quality, not semantic lens confidence", "mask": mask_record,
                        "coarse_vs_sam_within_source_crop": _iou(coarse_crop, local),
                        "sam_pixels_outside_semantic_crop": count-int(local.sum()),
                        "native_photo_fraction": count/mask.size})
                    composites[variant["name"]][decoder] |= mask
                    packed_masks.append((proposal_id, decoder, count, np.packbits(mask, axis=None)))
            for left, right in itertools.combinations(packed_masks, 2):
                if left[0] == right[0]:
                    continue
                intersection = int(popcount[np.bitwise_and(left[3], right[3])].sum())
                union = left[2]+right[2]-intersection
                item["crossproposal_overlap"].append({"left": {"proposal": left[0], "decoder": left[1]},
                    "right": {"proposal": right[0], "decoder": right[1]}, "intersection_pixels": intersection,
                    "union_pixels": union, "iou": intersection/union if union else None, "both_empty": union == 0})
            item["coverage"] = {"native_pixels": image.width*image.height,
                "semantic_grid_positive_pixels_by_variant": {v["name"]: s["grid_positive_pixels"] for v, s, *_ in prepared},
                "retained_coarse_native_pixels_by_variant": {name: int(mask.sum()) for name, mask in coarse_unions.items()},
                "sam_composite_pixels_by_variant_decoder": {name: [int(mask.sum()) for mask in masks] for name, masks in composites.items()},
                "retained_prompt_count": len(all_prompts), "decoder_masks": len(packed_masks)}
            display_box = next(v["crop_xyxy_exclusive"] for v, *_ in prepared if v["name"] == "contrast_crop")
            sheet = Image.new("RGB", (1600, 524), "white")
            ImageDraw.Draw(sheet).text((6, 5), item["id"]+" | COMPOSITES ONLY; all raw decoder masks retained", fill="black")
            item["display_composites"] = []
            for row_index, (variant, *_unused) in enumerate(prepared):
                name = variant["name"]
                panels = [("photo / "+name, None), ("coarse retained union", coarse_unions[name])]
                for decoder in range(3):
                    panels.append((f"SAM decoder {decoder} UNION", composites[name][decoder]))
                    path = folder/f"{name}-decoder-{decoder}-DISPLAY-COMPOSITE.png"
                    rec = _mask_receipt(path, composites[name][decoder], folder)
                    item["display_composites"].append({"variant": name, "decoder": decoder,
                        "meaning": "union of corresponding decoder index across all proposals; no accepted mask", **rec})
                for column, (title, mask) in enumerate(panels):
                    sheet.paste(_panel(image, mask, title, display_box), (column*320, 28+row_index*246))
            sheet.save(folder/"overlay.png")
            item["overlay"] = _receipt(folder/"overlay.png", folder)
            item["overlay"]["display_crop_xyxy_exclusive"] = display_box
            display_panels.append(sheet)
            item["status"] = ("unsupported_component_capacity" if any(s["status"] != "proposals_recorded" for _, s, *_ in prepared)
                              else "refinement_hypotheses_recorded" if all_prompts else "no_semantic_proposals")
        except Exception as error:
            item.update(status="failed", error_type=type(error).__name__, error=str(error))
        item["seconds"] = time.perf_counter()-case_start
        _write(folder/"report.json", item)
        item["case_report"] = _receipt(folder/"report.json", output)
        _write(output/"progress.json", {"status": "running", "completed_images": len(report["images"]),
                                       "images": [{key: row[key] for key in ("id", "status", "seconds")} for row in report["images"]]})
        print(json.dumps({key: item[key] for key in ("id", "status", "prompt_count", "seconds", "error") if key in item}), flush=True)
    for first in range(0, len(display_panels), 4):
        panels = display_panels[first:first+4]
        sheet = Image.new("RGB", (1600, 524*len(panels)), "white")
        for index, panel in enumerate(panels):
            sheet.paste(panel, (0, 524*index))
        sheet.save(output/f"contact-sheet-{first//4+1:02d}.png")
    if engine.describe() != engine_identity:
        raise ValueError("SAM engine identity changed during experiment")
    for path, digest in {**pins, **implementation}.items():
        if _sha(path) != digest:
            raise ValueError("Input or implementation changed during experiment: "+path)
    counts = Counter(row["status"] for row in report["images"])
    report.update(status="refinement_experiment_complete" if not (counts["failed"] or counts["unsupported_component_capacity"]) else "incomplete_experiment",
                  status_counts=dict(counts), seconds=time.perf_counter()-started, input_sha256=pins,
                  engine_execution=engine.receipt(), total_prompts=sum(row.get("prompt_count", 0) for row in report["images"]),
                  total_decoder_masks=sum(row.get("coverage", {}).get("decoder_masks", 0) for row in report["images"]))
    _write(output/"report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--weights-receipt", type=Path, required=True,
                        help="Existing saved region report; only its explicitly pinned SAM checkpoint is used")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.baseline_report, args.output, weights_receipt=args.weights_receipt)
    raise SystemExit(0 if result["status"] == "refinement_experiment_complete" else 1)
