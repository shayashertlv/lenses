"""WP5 tool test (B4): a CLOSE-UP owner sheet, photo | Astra's texture | painted tool test, per region, so the print's
detail is visible (the companion of ``tooltest_owner_sheet``, from the same two arview captures).

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.tooltest_closeup_sheet [--painted DIR]
        [--original DIR] [--out PNG] [--width 1100]

Rows (sides by x sign only; q001's -x parts are Brow_Left / Temple_Left):
- front brows (both brow fronts with the endpiece blocks under the T) at the front pose, against photo 05 (taken 18 deg
  above the front pose);
- the same brows at pitch +20, photo 05's own angle (1.6 deg off), the like-for-like view;
- the -x temple at yaw +45 (-x side near), against photo 03 (the -x side, 28.8 deg off), the photo cut to the stretch
  of the temple the AR pose shows (the head hides its rear; ``ar_visible_z``).
AR tiles: the pose's skin beauty render cropped to the region's parts (the union of both captures' part-id boxes, so
Astra and painted share one crop), magnified LANCZOS to ``--width`` (arview never draws wider than 1280 px, so the
native render is the AR's detail ceiling; the magnification is printed under each tile). Photo tiles: the region's
parts rendered at the photo's WP3b camera (``ar_levels.photo_frame_mask``), boxed with a margin, magnified the same
way. Under every tile: L* darkest decile (p10) and median (p50) of the region's frame pixels (the ``frame`` form:
every pixel, highlights included; the instrument's ``lstar``).
The painted copy is a TOOL TEST (a print attached to a COPY of q001), never a delivered model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.tests.instruments import print_metrics as PM
from glasses3d.tests.photoprint import ar_levels as AL
from glasses3d.tests.photoprint.tooltest_owner_sheet import FONT, FONT_B, TOOLTEST, _font

BROWS = ("Brow_Left", "Brow_Right")
TEMPLE_NEG_X = ("Temple_Left",)
# (title, pose, photo, parts, note, stretch): stretch = the photo tile shows only the stretch of the parts the AR pose
# shows (``ar_visible_z``: at yaw +45 the head hides the temple's rear, which photo 03 shows whole)
ROWS = [("front brows (both brow fronts, endpiece blocks under the T)", "front", "05", BROWS,
         "front pose; photo 05 is 18 deg above it", False),
        ("brows at pitch +20 (photo 05's own angle, 1.6 deg off)", "pitch_p20", "05", BROWS,
         "pitch +20; like-for-like with photo 05", False),
        ("-x temple at yaw +45 (-x side near)", "yaw_p45", "03", TEMPLE_NEG_X,
         "yaw +45; photo 03 shows the -x side, 28.8 deg off", True)]
AR_MARGIN = 10
PHOTO_MARGIN = 0.04       # of the box's larger side


def _box(mask: np.ndarray, margin: int, size) -> tuple[int, int, int, int]:
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        raise SystemExit("region has no pixels")
    return (max(0, int(xs.min()) - margin), max(0, int(ys.min()) - margin),
            min(size[0], int(xs.max()) + 1 + margin), min(size[1], int(ys.max()) + 1 + margin))


def _lstats(px) -> dict:
    L = PM.lstar(np.asarray(px, np.float64).reshape(-1, 3))
    return {"n": int(L.size), "p10": round(float(np.percentile(L, 10)), 1), "p50": round(float(np.percentile(L, 50)), 1)}


def ar_region(cap: Path, pose: str, parts) -> tuple[np.ndarray, np.ndarray]:
    """(beauty RGB, region mask) of a capture's pose: the parts' part-id palette pixels (eroded 1 px)."""
    rep = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    pid = np.asarray(Image.open(cap / f"{pose}__part-id.png").convert("RGB"))
    beauty = np.asarray(Image.open(cap / f"{pose}__skin__beauty.png").convert("RGB"))
    return beauty, PM.ar_frame_mask(pid, rep["id_palette"], parts)


def ar_visible_z(cap: Path, pose: str, parts, job) -> tuple[float, float]:
    """(min, max) model z in mm of the parts' vertices whose projection at ``pose`` (the report's asset_to_world and
    camera projection; the job mesh is the GLB's in mm) lands on the parts' own part-id pixels: the stretch the AR
    shows."""
    rep = json.loads((cap / "report.json").read_text(encoding="utf-8"))
    p = next(q for q in rep["poses"] if q["id"] == pose)
    M = np.asarray(p["asset_to_world"], float).reshape(4, 4).T
    P = np.asarray(p["camera"]["projection"], float).reshape(4, 4).T
    W, H = p["camera"]["width"], p["camera"]["height"]
    pid = np.asarray(Image.open(cap / f"{pose}__part-id.png").convert("RGB"))
    m = PM.ar_frame_mask(pid, rep["id_palette"], parts)
    vi = np.unique(job.mesh.F[np.isin(job.mesh.part, job.part_ids(list(parts)))])
    V = job.mesh.V[vi]
    c = (P @ M @ np.c_[V / 1000.0, np.ones(len(V))].T).T
    x = np.clip(np.round((c[:, 0] / c[:, 3] + 1) / 2 * W).astype(int), 0, W - 1)
    y = np.clip(np.round((1 - c[:, 1] / c[:, 3]) / 2 * H).astype(int), 0, H - 1)
    hit = m[y, x]
    if not hit.any():
        raise SystemExit(f"{cap} {pose}: no {parts} vertex lands on its part-id pixels")
    return float(V[hit, 2].min()), float(V[hit, 2].max())


def photo_stretch_mask(job, view: str, occ, parts, z_range) -> np.ndarray:
    """``ar_levels.photo_frame_mask`` limited to the faces whose centroid z lies in ``z_range``."""
    from scipy import ndimage
    v = job.views[view]
    fid = occ.scene(job.mesh.V).render(v.camera, v.shape, 1)["face_id"]
    nf = len(job.mesh.F)
    ok = (fid >= 0) & (fid < nf)
    f = np.clip(fid, 0, nf - 1)
    cz = job.mesh.V[job.mesh.F[f], 2].mean(axis=-1)
    sel = ok & np.isin(job.mesh.part[f], job.part_ids(list(parts))) & (cz >= z_range[0]) & (cz <= z_range[1])
    sel = ndimage.binary_erosion(sel, iterations=AL.PHOTO_ERODE)
    return sel & ndimage.binary_erosion(np.asarray(v.matte, bool), iterations=AL.PHOTO_ERODE)


def _magnify(im: Image.Image, width: int) -> tuple[Image.Image, float]:
    s = width / im.width
    return im.resize((width, max(1, round(im.height * s))), Image.LANCZOS), s


def build(painted: Path, original: Path, out: Path, width: int = 1100, job=None) -> dict:
    job = job or IN.load_job()
    occ = AL._occ(job)
    rows, rec = [], {"sheet": str(out), "painted": str(painted), "original": str(original), "rows": []}
    for title, pose, photo, parts, note, stretch in ROWS:
        v = job.views[f"reference-{photo}.png"]
        z_range = None
        if stretch:
            zs = [ar_visible_z(c, pose, parts, job) for c in (original, painted)]
            z_range = (min(z[0] for z in zs), max(z[1] for z in zs))
            pm = photo_stretch_mask(job, f"reference-{photo}.png", occ, parts, z_range)
            note += f"; photo cut to the stretch the AR shows, z {z_range[1]:.0f} to {z_range[0]:.0f} mm"
        else:
            pm = AL.photo_frame_mask(job, f"reference-{photo}.png", occ, parts)
        ph, pw = pm.shape
        ys, xs = np.nonzero(pm)
        pbox = _box(pm, round(PHOTO_MARGIN * max(np.ptp(xs), np.ptp(ys))), (pw, ph))
        src = Image.fromarray(np.asarray(v.image, np.uint8))      # the reference photo's RGB, as the job reads it
        caps = {"Astra's texture": original, "painted (tool test)": painted}
        regions = {k: ar_region(c, pose, parts) for k, c in caps.items()}
        h0, w0 = next(iter(regions.values()))[1].shape
        abox = _box(np.logical_or.reduce([m for _b, m in regions.values()]), AR_MARGIN, (w0, h0))
        tiles = []
        im, s = _magnify(src.crop(pbox), width)
        st = _lstats(v.image[pm])
        tiles.append((im, f"photo {photo}", f"native {pbox[2] - pbox[0]}x{pbox[3] - pbox[1]} px, x{s:.1f};  "
                      f"L* p10 {st['p10']}  p50 {st['p50']}", st))
        for k, (beauty, m) in regions.items():
            im, s = _magnify(Image.fromarray(beauty).crop(abox), width)
            st = _lstats(beauty[m])
            tiles.append((im, k, f"native {abox[2] - abox[0]}x{abox[3] - abox[1]} px, x{s:.1f};  "
                          f"L* p10 {st['p10']}  p50 {st['p50']}", st))
        rows.append((title, note, tiles))
        rec["rows"].append({"title": title, "pose": pose, "photo": photo, "parts": list(parts),
                            "photo_box": list(pbox), "ar_box": list(abox),
                            "z_range_mm": [round(z, 1) for z in z_range] if z_range else None,
                            "lstar_frame": {name: st for _im, name, _c, st in tiles}})

    gap, pad, f_title, f_row, f_cap = 16, 24, _font(FONT_B, 34), _font(FONT_B, 28), _font(FONT, 22)
    sheet_w = pad * 2 + 3 * width + 2 * gap
    sheet_h = 80 + sum(50 + 32 + max(t[0].height for t in tiles) + 40 for _t, _n, tiles in rows) + 60
    sheet = Image.new("RGB", (sheet_w, sheet_h), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 20), "Tom Ford FT1119 close-up: your photo | Astra's texture | painted from your photos (tool test)",
           fill=(20, 20, 20), font=f_title)
    y = 80
    colours = {"Astra's texture": (150, 20, 20), "painted (tool test)": (20, 110, 30)}
    for title, note, tiles in rows:
        d.text((pad, y + 6), f"{title}   [{note}]", fill=(20, 20, 20), font=f_row)
        y += 50
        x = pad
        th = max(t[0].height for t in tiles)
        for im, name, cap, _st in tiles:
            d.text((x + 4, y), name, fill=colours.get(name, (20, 20, 20)), font=f_cap)
            sheet.paste(im, (x, y + 30))
            d.text((x + 4, y + 34 + im.height), cap, fill=(70, 70, 70), font=f_cap)
            x += width + gap
        y += 32 + th + 40
    d.text((pad, y), "AR tiles: arview capture --poses default (skin background), Astra and painted share one crop per "
           "row; magnified LANCZOS. L*: the region's frame pixels, highlights included. Tool test = the photo print "
           "attached to a COPY of q001; not a delivered model.", fill=(90, 90, 90), font=f_cap)
    sheet = sheet.crop((0, 0, sheet_w, y + 40))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    rec["size"] = list(sheet.size)
    out.with_suffix(".json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--painted", type=Path, default=TOOLTEST / "arview_painted")
    ap.add_argument("--original", type=Path, default=TOOLTEST / "arview_original")
    ap.add_argument("--out", type=Path, default=TOOLTEST / "closeup_sheet.png")
    ap.add_argument("--width", type=int, default=1100, help="tile width in pixels")
    a = ap.parse_args(argv)
    print(json.dumps(build(a.painted, a.original, a.out, a.width)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
