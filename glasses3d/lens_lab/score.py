"""Fixed-photo-region measurements for the $0 lens lab, never a product gate.

The two cameras, owner photos, accepted-run images and photo-space masks are
frozen before any candidate is measured. Candidate masks never choose pixels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import binary_erosion

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "glasses3d/data/lens_lab"
DEFAULT_BASIS = DATA / "scoring/basis"
REGIONS = ("left", "centre", "right")
DIAGNOSTIC = "DIAGNOSTIC — not a product, not given to Astra"


def output_path(path):
    """Resolve a private lab destination before any output or tool mutation."""
    path = Path(path).resolve()
    root = DATA.resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError("Lab output must be a private subdirectory of glasses3d/data/lens_lab")
    return path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def rgb(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def lab(rgb8):
    """sRGB/D65 CIELAB. No image normalization or white balance."""
    srgb = np.asarray(rgb8, dtype=np.float64) / 255
    linear = np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)
    xyz = linear @ np.array([[.4124564, .2126729, .0193339],
                             [.3575761, .7151522, .1191920],
                             [.1804375, .0721750, .9503041]])
    xyz /= np.array([.95047, 1., 1.08883])
    delta = 6 / 29
    f = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3 * delta**2) + 4 / 29)
    return np.stack((116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])), axis=-1)


def colour_stats(pixels, mask):
    pixels = np.asarray(pixels)
    mask = np.asarray(mask, dtype=bool)
    if pixels.shape[:2] != mask.shape or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("RGB image and fixed photo mask must have identical dimensions")
    selected = pixels[mask]
    if not len(selected):
        raise ValueError("a fixed scoring region has no pixels")
    # Exact same luminance coefficients as agent.tools.look.colour_stats.
    srgb = selected.astype(float) / 255
    linear = np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)
    luminance = linear @ np.array([.2126, .7152, .0722])
    lightness = np.where(luminance > (6 / 29)**3, 116 * np.cbrt(luminance) - 16,
                         (29 / 3)**3 * luminance)
    return {"pixels": len(selected), "median_srgb_8bit": np.median(selected, axis=0).tolist(),
            "median_lightness_Lstar": float(np.median(lightness)),
            "median_Lab_D65": np.median(lab(selected), axis=0).tolist()}


def photo_regions(mask):
    """Image-left/centre/right horizontal thirds of the one frozen photo mask."""
    mask = np.asarray(mask, bool)
    _, x = np.nonzero(mask)
    if not len(x):
        raise ValueError("empty photo lens mask")
    lo, hi = int(x.min()), int(x.max()) + 1
    boundaries = [lo + (hi - lo) / 3, lo + 2 * (hi - lo) / 3]
    xx = np.arange(mask.shape[1])[None, :]
    encoded = np.where(mask, 1 + (xx >= boundaries[0]) + (xx >= boundaries[1]), 0).astype(np.uint8)
    if any(not np.any(encoded == index) for index in (1, 2, 3)):
        raise ValueError("all three photo regions must have pixels")
    return encoded, {"x_min": lo, "x_max_exclusive": hi, "boundaries_x": boundaries,
                     "convention": "image-left/centre/image-right, equal horizontal thirds, not wearer sides"}


def measurements(pixels, encoded):
    return {"whole_lens": colour_stats(pixels, encoded > 0),
            "regions": {name: colour_stats(pixels, encoded == index)
                        for index, name in enumerate(REGIONS, 1)}}


def regional_errors(reference, candidate):
    errors = {}
    for name in REGIONS:
        target, actual = reference["regions"][name], candidate["regions"][name]
        errors[name] = {
            "delta_Lstar": actual["median_lightness_Lstar"] - target["median_lightness_Lstar"],
            "delta_rgb_8bit": (np.array(actual["median_srgb_8bit"]) -
                             np.array(target["median_srgb_8bit"])).tolist(),
            "delta_E76_of_median_Lab": float(np.linalg.norm(np.array(actual["median_Lab_D65"]) -
                                                            target["median_Lab_D65"]))}
    return errors


def front_pattern(measured):
    """Descriptive signs, not a match/acceptance gate; image review remains necessary."""
    regions = measured["regions"]
    left, centre, right = [regions[name]["median_Lab_D65"] for name in REGIONS]
    return {"centre_a_star": centre[1], "left_a_star": left[1], "right_a_star": right[1],
            "left_b_star": left[2], "right_b_star": right[2],
            "purple_sides_green_centre_signs": bool(centre[1] < 0 and left[1] > 0 and right[1] > 0
                                                   and left[2] < 0 and right[2] < 0),
            "minimum_side_minus_centre_a_star": min(left[1], right[1]) - centre[1],
            "note": "Signs describe regional medians only; do not establish spectral fidelity or visual acceptance."}


def _font(size):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def _box(mask, margin=15):
    ys, xs = np.nonzero(mask)
    return [max(0, int(xs.min()) - margin), max(0, int(ys.min()) - margin),
            min(mask.shape[1], int(xs.max()) + margin + 1),
            min(mask.shape[0], int(ys.max()) + margin + 1)]


def prepare_basis(output_dir=None, *, source_report=None, annotation_path=None):
    """Copy and bind existing task-13 evidence; creates no renderer process."""
    output = output_path(output_dir or DEFAULT_BASIS)
    if (output / "basis.json").exists():
        return load_basis(output)
    source = Path(source_report or REPO /
                  "glasses3d/data/review_20261018/codex/task13_work/run4/look-result.json").resolve()
    annotations = Path(annotation_path or DATA / "scoring/photo_lens_annotations.json").resolve()
    evidence, annotation = read_json(source), read_json(annotations)
    if evidence["status"] != "ok":
        raise ValueError("baseline comparison did not succeed")
    output.mkdir(parents=True, exist_ok=True)
    rows, assets = [], {}
    for rid in ("reference-01", "reference-02"):
        row = next(row for row in evidence["views"] if row["ref"] == rid)
        source_photo, source_run4 = Path(row["photo_path"]), Path(row["drawing_path"])
        capture_report = read_json(source_run4.parent / "report.json")
        if capture_report["input"]["sha256"] != evidence["export_sha256"]:
            raise ValueError("run-4 baseline image is not bound to its export")
        record = next(item for item in capture_report["images"] if item["file"] == source_run4.name)
        if sha256(source_run4) != record["sha256"]:
            raise ValueError("run-4 baseline image bytes changed")
        photo, accepted = output / f"{rid}-photo.png", output / f"{rid}-run4.png"
        shutil.copyfile(source_photo, photo)
        shutil.copyfile(source_run4, accepted)
        a = annotation["views"][rid]
        if sha256(photo) != a["photo_sha256"]:
            raise ValueError("photo mask annotation belongs to different photo bytes")
        shape = rgb(photo).shape[:2]
        if shape != (row["camera"]["height"], row["camera"]["width"]):
            raise ValueError("photo and frozen camera dimensions differ")
        mask_image = Image.new("L", (shape[1], shape[0]))
        ImageDraw.Draw(mask_image).polygon([tuple(point) for point in a["polygon_px"]], fill=255)
        mask = binary_erosion(np.asarray(mask_image) > 0, structure=np.ones((7, 7), bool))
        encoded, division = photo_regions(mask)
        region_path = output / f"{rid}-regions.png"
        Image.fromarray(encoded).save(region_path)
        overlay = rgb(photo).copy()
        tint = np.array([[245, 119, 211], [100, 237, 157], [139, 153, 255]])
        for index in (1, 2, 3):
            selected = encoded == index
            overlay[selected] = np.rint(overlay[selected] * .72 + tint[index - 1] * .28)
        overlay_image = Image.fromarray(overlay)
        draw = ImageDraw.Draw(overlay_image)
        draw.text((24, 24), f"LAB scoring mask: {rid} | frozen PHOTO outline; left / centre / right",
                  fill="black", font=_font(23))
        overlay_path = output / f"{rid}-mask-overlay.png"
        overlay_image.save(overlay_path)
        rows.append({"ref": rid, "view": "front" if rid == "reference-01" else "angled",
                     "camera": row["camera"], "photo": photo.name, "run4": accepted.name,
                     "region_mask": region_path.name, "mask_overlay": overlay_path.name,
                     "division": division, "lens_box": _box(encoded > 0),
                     "photo_measurements": measurements(rgb(photo), encoded),
                     "run4_measurements": measurements(rgb(accepted), encoded)})
        for path in (photo, accepted, region_path, overlay_path):
            assets[path.name] = sha256(path)
    camera_path = output / "cameras.json"
    write_json(camera_path, {"schema": "arview.cameras/1", "cameras": [row["camera"] for row in rows]})
    assets[camera_path.name] = sha256(camera_path)
    basis = {"schema": "g3d.lens_lab.scoring_basis/1", "label": "LAB — diagnostic measurements only",
             "source_report": str(source), "source_report_sha256": sha256(source),
             "annotation_path": str(annotations), "annotation_sha256": sha256(annotations),
             "accepted_glb_sha256": evidence["export_sha256"], "camera_file": camera_path.name,
             "method": "Photo-space lens outline annotated once, eroded 3 pixels and split into horizontal "
                       "thirds. The same immutable pixels measure the photo, accepted run 4 and all candidates. "
                       "All pixels inside each region are retained (highlights, branding and visible temples). "
                       "No candidate masks, exposure/color correction, pixel selection or camera refitting.",
             "objective": "Equal-weight mean CIE76 distance of per-pixel Lab coordinate medians over 3 regions "
                          "in each of 2 views (6 regions). Diagnostic ranking only, never a production gate.",
             "views": rows, "assets": assets}
    write_json(output / "basis.json", basis)
    return load_basis(output)


def load_basis(directory=None):
    directory = Path(directory or DEFAULT_BASIS).resolve()
    document = read_json(directory / "basis.json")
    for relative, digest in document["assets"].items():
        target = (directory / relative).resolve()
        if not target.is_relative_to(directory) or sha256(target) != digest:
            raise ValueError(f"frozen scoring asset changed: {relative}")
    return {**document, "directory": str(directory), "basis_sha256": sha256(directory / "basis.json")}


def capture_glb(glb, output_dir, *, basis_dir=None):
    """Real pinned AR renderer, task-13 cameras, authored scale, lensenv=1."""
    output = output_path(output_dir)
    from glasses3d.agent.tools import arview
    from glasses3d.agent import procs
    from glasses3d.lens_lab import harness
    # The harness performs the task-17 barrier as well; standalone score must obey it.
    harness.ensure_heavy_allowed()
    basis = load_basis(basis_dir)
    output.mkdir(parents=True, exist_ok=False)
    glb = Path(glb).resolve()
    model = output / "lab-model.glb"
    shutil.copyfile(glb, model)
    digest = sha256(model)
    argv = [shutil.which("node") or "node", str(arview.ARVIEW / "bin/arview.mjs"),
            "capture", str(model), "--cameras", str(Path(basis["directory"]) / basis["camera_file"]),
            "--no-face", "--fixed-scale", "--lensenv", "1", "--out", str(output / "capture")]
    started = time.time()
    env = {key: value for key, value in os.environ.items() if key.upper() in procs.WORKER_ENV_KEYS}
    temp = output / "temp"
    temp.mkdir()
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1",
               TEMP=str(temp), TMP=str(temp))
    process = {"argv": argv, "started_unix": started, "glb_sha256": digest,
               "parent_pid": os.getpid(), "blas_threads": 1, "timeout_wrapper": False}
    job = None
    try:
        with (output / "capture.log").open("ab") as log:
            proc, job = procs.spawn_in_job(argv, cwd=arview.ARVIEW, stdout=log, env=env)
            process.update(pid=proc.pid, qos=procs.qos_state(proc))
            write_json(output / "capture-process.json", process)
            code = proc.wait()
    finally:
        if job is not None:
            job.close()
    process.update(elapsed_s=time.time() - started, exit=code, finished_unix=time.time())
    write_json(output / "capture-process.json", process)
    if code not in (0, 3):
        raise RuntimeError(f"lab capture failed: exit={code}")
    report = read_json(output / "capture/report.json")
    if report["input"]["sha256"] != digest or report.get("errors"):
        raise ValueError("AR capture did not verify the candidate export")
    for item in report["images"]:
        path = (output / "capture" / item["file"]).resolve()
        if not path.is_relative_to(output / "capture") or sha256(path) != item["sha256"]:
            raise ValueError("AR capture image integrity failure")
    return output / "capture"


def comparison_sheet(basis, records, output, *, label):
    """One labelled native-data comparison image per candidate; both views."""
    width, gutter, header = 660, 18, 122
    root = Path(basis["directory"])
    heights = []
    for row in basis["views"]:
        box = row["lens_box"]
        heights.append(84 + round((box[3] - box[1]) * ((width - 2 * gutter) / (box[2] - box[0]))))
    canvas = Image.new("RGB", (width * 3, header + sum(heights) + 30), "#eeeeee")
    draw = ImageDraw.Draw(canvas)
    draw.text((gutter, 14), label, fill="black", font=_font(27))
    draw.text((gutter, 53), "LAB | Fixed photo regions and frozen run-4 cameras; equal crop/scale; no colour correction.",
              fill="black", font=_font(21))
    for index, title in enumerate(("OWNER PHOTO", "ACCEPTED RUN 4 — lab reference", "LAB CANDIDATE")):
        draw.text((index * width + gutter, 89), title, fill="black", font=_font(23))
    top = header
    for row, record, height in zip(basis["views"], records, heights):
        images = [root / row["photo"], root / row["run4"], Path(record["drawing"])]
        values = [row["photo_measurements"], row["run4_measurements"], record["trial"]]
        box = row["lens_box"]
        scale = (width - 2 * gutter) / (box[2] - box[0])
        for index, (path, measured) in enumerate(zip(images, values)):
            x = index * width + gutter
            draw.text((x, top), f"{row['ref']} / {row['view']} | L* {measured['whole_lens']['median_lightness_Lstar']:.2f}",
                      fill="black", font=_font(20))
            regional = " / ".join(f"{name} L* {measured['regions'][name]['median_lightness_Lstar']:.1f}"
                                  for name in REGIONS)
            draw.text((x, top + 28), regional, fill="black", font=_font(17))
            tile = Image.open(path).convert("RGB").crop(box)
            tile = tile.resize((round(tile.width * scale), round(tile.height * scale)), Image.Resampling.LANCZOS)
            canvas.paste(tile, (x, top + 62))
        top += height
    canvas.save(output)


def score_capture(capture_dir, output_dir, *, basis_dir=None, trial_label="LAB trial", make_sheet=True):
    output = output_path(output_dir)
    basis = load_basis(basis_dir)
    root, capture = Path(basis["directory"]), Path(capture_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = read_json(capture / "report.json")
    records, errors, baseline_errors = [], [], []
    for row in basis["views"]:
        drawing = capture / f"{row['camera']['id']}__beauty.png"
        image_record = next(item for item in report["images"] if item["file"] == drawing.name)
        if sha256(drawing) != image_record["sha256"]:
            raise ValueError("candidate beauty image hash mismatch")
        encoded = np.asarray(Image.open(root / row["region_mask"]))
        measured = measurements(rgb(drawing), encoded)
        regional = regional_errors(row["photo_measurements"], measured)
        accepted = regional_errors(row["photo_measurements"], row["run4_measurements"])
        errors.extend(item["delta_E76_of_median_Lab"] for item in regional.values())
        baseline_errors.extend(item["delta_E76_of_median_Lab"] for item in accepted.values())
        records.append({"ref": row["ref"], "drawing": str(drawing), "drawing_sha256": sha256(drawing),
                        "photo": row["photo_measurements"], "run4": row["run4_measurements"], "trial": measured,
                        "regional_error": regional, "run4_regional_error": accepted})
    result = {"schema": "g3d.lens_lab.score/1", "label": trial_label, "diagnostic_only": True,
              "basis_sha256": basis["basis_sha256"], "candidate_glb_sha256": report["input"]["sha256"],
              "capture_report": str(capture / "report.json"), "capture_report_sha256": sha256(capture / "report.json"),
              "objective": basis["objective"], "mean_regional_delta_E76": float(np.mean(errors)),
              "run4_mean_regional_delta_E76": float(np.mean(baseline_errors)), "views": records,
              "front_pattern": front_pattern(records[0]["trial"]),
              "photo_front_pattern": front_pattern(records[0]["photo"])}
    if make_sheet:
        path = output / "comparison.png"
        comparison_sheet(basis, records, path, label=trial_label)
        result.update(comparison_image=str(path), comparison_sha256=sha256(path))
    write_json(output / "score.json", result)
    return result


def score_glb(glb, output_dir, *, basis_dir=None, trial_label="LAB trial", make_sheet=True):
    output = output_path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    capture = capture_glb(glb, output / "render", basis_dir=basis_dir)
    return score_capture(capture, output, basis_dir=basis_dir, trial_label=trial_label, make_sheet=make_sheet)


def score_trial(trial_dir, output_dir=None, *, basis_dir=None):
    trial = output_path(trial_dir)
    output = output_path(output_dir or trial / "score")
    manifest = read_json(trial / "trial.json")
    job = Path(manifest["job_dir"]).resolve()
    if job != (trial / "job").resolve() or not job.is_relative_to(trial):
        raise ValueError("The lab job_dir must be this trial's private job directory")
    if (trial / "finish.json").exists():
        finished = read_json(trial / "finish.json")
        if finished.get("status") != "lab_finished":
            raise ValueError("trial finish did not succeed")
        export = finished["export"]
    else:
        from glasses3d.lens_lab import harness
        export = harness.call_tool(trial, "export_glb", {})["result"]
    if export.get("status") != "exported":
        raise RuntimeError(f"trial did not export: {export}")
    glb = (job / export["path"]).resolve()
    if not glb.is_relative_to(job) or sha256(glb) != export["sha256"]:
        raise ValueError("finished trial export escaped its copy or changed bytes")
    return score_glb(glb, output, basis_dir=basis_dir, make_sheet=manifest.get("variant") != "candidates",
                     trial_label="LAB Codex-as-Astra trial — not a product")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    trial = sub.add_parser("score-trial", help="Score a finished private lab trial")
    trial.add_argument("trial_dir", type=Path)
    trial.add_argument("--output-dir", type=Path)
    trial.add_argument("--basis-dir", type=Path)
    glb = sub.add_parser("score-glb", help="Capture and measure a copied lab GLB")
    glb.add_argument("glb", type=Path)
    glb.add_argument("output_dir", type=Path)
    glb.add_argument("--basis-dir", type=Path)
    glb.add_argument("--label", default="LAB trial — not a product")
    prepare = sub.add_parser("prepare-basis", help="Freeze the task-13 evidence and photo mask annotations; no render")
    prepare.add_argument("--output-dir", type=Path)
    prepare.add_argument("--source-report", type=Path)
    prepare.add_argument("--annotation-path", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare-basis":
        result = prepare_basis(args.output_dir, source_report=args.source_report, annotation_path=args.annotation_path)
        print(json.dumps({"basis_json": str(Path(result["directory"]) / "basis.json"),
                          "basis_sha256": result["basis_sha256"]}, indent=2))
        return 0
    if args.command == "score-trial":
        result = score_trial(args.trial_dir, args.output_dir, basis_dir=args.basis_dir)
        output = args.output_dir or args.trial_dir / "score"
    else:
        result = score_glb(args.glb, args.output_dir, basis_dir=args.basis_dir, trial_label=args.label)
        output = args.output_dir
    summary = {"score_json": str(output.resolve() / "score.json"),
               "mean_regional_delta_E76": result["mean_regional_delta_E76"]}
    if "comparison_image" in result:
        summary["comparison_image"] = result["comparison_image"]
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
