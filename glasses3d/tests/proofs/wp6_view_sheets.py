"""WP6 comparison sheets (TOOL TEST, $0): photo | tool-test temples | delivered, one sheet per view family (side 05 + 01,
top 03, rear 06, angled 02). Each model is drawn at ITS OWN refit camera and theta (the cameras the gates grade it at):
row 1 is a Lambert-shaded render of the whole scene geometry (the delivered front + that model's temples, temples tinted
so facets read as shade steps), row 2 the silhouette overlay on the photo (photo matte edge green, model edge magenta).
Every sheet carries a red 'tool test (not a delivered model)' banner. Reads only pickles a fit run wrote; renders with
the instruments' own rasteriser (relief_views.render_model), so what the sheet shows is what the gates measured.

    glasses3d/.venv/Scripts/python -u -B -m glasses3d.tests.proofs.wp6_view_sheets [--run-dir DIR] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

G3D = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(G3D.parent))

from glasses3d.g3d_host import raster  # noqa: E402
from glasses3d.g3d_host import relief_rules as RR  # noqa: E402
from glasses3d.g3d_host import relief_views as RV  # noqa: E402
from glasses3d.tests.proofs import wp6_prada_stages as PS  # noqa: E402

BANNER = "tool test (not a delivered model)"
SHEETS = {"side": ("05", "01"), "top": ("03",), "rear": ("06",), "angled": ("02",)}
MAX_TILE_H = 560                            # a tall close-up is scaled to this height, not to the column width
ZOOM = {"05": (1,), "01": (-1,), "02": (1,), "06": (1, -1)}   # temple close-ups (the near temple in 05 / 01)
# camera-relative key light (right, up, toward camera) from the upper left, plus a weak fill from the right: facets
# with different normals land on different shades in every view
KEY = np.array([-0.35, 0.80, 0.48])
FILL = np.array([0.6, -0.1, 0.8])
TEMPLE_RGB = np.array([238.0, 226.0, 196.0])          # cream (the acetate's colour family)
FRONT_RGB = np.array([200.0, 200.0, 204.0])           # neutral grey: the shared delivered front
CREASE_LINE_DEG = RR.MODEL_CREASE_DEG                  # a model crease (6 deg, the frozen rule), adjacent px
CREASE_RGB = np.array([150.0, 70.0, 20.0])


def shaded(job, model, key, cam, theta) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Lambert render of the delivered front + the model's temples at (cam, theta): (RGB uint8, hit mask, tag). Dark lines
    mark the model's own creases: adjacent temple pixels whose surface normals differ by >= CREASE_LINE_DEG."""
    r = RV.render_model(job, model, key, cam, theta, points=False, normals=True)
    Rr, Ru, Rt = raster.camera_basis(cam)
    n = r["normal"].copy()
    n[(n @ Rt) < 0] *= -1
    k = KEY / np.linalg.norm(KEY)
    f = FILL / np.linalg.norm(FILL)
    lk = k[0] * Rr + k[1] * Ru + k[2] * Rt
    lf = f[0] * Rr + f[1] * Ru + f[2] * Rt
    s = 0.42 + 0.62 * np.clip(n @ lk, 0, 1) + 0.10 * np.clip(n @ lf, 0, 1)
    s = np.clip(s, 0, 1.05)[..., None]
    tag = r["tag"]
    temple = np.isin(tag, (1, -1))
    rgb = np.where(temple[..., None], TEMPLE_RGB, FRONT_RGB) * s
    img = np.where(r["mask"][..., None], rgb, 255.0)
    # model creases: normal change between 4-neighbours on the same temple
    cos_t = np.cos(np.radians(CREASE_LINE_DEG))
    line = np.zeros(tag.shape, bool)
    for dy, dx in ((0, 1), (1, 0)):
        a = n[: n.shape[0] - dy, : n.shape[1] - dx]
        b = n[dy:, dx:]
        ta, tb = tag[: tag.shape[0] - dy, : tag.shape[1] - dx], tag[dy:, dx:]
        same = (ta == tb) & np.isin(ta, (1, -1))
        c = np.sum(a * b, axis=-1) / np.maximum(np.linalg.norm(a, axis=-1) * np.linalg.norm(b, axis=-1), 1e-12)
        e = same & (c < cos_t)
        line[: tag.shape[0] - dy, : tag.shape[1] - dx] |= e
    line = cv2.dilate(line.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & temple
    img[line] = CREASE_RGB
    return np.clip(img, 0, 255).astype(np.uint8), r["mask"], tag


def _put(img, text, org, scale=0.6, color=(0, 0, 0), thick=1):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def _block(job, k, rend, models, fits, box, width, zoom=None):
    """Two rows (shaded renders; silhouette overlays) for one crop box of view k, the photo in column 0."""
    x0, y0, x1, y1 = box
    sc = min(width / max(x1 - x0, 1), MAX_TILE_H / max(y1 - y0, 1))
    tw, h = int(round(sc * (x1 - x0))), int(round(sc * (y1 - y0)))
    h = max(h, 200)

    def rs(t):
        im = cv2.resize(np.ascontiguousarray(t[y0:y1, x0:x1]), (tw, int(round(sc * (y1 - y0)))),
                        interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
        out = np.full((h, width, 3), 255, np.uint8)
        out[:im.shape[0], :im.shape[1]] = im
        return out
    row1 = [rs(job.photo(k))] + [rs(img) for img, _, _ in rend.values()]
    row2 = [np.full((h, width, 3), 255, np.uint8)] + [rs(RV.overlay(job, k, msk)) for _, msk, _ in rend.values()]
    zt = "" if zoom is None else f" - zoom on the {'+' if zoom > 0 else '-'}x temple"
    _put(row1[0], f"photo {k}{zt}", (8, 24), 0.7 if zoom is None else 0.6)
    _put(row2[0], f"photo {k}: silhouette overlays ->", (8, 30), 0.6)
    _put(row2[0], "green = photo matte edge", (8, 60), 0.6, (0, 150, 0))
    _put(row2[0], "magenta = model edge", (8, 88), 0.6, (200, 0, 200))
    _put(row2[0], "each model at its own refit camera", (8, 116), 0.55)
    if h > 190:
        _put(row2[0], "row above: shaded geometry; brown lines =", (8, 150), 0.55, (150, 70, 20))
        _put(row2[0], f"model normal change >= {CREASE_LINE_DEG:g} deg (relief_rules)", (8, 174), 0.55,
             (150, 70, 20))
    for i, lab in enumerate(models):
        _put(row1[i + 1], f"{lab} - shaded, {k}{zt}", (8, 24), 0.55)
        th = fits[lab][k]["theta"]
        _put(row1[i + 1], f"theta +x {th.get(1, 0.0):.2f}  -x {th.get(-1, 0.0):.2f} deg", (8, h - 12), 0.5,
             (90, 90, 90))
    sep = np.full((h, 4, 3), 255, np.uint8)
    join = lambda tiles: np.hstack([t for tile in tiles for t in (tile, sep)][:-1])
    return np.vstack([join(row1), np.full((4, join(row1).shape[1], 3), 255, np.uint8), join(row2)])


def view_sheet(job, models: dict, fits: dict, keys, path: Path, title: str, width: int = 640) -> Path:
    """models {column label: Model}, fits {column label: {key: {camera, theta}}}; per view a block of two rows on the
    whole frame, then (ZOOM) one block per listed temple side cropped to that temple."""
    blocks = []
    for k in keys:
        rend = {lab: shaded(job, m, k, fits[lab][k]["camera"], fits[lab][k]["theta"]) for lab, m in models.items()}
        union = job.matte(k).copy()
        for _, msk, _ in rend.values():
            union |= msk
        blocks.append(_block(job, k, rend, models, fits, RV._crop_box(union, pad=24), width))
        for side in ZOOM.get(k, ()):
            tm = np.zeros(union.shape, bool)
            for _, _, tag in rend.values():
                tm |= tag == side
            if tm.any():
                blocks.append(_block(job, k, rend, models, fits, RV._crop_box(tm, pad=30), width, zoom=side))
    W = max(b.shape[1] for b in blocks)
    body = np.vstack([np.pad(b, ((0, 10), (0, W - b.shape[1]), (0, 0)), constant_values=255) for b in blocks])
    head = np.full((44, W, 3), 255, np.uint8)
    labels = ["photo (real)"] + list(models)
    for i, lab in enumerate(labels):
        _put(head, lab[:60], (10 + i * (width + 4), 30), 0.7)
    banner = np.zeros((40, W, 3), np.uint8)
    banner[:] = (180, 0, 0)
    _put(banner, f"{title} - {BANNER}", (10, 27), 0.7, (255, 255, 255), 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.vstack([banner, head, body])).save(path)
    return path


def run(run_dir: Path, out_dir: Path, delivered_fits: Path, title: str) -> dict:
    job = RV.Job("prada")
    cand = pickle.loads((run_dir / "fit_candidate.pkl").read_bytes())
    fits_d = pickle.loads(delivered_fits.read_bytes())
    cc = PS._compare_cache(cand, "candidate")
    C, D = "tool-test temples (candidate)", "delivered GLB"
    models = {C: RV.Model.from_spec(job, cand["spec"], C), D: RV.Model.delivered(job)}
    fits = {C: {k: {"camera": cc[k]["camera"], "theta": cc[k]["theta"]} for k in RV.VIEW_ORDER},
            D: {k: {"camera": fits_d[k]["camera"], "theta": fits_d[k]["theta"]} for k in RV.VIEW_ORDER}}
    out = {}
    for name, keys in SHEETS.items():
        p = out_dir / f"sheet_{name}_{'_'.join(keys)}_TOOL_TEST.png"
        view_sheet(job, models, fits, keys, p, f"{title} | {name} ({', '.join(keys)})")
        out[name] = str(p)
        print(f"sheet {name}: {p}", flush=True)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default=str(G3D / "data" / "wp6" / "diagnostic_resume"))
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--delivered-fits", default=str(G3D / "data" / "wp6" / "proof" / "fits_delivered.pkl"))
    ap.add_argument("--title", default="WP6 Prada DIAGNOSTIC past STOP-I, scene copy")
    a = ap.parse_args(argv)
    rd = Path(a.run_dir)
    run(rd, Path(a.out_dir) if a.out_dir else rd / "sheets", Path(a.delivered_fits), a.title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
