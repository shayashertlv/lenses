"""WP5 build step 3 $0 proof on FT1119, host side (WP5_DESIGN §8 A1-A5, A7, B3), single-source mode.

    glasses3d/.venv/Scripts/python -B -m glasses3d.tests.photoprint.print_proof [--out DIR] [--tx 16] [--workers N]
        [--skip-b3] [--skip-a7]        (--workers: default os.cpu_count() - 2)

Writes DIR/<geom_sha8>/ (default DIR = data/wp5/ft1119_print): the production bake (print.png, classes.png,
source.png, tier.png, layout.json, registration.json, report.json), ``proof.json`` (A1 from the layout, A2 per view,
A3 read back from print.png against independently sampled photo colours (the bake's own A3 beside it), A4 per part,
A5 off the seam, A7 serial-vs-pool sha256, B3 held-out rows per view and region, the single-source audit, and the
**STOP B verdict** of WP5_DESIGN §9) and ``sheets/overview.png`` (photo | render of the bake at each photo, then
AR-like novel views on S). Every A2/A3/A4/A5/B3 number is the independent instrument's
(tests/instruments/print_metrics.py, via tests/photoprint/printproof.py). A line is printed at least every 30 s
(``Progress.heartbeat`` over the whole run; the A7 serial bake prints too).

STOP B (§9) fires if the full bake loses a C1-C4 margin on a region that passed A (none passed A on FT1119), or if
A1-A6 or B3 fails; A6 and B5 are not built by this workflow (Blender side), so they count as not passed. When it fires,
B4 (the owner's arview review) waits for the owner's decision or a recorded engineering decision.

Fix round 2: the single-source audit is also made from the WRITTEN outputs (``printproof.audit_outputs``: every photo
texel's view is its region's anchor or an adjacent region's anchor, one view per face); A4 holes are drawn by the proof
(seeded, spread over each part); A5 carries a breakdown by side kinds; ``decisions`` lists the engineering decisions
this build rests on that PROGRESS.md does not record yet (this workflow may not edit it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from glasses3d.g3d_host import raster
from glasses3d.g3d_host import texture_utils as T
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import project as PR
from glasses3d.g3d_host.photoprint.compose import render_bake
from glasses3d.g3d_host.photoprint.procs import Progress, opt_out_ecoqos, pool_map
from glasses3d.g3d_host.photoprint.report import jsonable, write_json
from glasses3d.tests.photoprint import printproof as PP

OUT = IN.REPO / "glasses3d" / "data" / "wp5" / "ft1119_print"
STEP0 = IN.REPO / "glasses3d" / "data" / "wp5" / "ft1119_strip_null" / "report.json"     # the current step 0 run
A0_JSON = IN.REPO / "glasses3d" / "data" / "wp5" / "a0" / "a0.json"
TH = dict(print_reg_mm=0.8, edge_mean_mm=1.0, edge_p90_mm=2.5)


def geom_sha8(job) -> str:
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(job.mesh.V).tobytes())
    h.update(np.ascontiguousarray(job.mesh.F).tobytes())
    return h.hexdigest()[:8]


def _b3_job(view, tx, radii):
    opt_out_ecoqos()
    job = IN.load_job()
    return PP.heldout_b3(job, IN.FT1119_PARTS, view, radii_mm=radii, tx=tx)


def _a2_job(view, tx):
    opt_out_ecoqos()
    job = IN.load_job()
    return PP.round_trip(job, view, tx, IN.FT1119_PARTS)


def overview(job, res, path: Path) -> str:
    lay, atlas, sc = res["layout"], res["atlas"].astype(float), res["occ"].scene(job.mesh.V)
    rows = []
    for n in sorted(job.views):
        v = job.views[n]
        r = render_bake(job, lay, atlas, n, v.camera, scene=sc)
        img = v.image.astype(float)
        ren = img.copy()
        ren[r["mask"]] = r["ren"][r["mask"]]
        ys, xs = np.nonzero(r["mask"])
        y0, y1, x0, x1 = max(ys.min() - 5, 0), ys.max() + 5, max(xs.min() - 5, 0), xs.max() + 5
        pair = np.concatenate([img[y0:y1, x0:x1], ren[y0:y1, x0:x1]], 1)
        im = Image.fromarray(np.clip(pair, 0, 255).astype(np.uint8))
        rows.append(np.asarray(im.resize((1200, max(1, int(im.height * 1200 / im.width))))))
    lrow = lay.face_index(len(job.mesh.F))
    for yaw, pitch in ((0, 0), (30, 10), (-30, -10), (45, 0), (-45, 20)):
        cam = raster.view_camera(job.frame, yaw, pitch, 6.0, (600, 1200))
        rr = sc.render(cam, (600, 1200), 1, want_bary=True)
        fid = rr["face_id"]
        m = (fid >= 0) & (fid < len(lrow))
        row = np.full(fid.shape, -1)
        row[m] = lrow[fid[m]]
        m &= row >= 0
        ii, jj = np.nonzero(m)
        b = rr["bary"][ii, jj].astype(float)
        uv = np.einsum("kj,kjd->kd", np.stack([1 - b[:, 0] - b[:, 1], b[:, 0], b[:, 1]], 1), lay.uv[row[ii, jj]])
        img = np.full((600, 1200, 3), 235.0)
        img[(fid >= 0) & ~m] = 170.0
        img[ii, jj] = T.sample_bilinear(atlas.astype(np.float32), uv[:, 0] - 0.5, uv[:, 1] - 0.5)
        rows.append(img)
    W = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 4), (0, W - r.shape[1]), (0, 0)), constant_values=255) for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    big = Image.fromarray(np.clip(np.concatenate(rows, 0), 0, 255).astype(np.uint8))
    k = min(1.0, 1600 / big.width, 4000 / big.height)
    big.resize((int(big.width * k), int(big.height * k))).save(path)
    return str(path)


def stop_b(proof: dict) -> dict:
    """WP5_DESIGN §9 STOP B from the proof's own rows (see the module docstring)."""
    reasons = []
    if not proof["A1"].get("pass"):
        reasons.append("A1 fails")
    a2 = [v for v, r in proof.get("A2", {}).items() if not r.get("pass")]
    if a2:
        reasons.append(f"A2 fails at {a2}")
    if not proof["A3_readback"].get("pass"):
        reasons.append(f"A3 (read back) fails: {proof['A3_readback'].get('criteria')}")
    a4 = {p: r["a4"] for p, r in proof["A4"].items() if not r["a4"].get("pass")}
    for p, r in a4.items():
        reasons.append(f"A4 fails on {p}: median |log energy| {[round(x, 3) for x in r.get('median_log_energy', [])]}, "
                       f"mean-colour dE00 {r.get('median_mean_colour_de00', float('nan')):.2f} (limit "
                       f"{r.get('mean_colour_limit', float('nan')):.2f}), seam "
                       f"{r.get('median_seam_ratio') or float('nan'):.2f} over {r.get('holes')} holes "
                       f"(criteria {r.get('criteria')})")
    if not proof.get("audit_outputs", {}).get("pass", True):
        reasons.append(f"single-source audit from the outputs fails: {proof['audit_outputs']['outside_own_or_adjacent_anchor']} "
                       f"texels outside their own / adjacent anchor, {proof['audit_outputs']['faces_with_two_views']} "
                       f"faces with two views")
    if not proof["A5"].get("pass"):
        reasons.append(f"A5 fails: {proof['A5'].get('criteria')}")
    # correction round (step-4 review finding): A6 / B5 are built and graded by print_tooltest.py on the GLB
    # attach_print paints from THIS print (tooltest.json, matched by the print's sha256); a bake run cannot grade them
    tt = proof.get("tooltest")
    for key, what in (("A6", "Blender round trip"), ("B5", "material control")):
        if tt is None:
            reasons.append(f"{key} ({what}) not run for this print (print_tooltest.py grades it on the attached GLB)")
        elif not tt.get(key):
            reasons.append(f"{key} ({what}) fails on the tool-test GLB of this print")
    a7 = proof.get("A7")
    if a7 is None or a7.get("pass") is None:
        reasons.append("A7 not run")
    elif not a7.get("pass"):
        reasons.append("A7 fails")
    if "B3_summary" in proof:
        b = proof["B3_summary"]
        if b["passing"] < b["eligible_rows"]:
            reasons.append(f"B3 fails: {b['eligible_rows'] - b['passing']} of {b['eligible_rows']} eligible held-out rows "
                           f"fail")
        if b["eligible_rows"] == 0:
            reasons.append("B3: no eligible row")
    else:
        reasons.append("B3 not run")
    return {"fired": bool(reasons), "reasons": reasons,
            "rule": "WP5_DESIGN §9: STOP B fires if the full bake loses any C1-C4 margin on a region that passed A, or "
                    "if A1-A6 or B3 fails",
            "regions_that_passed_A": [],
            "next": "B4 (owner's arview review) waits: the owner decides (or an engineering decision is recorded in "
                    "PROGRESS) whether single-source print goes to B4 with these failures"}


DECISIONS = [
    "A0 is graded by the literal §8 rule (residual <= 0.2 mm at every view): A0 FAILS on q001 (three thin grazing views "
    "outside the fit, 0.24-0.33 mm; about 1 mm of depth error on the two temple regions no fitted view sees). The "
    "known-limit exemption of fix round 1 is reported only; it needs an owner waiver or a PROGRESS decision to count.",
    "Label boundaries are re-placed (label_moves='crease'): creases where they exist, else the line where the two "
    "adjacent anchors' gained photos agree best; only onto an ADJACENT region's anchor that sees the face, <= 3 mm; one "
    "view per face. The brows carry no crease near their boundaries (15-20 deg tessellation steps), so their seams "
    "cannot sit on creases.",
    "The < 30 % cover rule measures the painting views' SEEN texels (before text / highlight masks) over the AR-visible "
    "area a worn frame shows (temple inner and bottom faces excluded) and judges mirror partners together.",
    "A4: 8 seeded holes per part drawn by the proof over every single-view area; the mean-colour limit is "
    "max(2.0, the median donor-ring baseline) (a fill can only predict a hole's colour from its surroundings).",
    "A5 (fix round 1, recorded here): graded off the seam, p95 across <= max(3, within-chart p95) at 1 and 2 texels; "
    "the design's same-3D-point reading passes by construction (the harmonisation writes those texels).",
]


def audit(res: dict) -> dict:
    """Single-source audit from the bake's report: non-anchor share per region (texels painted by an ADJACENT region's
    anchor after the boundary moves), foreign-view texels (any other view: must be 0), and whether an A0 thin-view
    row's view paints its region."""
    st = res["report"]["statuses"]
    rows = {k: {"anchor": v["anchor"], "photo_texels": v.get("photo_texels"), "non_anchor_share": v.get("non_anchor_share"),
                "painted_by": v.get("painted_by"), "allowed_views": v.get("allowed_views"),
                "foreign_view_texels": v.get("foreign_view_texels")} for k, v in st.items()}
    out = {"label_moves": res["report"]["checks"]["labels"]["label_moves"],
           "non_anchor_photo_share": res["report"]["checks"]["labels"]["non_anchor_photo_share"],
           "foreign_view_photo_texels": res["report"]["checks"]["labels"].get("foreign_view_photo_texels"),
           "max_region_non_anchor_share": max((v.get("non_anchor_share") or 0.0) for v in st.values()), "regions": rows}
    if A0_JSON.is_file():
        a0 = json.loads(A0_JSON.read_text(encoding="utf-8"))
        kl = (a0.get("known_limits") or {}).get("rows") or []
        out["a0_known_limit_views_painting"] = [
            {"region": r["region"], "view": r["view"], "painted_texels": (st.get(r["region"], {}).get("painted_by") or
                                                                          {}).get(r["view"], 0)} for r in kl]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--tx", type=float, default=None, help="texels per mm (default: 2 x the donors' max px/mm)")
    ap.add_argument("--workers", type=int, default=None,
                    help="process pool size for the bake, A2 and B3 (default os.cpu_count() - 2, never more than the jobs)")
    ap.add_argument("--skip-b3", action="store_true")
    ap.add_argument("--skip-a7", action="store_true")
    ap.add_argument("--step0", default=str(STEP0),
                    help="step 0 report.json (its 'registered' regions are mixed in tier 1; statuses reported; its "
                         "sha256 is recorded in report.json)")
    a = ap.parse_args(argv)
    opt_out_ecoqos()
    prog = Progress("print_proof")
    with prog.heartbeat():
        return _main(a, prog)


def _main(a, prog) -> int:
    t0 = time.perf_counter()
    job = IN.load_job()
    root = Path(a.out) / geom_sha8(job)
    prog(f"production bake -> {root}", force=True)
    step0 = a.step0 if a.step0 and Path(a.step0).is_file() else None
    res = PR.project_photo_texture(job, IN.FT1119_PARTS, out_dir=root, tx_per_mm=a.tx, workers=a.workers,
                                   step0_report=step0, **TH)
    tx = float(res["layout"].tx_per_mm)
    proof = {"out": str(root), "tx_per_mm": tx, "step0_report": res["report"]["inputs"]["step0_report"],
             "atlas": [res["layout"].width, res["layout"].height], "mode": res["report"]["mode"],
             "texture_parts": res["report"]["texture_parts"], "below_min_cover": res["report"]["below_min_cover"],
             "A1": res["report"]["checks"]["A1"],
             "A3_tool_own": {k: v for k, v in res["report"]["checks"]["A3"].items() if k != "calls"}
             | {"calls": len(res["report"]["checks"]["A3"]["calls"]),
                "note": "the tool's own check (protected == P0) is true by construction; A3 is A3_readback"}}
    proof["decisions"] = DECISIONS
    proof["audit"] = audit(res)
    proof["audit_outputs"] = PP.audit_outputs(job, res, root)
    prog(f"audit: non-anchor photo share {proof['audit']['non_anchor_photo_share']:.4f} (adjacent anchors), foreign "
         f"views {proof['audit']['foreign_view_photo_texels']}; from the outputs: outside own/adjacent anchor "
         f"{proof['audit_outputs']['outside_own_or_adjacent_anchor']}, faces with two views "
         f"{proof['audit_outputs']['faces_with_two_views']}", force=True)
    proof["A3_readback"] = PP.readback_a3(job, res, root)
    prog(f"A3 read back {proof['A3_readback']}", force=True)
    proof["A5"] = PP.seams(job, res, root)
    prog(f"A5 {json.dumps(proof['A5']['offsets'])} pass {proof['A5']['pass']}", force=True)
    proof["A4"] = {p: PP.knockout(res, p) for p in IN.FT1119_PARTS}
    prog("A4 " + json.dumps({p: r["a4"].get("pass") for p, r in proof["A4"].items()}), force=True)
    proof["sheet"] = overview(job, res, root / "sheets" / "overview.png")
    views = sorted(job.views)
    proof["A2"] = dict(zip(views, pool_map(_a2_job, [(v, tx) for v in views], a.workers)))
    prog("A2 " + json.dumps({v[-6:-4]: r["pass"] for v, r in proof["A2"].items()}), force=True)
    if not a.skip_a7:
        prog("A7: serial bake (workers=1; progress lines from the bake)", force=True)
        r2 = PR.project_photo_texture(job, IN.FT1119_PARTS, out_dir=root / "a7_serial", tx_per_mm=tx, workers=1,
                                      radii_mm=None, log=print, step0_report=step0, **TH)
        names = ("print.png", "classes.png", "source.png", "tier.png", "registration.json", "report_stable.json")
        sha = {n: (hashlib.sha256((root / n).read_bytes()).hexdigest(),
                   hashlib.sha256((root / "a7_serial" / n).read_bytes()).hexdigest()) for n in names}
        proof["A7"] = {"files": {n: {"pool": x, "serial": y, "equal": x == y} for n, (x, y) in sha.items()},
                       "pass": all(x == y for x, y in sha.values())}
        del r2
        prog(f"A7 {proof['A7']['pass']}", force=True)
    if not a.skip_b3:
        rows = pool_map(_b3_job, [(v, tx, res["radii"]) for v in views], a.workers)
        proof["B3"] = {r["held_out"]: r["rows"] for r in rows}
        elig = [x for r in rows for x in r["rows"] if x["eligible"]]
        proof["B3_summary"] = {"eligible_rows": len(elig), "passing": sum(1 for x in elig if x["pass"]),
                               "rows": [{"held_out": x["held_out"], "region": x["region"], "pixels": x["pixels"],
                                         "projected_share": x["projected_share"], "criteria": x["criteria"],
                                         "spectrum_error": x["projection"]["spectrum_error"],
                                         "corr_len_ratio": x["projection"]["corr_len_ratio"],
                                         "decile_de00_raw": x["projection"]["decile_de00_raw"],
                                         "astra_decile_de00_raw": x["astra"]["decile_de00_raw"],
                                         "energy_0.4_1.6mm": x["projection"]["energy_0.4_1.6mm"],
                                         "energy_1_4px": x["projection"]["energy_1_4px"]} for x in elig]}
        prog(f"B3 {proof['B3_summary']['passing']} / {proof['B3_summary']['eligible_rows']} eligible rows pass",
             force=True)
    proof["STOP_B"] = stop_b(proof)
    prog(("STOP B FIRED: " + " | ".join(proof["STOP_B"]["reasons"])) if proof["STOP_B"]["fired"] else "STOP B not fired",
         force=True)
    proof["seconds"] = round(time.perf_counter() - t0, 1)
    write_json(root / "proof.json", jsonable(proof))
    print(json.dumps({k: proof[k] for k in ("out", "tx_per_mm", "atlas")} | {"STOP_B_fired": proof["STOP_B"]["fired"]}),
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
