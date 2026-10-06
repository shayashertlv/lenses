"""WP5 tool test (B4): ONE owner sheet comparing q001 as Astra textured it with the tool-test copy painted from the six
FT1119 photos, both captured by arview (``arview/qa/capture.mjs --poses default``).

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.tooltest_owner_sheet [--painted DIR]
        [--original DIR] [--out PNG] [--before DIR] [--candidate LABEL=DIR ...] [--calibration calibration.json]

Row 1: the six real photos (authoritative, uncropped, numbered as the reference files). Row 2: original q001 (Astra's
texture). Row 3: the painted TOOL TEST copy. Rows 2 and 3 show the same poses (front, yaw -30, yaw +45, pitch +20, skin
background, beauty variant), each cropped to the SAME box per pose: the union of both captures' glasses pixels read from
their part-id renders (black background), plus a margin. The last column is the worn front portrait's eye crop (runtime
lighting, condition a) of each. arview's yaw > 0 turns the nose toward image +x, so the asset's -x side is near the
camera at yaw +45 and its +x side at yaw -30; sides are named by the x sign only.
The painted copy is a TOOL TEST (a print attached to a COPY of q001), never a delivered model.
Fix round 1 of the correction round: ``--candidate LABEL=DIR`` adds a row per candidate AR level (the calibration's
captures of the same tool-test GLB with another level: the owner judges between them by eye), and when every capture
has the pose at photo 01's own angle (yaw -30, pitch +20: ``--poses all``) it is shown as an extra column; a capture
without the worn portrait gets an empty tile there.
Fix round 2: ``--calibration FILE`` (an ``ar_calibrate`` calibration.json) adds a row per owner candidate that is not
the delivered level, labelled with the candidate's own label (what it was fitted on), from its ``sheet_capture`` (the
re-capture WITH the worn portrait); the painted row's default label is the delivered candidate's. The front and yaw -30
captions say that photos 05 / 01 were taken 18 deg above those views.
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
POSES = [("front", "front  (photo 05: 18 deg above)"), ("yaw_m30", "yaw -30  (+x near; photo 01: 18 deg above)"),
         ("yaw_p45", "yaw +45  (-x side near)"),
         ("pitch_p20", "pitch +20  (from above: photo 05's angle)")]
EXTRA_POSES = [("yaw_m30_pitch_p20", "yaw -30, pitch +20  (photo 01's angle)")]
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


def build(painted: Path, original: Path, out: Path, before: Path | None = None, candidates: dict | None = None,
          painted_label: str | None = None, before_label: str | None = None) -> dict:
    caps = {"original": original, "painted": painted}
    if before is not None:                      # correction round: the previous painted tool test as a middle row
        caps = {"original": original, "before": before, "painted": painted}
    for lab, d in (candidates or {}).items():  # fix round 1: candidate AR levels, one row each
        caps[f"candidate: {lab}"] = Path(d)
    tiles = {k: [] for k in caps}
    crops = {}
    poses = POSES + [pc for pc in EXTRA_POSES if all((caps[k] / f"{pc[0]}__part-id.png").is_file() for k in caps)]
    for pose, caption in poses:
        boxes = [glasses_box(caps[k] / f"{pose}__part-id.png") for k in caps]
        size = Image.open(caps["painted"] / f"{pose}__skin__beauty.png").size
        box = union_box(boxes, size)
        crops[pose] = box
        for k in caps:
            im = Image.open(caps[k] / f"{pose}__skin__beauty.png").convert("RGB").crop(box)
            tiles[k].append((fit_h(im, ROW_H), caption))
    for k in caps:
        f = caps[k] / "portrait__a__eyes.png"
        if f.is_file():
            tiles[k].append((fit_h(Image.open(f).convert("RGB"), ROW_H), "worn (front portrait)"))
        else:
            tiles[k].append((Image.new("RGB", (ROW_H, ROW_H), (235, 235, 235)), "(no portrait captured)"))

    photos = []
    for i in range(1, 7):
        im = Image.open(PHOTOS / f"reference-{i:02d}.png").convert("RGB")
        photos.append((fit_h(im, PHOTO_H), f"{i:02d}"))

    def row_w(row):
        return sum(t.width for t, _ in row) + GAP * (len(row) - 1)

    width = max([row_w(photos)] + [row_w(tiles[k]) for k in caps]) + 2 * 24
    f_title, f_row, f_cap = _font(FONT_B, 34), _font(FONT_B, 30), _font(FONT, 22)
    head, row_label, cap_h = 70, 50, 30
    height = head + (1 + len(caps)) * (row_label + cap_h) + PHOTO_H + len(caps) * ROW_H + len(caps) * 28 + 70
    sheet = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    d.text((24, 18), "Tom Ford FT1119: same 3D shape (q001), two textures, in the AR try-on", fill=(20, 20, 20),
           font=f_title)
    y = head
    rows = [("your photos", (20, 20, 20), photos, PHOTO_H),
            ("now: Astra's texture", (150, 20, 20), tiles["original"], ROW_H)]
    if "before" in caps:
        rows.append((before_label or "painted, previous tool test (step 4)", (120, 90, 20), tiles["before"], ROW_H))
    rows.append((painted_label or ("painted from your photos (tool test)" if "before" not in caps else
                                   "painted, corrected: shading removed, level calibrated to the photos (tool test)"),
                 (20, 110, 30), tiles["painted"], ROW_H))
    for k in caps:
        if k.startswith("candidate: "):
            rows.append((k + " (tool test)", (30, 60, 140), tiles[k], ROW_H))
    for label, colour, row, h in rows:
        d.text((24, y + 8), label, fill=colour, font=f_row)
        y += row_label
        x = 24
        for im, cap in row:
            d.text((x + 4, y), cap, fill=(70, 70, 70), font=f_cap)
            sheet.paste(im, (x, y + cap_h))
            x += im.width + GAP
        y += cap_h + h + 28
    d.text((24, y), "AR rows: same poses, same crop. Tool test = the photo print attached to a COPY of q001; "
           "not a delivered model.", fill=(90, 90, 90), font=f_cap)
    sheet = sheet.crop((0, 0, width, y + 40))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    rec = {"sheet": str(out), "size": list(sheet.size), "crops": crops,
           "painted": str(painted), "original": str(original), "before": str(before) if before else None,
           "candidates": {k: str(v) for k, v in (candidates or {}).items()}}
    out.with_suffix(".json").write_text(json.dumps(rec, indent=1))
    return rec


def calibration_rows(path: Path) -> tuple[dict, str | None]:
    """({candidate label: capture dir}, delivered label) of an ar_calibrate calibration.json: every candidate that
    is not the delivered level, from its portrait re-capture when it has one."""
    cal = json.loads(Path(path).read_text(encoding="utf-8"))
    rows, delivered = {}, None
    for c in cal.get("candidates", {}).values():
        if c.get("delivered"):
            delivered = c.get("label")
            continue
        rows[c["label"]] = Path(c.get("sheet_capture") or c["capture"])
    return rows, delivered


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--painted", type=Path, default=TOOLTEST / "arview_painted")
    ap.add_argument("--original", type=Path, default=TOOLTEST / "arview_original")
    ap.add_argument("--out", type=Path, default=TOOLTEST / "owner_sheet.png")
    ap.add_argument("--before", type=Path, default=None, help="an earlier painted capture shown as a middle row")
    ap.add_argument("--candidate", action="append", default=[], help="LABEL=DIR: a candidate level's capture (a row)")
    ap.add_argument("--painted-label", default=None, help="the painted row's label")
    ap.add_argument("--before-label", default=None, help="the --before row's label")
    ap.add_argument("--calibration", type=Path, default=None,
                    help="ar_calibrate calibration.json: a row per non-delivered candidate level (portrait capture)")
    a = ap.parse_args(argv)
    cands = {}
    painted_label = a.painted_label
    if a.calibration:
        cands, delivered = calibration_rows(a.calibration)
        if painted_label is None and delivered:
            painted_label = f"painted, this round's level: {delivered} (tool test)"
    cands.update(dict(x.split("=", 1) for x in a.candidate))
    print(json.dumps(build(a.painted, a.original, a.out, a.before, cands, painted_label, a.before_label)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
