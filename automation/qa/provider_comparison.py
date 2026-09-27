"""Local raw-GLB rendering and source-photo comparison, with no API calls.

Manifest: {"cases":[{"id":"oakley-rodin","product":"oakley","provider":"rodin",
"path":"relative/model.glb","rotation_degrees":[0,0,0]}],
"products":{"oakley":{"photos":{"front":"front.jpg","back":"back.jpg"}}}}.
Paths resolve from the manifest directory. Run with --manifest FILE --output DIR.
Optional manifest modes: raw, normals, unlit, primitives. Optional backgrounds:
light, dark, checker. The renderer only centers, rotates and uniformly scales.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import textwrap

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parents[1]
AR = ROOT.parent / "ar"
VIEWS = ("front", "back", "left", "right", "angled")


def font(size: int):
    for name in ("C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def put_image(sheet: Image.Image, path: Path, box: tuple[int, int, int, int]) -> None:
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
        white = Image.new("RGBA", image.size, "white")
        white.alpha_composite(image)
        image = ImageOps.contain(white.convert("RGB"), (box[2] - box[0], box[3] - box[1]))
    sheet.paste(image, (box[0] + (box[2] - box[0] - image.width) // 2,
                        box[1] + (box[3] - box[1] - image.height) // 2))


def candidate_cards(manifest_path: Path, output: Path, *, modes=("actual-ar",),
                    views=None, environments=None, backgrounds=None) -> dict:
    """Bind anonymous, one-candidate/environment review cards to model/render bytes.

    Identity stays in receipt metadata; the image contains only a neutral title,
    view columns and controlled-background labels. Source photos are separate.
    """
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if report.get("manifest_sha256") != manifest_digest:
        raise ValueError("Render manifest changed; cannot bind review cards to different inputs")
    if report.get("status") not in ("rendered", "inspected") or not report.get("source_snapshot_stable"):
        raise ValueError("Review cards require a completed render with a stable source snapshot")
    cards = []
    tile_width, tile_height, label_width, header_height = 480, 340, 190, 86
    for case in report["cases"]:
        if case["status"] not in ("rendered", "runtime_compatible"):
            continue
        selected = [r for r in case["renders"] if r["mode"] in modes
                    and (views is None or r["view"] in views)
                    and (environments is None or r.get("environment", "room") in environments)
                    and (backgrounds is None or r["background"] in backgrounds)]
        for mode, environment in dict.fromkeys((r["mode"], r.get("environment", "room")) for r in selected):
            renders = [r for r in selected if r["mode"] == mode and r.get("environment", "room") == environment]
            columns = [view for view in (views or list(dict.fromkeys(r["view"] for r in renders))) if any(r["view"] == view for r in renders)]
            rows = [background for background in (backgrounds or list(dict.fromkeys(r["background"] for r in renders))) if any(r["background"] == background for r in renders)]
            sheet = Image.new("RGB", (label_width + tile_width * len(columns), header_height + tile_height * len(rows)), "#eef1f5")
            draw = ImageDraw.Draw(sheet)
            draw.text((18, 14), "Candidate", font=font(28), fill="#202d3d")
            for column, view in enumerate(columns):
                draw.text((label_width + column * tile_width + 12, 58), view, font=font(19), fill="#202d3d")
            receipts = []
            for row_index, background in enumerate(rows):
                y = header_height + tile_height * row_index
                draw.text((12, y + 20), background, font=font(18), fill="#202d3d")
                for column, view in enumerate(columns):
                    matches = [r for r in renders if r["view"] == view and r["background"] == background]
                    if len(matches) != 1:
                        raise ValueError(f"Incomplete or duplicate card cell for {case['id']}/{environment}/{background}/{view}")
                    render = matches[0]
                    path = output / render["filename"]
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    if render.get("sha256") and digest != render["sha256"]:
                        raise ValueError(f"Render changed after its receipt: {path}")
                    put_image(sheet, path, (label_width + column * tile_width, y,
                                           label_width + (column + 1) * tile_width, y + tile_height - 8))
                    receipts.append({"view": view, "background": background, "path": str(path), "sha256": digest})
            path = output / f"{case['id']}__{mode}__env-{environment}__blind-card.png"
            sheet.save(path)
            cards.append({"id": case["id"], "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "model_sha256": case["model_sha256"], "environment": environment, "mode": mode,
                          "columns": columns, "rows": rows, "blind": True, "render_receipts": receipts,
                          "source_fixture": {"kind": report.get("background_fixture"), "color": report.get("background_color")},
                          "environment_configuration": next((e for e in report.get("environments", []) if e["id"] == environment), None)})
    receipt = {"schema_version": 1, "manifest_path": str(manifest_path), "manifest_sha256": manifest_digest,
               "render_report_sha256": hashlib.sha256((output / "report.json").read_bytes()).hexdigest(),
               "cards": cards, "scope": "Anonymous appearance-review inputs; no source photos, family names, parameter values, provider names or quality scores appear in the cards."}
    (output / "candidate-cards.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def contact_sheets(manifest_path: Path, output: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    products = defaultdict(list)
    for case in report["cases"]:
        products[case["product"]].append(case)
    cards, labeled_cards, differences = [], [], []
    tile_width, tile_height, label_width, header_height = 480, 340, 220, 98
    for product, cases in products.items():
        photos = manifest.get("products", {}).get(product, {}).get("photos", {})
        combinations = list(dict.fromkeys((r["mode"], r["background"], r.get("environment", "room"), r.get("environment_explicit", False))
                                         for case in cases for r in case["renders"] if r["mode"] != "background-audit"))
        for mode, background, environment, explicit in combinations:
                views = list(dict.fromkeys(r["view"] for case in cases for r in case["renders"]
                                           if (r["mode"], r["background"], r.get("environment", "room")) == (mode, background, environment)))
                rows = len(cases) + bool(photos)
                sheet = Image.new("RGB", (label_width + tile_width * len(views), header_height + rows * tile_height), "#eef1f5")
                draw = ImageDraw.Draw(sheet)
                draw.text((18, 14), f"{product} | {mode} | {background} | {environment}", font=font(28), fill="#202d3d")
                draw.text((18, 51), "Controlled viewer comparison. Source photos retain their camera/lighting; no calibrated photo accuracy or real-wearer claim.", font=font(17), fill="#47576b")
                for column, view in enumerate(views):
                    draw.text((label_width + column * tile_width + 12, 77), view, font=font(17), fill="#202d3d")
                row_index = 0
                if photos:
                    draw.text((15, header_height + 20), "Product photos", font=font(22), fill="#202d3d")
                    for column, view in enumerate(views):
                        photo = photos.get(view)
                        if isinstance(photo, dict):
                            photo = photo.get("path")
                        if photo:
                            path = (manifest_path.parent / photo).resolve()
                            put_image(sheet, path, (label_width + column * tile_width, header_height,
                                                   label_width + (column + 1) * tile_width, header_height + tile_height - 8))
                    row_index += 1
                for case in cases:
                    y = header_height + row_index * tile_height
                    details = (f"{case['triangles']:,.0f} triangles\n{case.get('material_count', 0)} materials"
                               if "triangles" in case else case["status"])
                    label = textwrap.fill(str(case['provider']), width=19)
                    text = f"{label}\n{details}\n{case.get('file_bytes', 0) / 1048576:.2f} MiB"
                    draw.multiline_text((15, y + 20), text, font=font(20), fill="#202d3d", spacing=9)
                    for column, view in enumerate(views):
                        render = next((r for r in case["renders"] if r["view"] == view and r["mode"] == mode and r["background"] == background and r.get("environment", "room") == environment), None)
                        if render:
                            put_image(sheet, output / render["filename"], (label_width + column * tile_width, y,
                                                                        label_width + (column + 1) * tile_width, y + tile_height - 8))
                    row_index += 1
                suffix = f"__env-{environment}" if explicit else ""
                name = f"{product}__{mode}__{background}{suffix}__comparison.png"
                sheet.save(output / name)
                cards.append(name)
        # One review card per case/environment: view columns, background rows, source photo row.
        # Layout is emitted explicitly so a vision reviewer never has to infer a legacy layout.
        for case in cases:
            rendered = [r for r in case["renders"] if r["mode"] in ("raw", "actual-ar")]
            for environment in dict.fromkeys(r.get("environment", "room") for r in rendered):
                selected = [r for r in rendered if r.get("environment", "room") == environment]
                views = list(dict.fromkeys(r["view"] for r in selected))
                backgrounds = list(dict.fromkeys(r["background"] for r in selected))
                rows = backgrounds.copy()
                if photos:
                    rows.insert(0, "source-photos")
                sheet = Image.new("RGB", (label_width + tile_width * len(views), header_height + len(rows) * tile_height), "#eef1f5")
                draw = ImageDraw.Draw(sheet)
                draw.text((18, 14), f"{case['id']} | environment {environment}", font=font(27), fill="#202d3d")
                draw.text((18, 51), "Photos are uncalibrated references. Rows/columns are explicit; empty photo cells have no matching source view.", font=font(17), fill="#47576b")
                for column, view in enumerate(views):
                    draw.text((label_width + column * tile_width + 12, 77), view, font=font(17), fill="#202d3d")
                for row_index, background in enumerate(rows):
                    y = header_height + row_index * tile_height
                    draw.text((15, y + 20), background, font=font(20), fill="#202d3d")
                    for column, view in enumerate(views):
                        path = None
                        if background == "source-photos":
                            photo = photos.get(view)
                            if isinstance(photo, dict):
                                photo = photo.get("path")
                            if photo:
                                path = (manifest_path.parent / photo).resolve()
                        else:
                            render = next((r for r in selected if r["background"] == background and r["view"] == view), None)
                            if render:
                                path = output / render["filename"]
                        if path:
                            put_image(sheet, path, (label_width + column * tile_width, y,
                                                    label_width + (column + 1) * tile_width, y + tile_height - 8))
                name = f"{case['id']}__env-{environment}__candidate-card.png"
                sheet.save(output / name)
                labeled_cards.append({"id": case["id"], "environment": environment, "filename": name,
                                        "columns": views, "rows": rows, "source_photo_camera_calibrated": False})
            # Verify lighting has a measurable effect; this is not an image-accuracy metric.
            keyed = defaultdict(list)
            for render in rendered:
                keyed[(render["mode"], render["background"], render["view"])].append(render)
            for (mode, background, view), variants in keyed.items():
                if len(variants) < 2:
                    continue
                with Image.open(output / variants[0]["filename"]) as image:
                    baseline = image.convert("RGB")
                for variant in variants[1:]:
                    with Image.open(output / variant["filename"]) as image:
                        delta = ImageChops.difference(baseline, image.convert("RGB"))
                    differences.append({"id": case["id"], "mode": mode, "background": background, "view": view,
                                        "environments": [variants[0].get("environment", "room"), variant.get("environment", "room")],
                                        "mean_absolute_rgb_delta": ImageStat.Stat(delta).mean,
                                        "maximum_rgb_delta": max(value[1] for value in delta.getextrema()),
                                        "meaning": "Whole-image lighting response only; not product accuracy or acceptance"})
    (output / "contact-sheets.json").write_text(json.dumps({"files": cards}, indent=2) + "\n", encoding="utf-8")
    (output / "labeled-candidate-cards.json").write_text(json.dumps({"cards": labeled_cards}, indent=2) + "\n", encoding="utf-8")
    (output / "environment-differences.json").write_text(json.dumps({"accepted": False, "comparisons": differences}, indent=2) + "\n", encoding="utf-8")
    candidate_cards(manifest_path, output, modes=("actual-ar", "raw"))
    return {"output": str(output), "contact_sheets": cards,
            "cases": [{key: case.get(key) for key in ("id", "status", "triangles", "vertices", "file_bytes", "mesh_count", "material_count")} for case in report["cases"]]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sheets-only", action="store_true")
    parser.add_argument("--ar-check", action="store_true", help="Attempt unchanged-byte actual AR handover; record rejection without inferred repairs")
    args = parser.parse_args()
    manifest, output = args.manifest.resolve(), args.output.resolve()
    if not args.sheets_only:
        node = shutil.which("node")
        if not node:
            raise SystemExit("Node.js unavailable; no render attempted")
        subprocess.run([node, str(AR / "qa/provider-comparison.mjs"), f"--manifest={manifest}", f"--output={output}",
                        f"--stage={'ar' if args.ar_check else 'raw'}"], cwd=AR, check=True)
    print(json.dumps(contact_sheets(manifest, output), indent=2))


if __name__ == "__main__":
    main()
