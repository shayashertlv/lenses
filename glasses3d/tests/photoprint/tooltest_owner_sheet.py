"""WP5 tool test (B4): ONE owner sheet comparing q001 as Astra textured it with the tool-test copy painted from the six
FT1119 photos, both captured by arview (``arview/qa/capture.mjs --poses default``).

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.tooltest_owner_sheet [--painted DIR]
        [--original DIR] [--out PNG]

Row 1: the six real photos (authoritative, uncropped, numbered as the reference files). Row 2: original q001 (Astra's
texture). Row 3: the painted TOOL TEST copy. Rows 2 and 3 show the same poses (front, yaw -30, yaw +45, pitch +20, skin
background, beauty variant), each cropped to the SAME box per pose: the union of both captures' glasses pixels read from
their part-id renders (black background), plus a margin. The last column is the worn front portrait's eye crop (runtime
lighting, condition a) of each. arview's yaw > 0 turns the nose toward image +x, so the asset's -x side is near the
camera at yaw +45 and its +x side at yaw -30; sides are named by the x sign only.
The painted copy is a TOOL TEST (a print attached to a COPY of q001), never a delivered model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

GLASSES3D = Path(__file__).resolve().parents[2]
DATA = GLASSES3D / "data"
TOOLTEST = DATA / "wp5" / "tooltest"
PHOTOS = DATA / "reference" / "ft1119"
POSES = [("front", "front"), ("yaw_m30", "yaw -30  (+x side near)"), ("yaw_p45", "yaw +45  (-x side near)"),
         ("pitch_p20", "pitch +20  (from above)")]
MARGIN = 22
ROW_H = 280
PHOTO_H = 300
GAP = 14
FONT = "C:/Windows/Fonts/arial.ttf"
FONT_B = "C:/Windows/Fonts/arialbd.ttf"


def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def glasses_box(part_id: Path) -> tuple[int, int, int, int]:
    a = np.asarray(Image.open(part_id).convert("RGB")).astype(np.int32)
    ys, xs = np.nonzero(a.sum(axis=2) > 30)
    if xs.size == 0:
        raise SystemExit(f"no glasses pixels in {part_id}")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def union_box(boxes, size) -> tuple[int, int, int, int]:
    x0 = max(0, min(b[0] for b in boxes) - MARGIN)
    y0 = max(0, min(b[1] for b in boxes) - MARGIN)
    x1 = min(size[0], max(b[2] for b in boxes) + MARGIN)
    y1 = min(size[1], max(b[3] for b in boxes) + MARGIN)
    return x0, y0, x1, y1


def fit_h(im: Image.Image, h: int) -> Image.Image:
    w = round(im.width * h / im.height)
    return im.resize((w, h), Image.LANCZOS)


def build(painted: Path, original: Path, out: Path) -> dict:
    caps = {"original": original, "painted": painted}
    tiles = {"original": [], "painted": []}
    crops = {}
    for pose, caption in POSES:
        boxes = [glasses_box(caps[k] / f"{pose}__part-id.png") for k in caps]
        size = Image.open(caps["painted"] / f"{pose}__skin__beauty.png").size
        box = union_box(boxes, size)
        crops[pose] = box
        for k in caps:
            im = Image.open(caps[k] / f"{pose}__skin__beauty.png").convert("RGB").crop(box)
            tiles[k].append((fit_h(im, ROW_H), caption))
    for k in caps:
        im = Image.open(caps[k] / "portrait__a__eyes.png").convert("RGB")
        tiles[k].append((fit_h(im, ROW_H), "worn (front portrait)"))

    photos = []
    for i in range(1, 7):
        im = Image.open(PHOTOS / f"reference-{i:02d}.png").convert("RGB")
        photos.append((fit_h(im, PHOTO_H), f"{i:02d}"))

    def row_w(row):
        return sum(t.width for t, _ in row) + GAP * (len(row) - 1)

    width = max(row_w(photos), row_w(tiles["original"]), row_w(tiles["painted"])) + 2 * 24
    f_title, f_row, f_cap = _font(FONT_B, 34), _font(FONT_B, 30), _font(FONT, 22)
    head, row_label, cap_h = 70, 50, 30
    height = head + 3 * (row_label + cap_h) + PHOTO_H + 2 * ROW_H + 2 * 28 + 70
    sheet = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    d.text((24, 18), "Tom Ford FT1119: same 3D shape (q001), two textures, in the AR try-on", fill=(20, 20, 20),
           font=f_title)
    y = head
    rows = [("your photos", (20, 20, 20), photos, PHOTO_H),
            ("now: Astra's texture", (150, 20, 20), tiles["original"], ROW_H),
            ("painted from your photos (tool test)", (20, 110, 30), tiles["painted"], ROW_H)]
    for label, colour, row, h in rows:
        d.text((24, y + 8), label, fill=colour, font=f_row)
        y += row_label
        x = 24
        for im, cap in row:
            d.text((x + 4, y), cap, fill=(70, 70, 70), font=f_cap)
            sheet.paste(im, (x, y + cap_h))
            x += im.width + GAP
        y += cap_h + h + 28
    d.text((24, y), "Rows 2 and 3: same poses, same crop. Tool test = the photo print attached to a COPY of q001; "
           "not a delivered model.", fill=(90, 90, 90), font=f_cap)
    sheet = sheet.crop((0, 0, width, y + 40))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    rec = {"sheet": str(out), "size": list(sheet.size), "crops": crops,
           "painted": str(painted), "original": str(original)}
    out.with_suffix(".json").write_text(json.dumps(rec, indent=1))
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--painted", type=Path, default=TOOLTEST / "arview_painted")
    ap.add_argument("--original", type=Path, default=TOOLTEST / "arview_original")
    ap.add_argument("--out", type=Path, default=TOOLTEST / "owner_sheet.png")
    a = ap.parse_args(argv)
    print(json.dumps(build(a.painted, a.original, a.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
