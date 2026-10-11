"""Owner sheet for a facet run's result (WP6_FACETS §5: judged on a sheet #1-style comparison). One PNG per photo:
PHOTO | ORIGINAL | earlier Astra (c0003, rejected 2026-10-10) | this run's best checkpoint | PREFERRED (v1 build), rows
native (white-balanced, shared stretch) and gloss_black (black-side: gloss_black only), all at the photo's fitted
camera and temple pose under the job rig. Gains, crops and stretches come from the original and are shared, as in
wp6v2_owner_sheet. Every input is sha-checked; .blend sources are opened as copies.

    python -m glasses3d.tests.proofs.wp6v2_result_sheet --blend <run checkpoint .blend> --sha <sha256> \
        --label "ASTRA c0013 (facet-astra-002)" --out glasses3d/data/wp6v2/result_facet-astra-002
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import time
from pathlib import Path

from PIL import Image, ImageDraw

from glasses3d.agent.seeing import Seeing
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host import pose as PO
from glasses3d.tests.proofs import wp6v2_owner_sheet as OS

QUESTION = "Do Astra's new temples (the 4th column) look like the photos?"


def sheet(rid: str, recs: dict, models: dict, title: str) -> Image.Image:
    rows = [(row, key) for row, key in (("native", "native_linear"), ("gloss_black", "gloss_black"))
            if all(key in recs[name]["rows"] for name in models)]
    cells = []
    for row, key in rows:
        cells.append(OS.label_cell(Image.open(recs["original"]["rows"]["photo"]), f"PHOTO {rid}"))
        for name, (label, _p, _s) in models.items():
            cells.append(OS.label_cell(Image.open(recs[name]["rows"][key]), f"{label}: {row}"))
    g = C.grid(cells, len(models) + 1, max_px=5200, gap=8)
    head = Image.new("RGBA", (g.width, 34), (255, 255, 255, 255))
    ImageDraw.Draw(head).text((8, 6), title, fill=(10, 10, 10, 255), font=C._font(18))
    out = Image.new("RGB", (g.width, g.height + head.height), (255, 255, 255))
    out.paste(head.convert("RGB"), (0, 0))
    out.paste(g.convert("RGB"), (0, head.height))
    return out


def main(blend: Path, want: str, label: str, out: Path, rig: str = "studio_dome_v1") -> dict:
    models = {"original": OS.MODELS["original"], "c0003": ("EARLIER ASTRA c0003",) + OS.MODELS["c0003"][1:],
              "result": (label, Path(blend), want), "preferred": OS.MODELS["preferred"]}
    t0 = time.perf_counter()
    for name, (_l, path, sha) in models.items():
        got = OS.sha(path)
        if got != sha:
            raise RuntimeError(f"{name}: {path} sha {got[:12]} != {sha[:12]}")
    OS.OUT = Path(out)
    if OS.OUT.exists():
        shutil.rmtree(OS.OUT, onerror=OS._rw)
    OS.OUT.mkdir(parents=True)
    doc = json.loads(OS.BASIS_JSON.read_text(encoding="utf-8"))
    photos = {n: Path(r["image"]) for n, r in doc["images"].items()}
    jobs, reports = {}, {}
    try:
        for name, (_l, path, _w) in models.items():
            jobs[name] = OS.open_job(name, path, photos)
        boxes = {}
        for name, (_s, see) in jobs.items():
            bs = C.load_basis(see.root)
            exp = see.export_head()
            mesh = see.fit_mesh(exp["path"], exp["sha256"], skip=bs.skip, frame=bs.frame)
            roles = PO.part_roles(exp["path"])
            for rid in OS.PHOTOS:
                g = Seeing._matched_geometry(bs, rid, mesh, roles)
                boxes.setdefault(rid, []).append(C.crop_box(g["temple"], bs.matte(rid)))
        crops = {}
        for rid, bl in boxes.items():
            box = OS.union_box(bl)
            crops[rid] = {"box": list(box), "k": min(1.0, OS.CELL_MAX_PX / max(box[2] - box[0], box[3] - box[1]))}
        r = jobs["original"][1].compare_to_photos(list(OS.PHOTOS), passes=("native_linear", "gloss_black"),
                                                  rig=rig, boxes=crops)
        assert r["status"] == "ok", r
        reports["original"] = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
        gains, env = reports["original"]["gains"], reports["original"]["environment"]
        for rid in OS.PHOTOS:
            crops[rid]["stretch"] = reports["original"]["photos"][rid]["stretch"]
        for name in models:
            if name == "original":
                continue
            r = jobs[name][1].compare_to_photos(list(OS.PHOTOS), passes=("native_linear", "gloss_black"),
                                                rig=rig, boxes=crops, gains=gains, env=env)
            assert r["status"] == "ok", r
            reports[name] = json.loads(Path(r["report"]).read_text(encoding="utf-8"))
        for name, (s, _see) in jobs.items():
            assert s.fingerprint() == s.head["fingerprint"], f"{name}: the compare moved the scene"
    finally:
        for s, _see in jobs.values():
            s.close()
    pages = []
    for rid in OS.PHOTOS:
        recs = {name: reports[name]["photos"][rid] for name in models}
        o = recs["original"]
        title = f"{rid} ({o['view_kind']}, {o['colourway']}), light C (studio_dome_v1)"
        p = OS.OUT / f"{rid}.png"
        sheet(rid, recs, models, title).save(p)
        pages.append({"photo": rid, "png": p.name,
                      "temple_iou": {n: round(recs[n]["temple_iou"], 3) for n in models},
                      "outer_edge_offsets_mm": {n: recs[n]["outer_edge_offsets_mm"] for n in models}})
    summary = {"question": QUESTION, "created": C._utc(), "rig": rig,
               "inputs": {n: {"label": l, "path": str(pth), "sha256": w} for n, (l, pth, w) in models.items()},
               "basis": {"path": str(OS.BASIS_JSON), "sha256": OS.sha(OS.BASIS_JSON)}, "crops": crops,
               "pages": pages, "seconds": round(time.perf_counter() - t0, 1)}
    C.write_json(OS.OUT / "summary.json", summary)
    cols = " | ".join(html.escape(l) for l, _p, _s in models.values())
    body = "\n".join(f'<h3>{html.escape(pg["photo"])}</h3><img src="{pg["png"]}" style="max-width:100%">' for pg in pages)
    (OS.OUT / "index.html").write_text(
        f"<!doctype html><meta charset='utf-8'><title>Astra facet result</title><body style='font-family:sans-serif'>"
        f"<h2>{html.escape(QUESTION)}</h2><p>Columns: PHOTO | {cols}. Same camera, temple pose and light C for "
        f"every model; brightness and contrast are matched to each photo. The lettering, lenses and front are the "
        f"original's in every column; only the temples differ.</p>{body}</body>", encoding="utf-8")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--blend", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    s = main(Path(a.blend), a.sha, a.label, Path(a.out))
    print(json.dumps({"out": str(OS.OUT), "seconds": s["seconds"], "pages": [p["png"] for p in s["pages"]]}, indent=2))
