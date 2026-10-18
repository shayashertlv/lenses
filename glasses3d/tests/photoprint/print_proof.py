"""WP5 build step 3 $0 proof on FT1119, host side (WP5_DESIGN §8 A1-A5, A7, B3), single-source mode: the helpers
``bake_ft1119.py`` imports (``TH``, ``STEP0``, ``overview``, ``stop_b``, ``DECISIONS``, ``audit``, ``_a2_job``).

The step-3 driver itself (``main``, which wrote DIR/<geom_sha8>/ with ``proof.json`` and ``sheets/overview.png``; its
evidence is data/wp5/ft1119_print/43aea1bb) was superseded by ``bake_ft1119.py`` and deleted on 2026-10-13; see git
tag review-20261013-pre-cleanup. ``overview`` draws photo | render of the bake at each photo, then AR-like novel views
on S. Every A2/A3/A4/A5/B3 number is the independent instrument's (tests/instruments/print_metrics.py, via
tests/photoprint/printproof.py).

STOP B (§9) fires if the full bake loses a C1-C4 margin on a region that passed A (none passed A on FT1119), or if
A1-A6 or B3 fails; A6 and B5 are not built by this workflow (Blender side), so they count as not passed. When it fires,
B4 (the owner's arview review) waits for the owner's decision or a recorded engineering decision.

Fix round 2: the single-source audit is also made from the WRITTEN outputs (``printproof.audit_outputs``: every photo
texel's view is its region's anchor or an adjacent region's anchor, one view per face); A4 holes are drawn by the proof
(seeded, spread over each part); A5 carries a breakdown by side kinds; ``decisions`` lists the engineering decisions
this build rests on that PROGRESS.md does not record yet (this workflow may not edit it).
"""
from __future__ import annotations

from glasses3d.tests.fixtures.legacy_ft1119 import PRINT_PARTS as FT1119_PARTS

import json
from pathlib import Path

import numpy as np
from PIL import Image

from glasses3d.g3d_host import raster
from glasses3d.g3d_host import texture_utils as T
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.tests.fixtures import legacy_ft1119
from glasses3d.g3d_host.photoprint.compose import render_bake
from glasses3d.g3d_host.photoprint.procs import opt_out_ecoqos
from glasses3d.tests.photoprint import printproof as PP

STEP0 = IN.REPO / "glasses3d" / "data" / "wp5" / "ft1119_strip_null" / "report.json"     # the current step 0 run
A0_JSON = IN.REPO / "glasses3d" / "data" / "wp5" / "a0" / "a0.json"
from glasses3d.g3d_host.photoprint.production import THRESHOLDS
TH = dict(THRESHOLDS)


def _a2_job(view, tx):
    opt_out_ecoqos()
    job = legacy_ft1119.load_job()
    return PP.round_trip(job, view, tx, FT1119_PARTS)


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
    "A5 (current frozen protocol): graded off the seam, p95 across <= max(3, within-chart p95) at offsets 7 and 8 "
    "texels, beyond any wider reflection band. These offsets remain when Poisson removes reflection. Offsets "
    "1/2/4/6 and same-3D-point agreement are diagnostic; Poisson does not impose same-point equality.",
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
