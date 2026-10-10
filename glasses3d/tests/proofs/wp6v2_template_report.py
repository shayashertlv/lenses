"""T3 (plan/WP6_FACETS.md §2.4, §5): revision 0 of the Prada original. Reads the pipeline's export of the original
scene (checkpoint c0000 of real-astra-001, sha-bound), runs g3d.temple.template_from_mesh on the +X temple body,
rebuilds it with the /2 core (no facets) and writes, under data/wp6v2/template/:

    report.json      the template report (§2.4 numbers) + inputs, the edge-rounding measure and the image list
    spec.json        revision 0 (g3d.temple/2)
    chart.png        draw.chart_image of revision 0 with the decal and hinge footprints
    sections.png     draw.section_strip: revision 0 over the original at d 5..80
    sections_tip.png the same at d 95..136

    python -m glasses3d.tests.proofs.wp6v2_template_report [--out DIR]

Decals and hinge parts are found by geometry (measure.hinge_part_kind; every vertex within DECAL_NEAR_MM of the body),
never by name; only the body is named (the test case). The edge-rounding measure is information for the suggested
roll: per station, how far the original's outer profile falls below its straight-line fit near the top and bottom
edges, fitted with the core roll's quarter-arc depth c(s) = r - sqrt(r^2 - (r - s)^2).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from glasses3d.g3d.temple import build_temple  # noqa: E402
from glasses3d.g3d.temple import draw as DR  # noqa: E402
from glasses3d.g3d.temple import measure as MS  # noqa: E402
from glasses3d.g3d.temple import template as TT  # noqa: E402
from glasses3d.g3d_host import glb as GLB  # noqa: E402

G3D = Path(__file__).resolve().parents[2]
SRC = G3D / "data/wp6/astra_temple_lab_20261010/real-astra-001/work/exports/head-b89ad6603d00577f.glb"
SRC_SHA = "3198847036fcdc0f84a2eadc6cd429cc66f090f8e8431e246f854bd2e90d3012"
BODY = "Temple right | faceted sweep and ear bend"
OUT = G3D / "data" / "wp6v2" / "template"
EDGE_STATIONS = tuple(range(5, 96, 5))
EDGE_PROBE_MM = 1.5          # the edge-rounding measure looks this far in from each edge
# §2.4 "Roll": the coding agent's suggestion, read from sections.png / sections_tip.png (2026-10-10): the original's
# outer corners carry small 45-degree bevels of about 0.3-0.5 mm at d 5-35 (bottom) and d 65-128 (top and bottom); the
# bottom-edge quarter-arc fit gives a median 0.51 mm; the top edge at d 15-50 is the upper facet, not a rounding (its
# fit sits at the 1.5 mm probe limit). Revision 0 keeps roll_mm 0; Astra sets it.
ROLL_SUGGESTED_MM = 0.5


def parts(g) -> list:
    out = []
    for p in g.primitives:
        if p.mode != 4 or not len(p.triangles):
            continue
        role = (g.doc["nodes"][p.node_index].get("extras") or {}).get("partRole")
        out.append({"name": p.node_name, "role": role, "V": p.positions * 1000.0, "F": p.triangles})
    return out


def edge_rounding(L, F, ds) -> dict:
    """Quarter-arc radius fitted to the original's outer-profile drop below its line fit within EDGE_PROBE_MM of the
    top and bottom edges (least squares over a 0.01 mm radius grid), per station; information only."""
    rows = []
    rgrid = np.arange(0.0, EDGE_PROBE_MM + 1e-9, 0.01)
    for d in ds:
        seg = MS.slice_d(L, F, float(d))
        if not len(seg):
            continue
        yb, yt = float(seg[..., 0].min()), float(seg[..., 0].max())
        h = yt - yb
        lo, hi = yb + TT.EDGE_SHARE * h, yt - TT.EDGE_SHARE * h
        yg = np.linspace(lo, hi, 200)
        xo, _ = MS.section_profile(seg, yg)
        a, b, _ = TT.robust_line(yg[np.isfinite(xo)], xo[np.isfinite(xo)])
        rec = {"d": float(d)}
        for edge, y0, sgn in (("top", yt, -1.0), ("bottom", yb, 1.0)):
            s = np.linspace(0.005, EDGE_PROBE_MM, 150)
            ys = y0 + sgn * s
            x, _ = MS.section_profile(seg, ys)
            ok = np.isfinite(x)
            drop = (a + b * ys - x)[ok]
            s = s[ok]
            best = (np.inf, 0.0)
            for r in rgrid:
                c = np.where(s < r, r - np.sqrt(np.maximum(r * r - (r - s) ** 2, 0.0)), 0.0)
                e = float(np.mean((drop - c) ** 2))
                if e < best[0]:
                    best = (e, float(r))
            rec[edge] = {"r_mm": best[1], "rms_mm": math.sqrt(best[0]), "drop_at_edge_mm": float(drop[0]) if len(drop)
                         else None}
        rows.append(rec)
    rt = [r["top"]["r_mm"] for r in rows]
    rb = [r["bottom"]["r_mm"] for r in rows]
    return {"stations": rows, "median_r_top_mm": float(np.median(rt)), "median_r_bottom_mm": float(np.median(rb)),
            "probe_mm": EDGE_PROBE_MM}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    raw = SRC.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != SRC_SHA:
        raise SystemExit(f"{SRC} sha256 {sha} is not the c0000 export {SRC_SHA}")
    g = GLB.load(SRC)
    ps_ = parts(g)
    body = next(p for p in ps_ if p["name"] == BODY)
    V, F = body["V"], body["F"]
    side = 1 if float(V[:, 0].mean()) > 0 else -1
    hinge_z = float(V[:, 2].max())
    boxes = [(p["V"].min(0), p["V"].max(0), p["role"]) for p in ps_]
    axis = MS.knuckle_axis(boxes, side, hinge_z)
    spec, rep = TT.template_from_mesh(V, F, side, hinge_z, axis)
    tb = build_temple(spec)
    L, Fc = MS.chart_mesh(V, F, side, hinge_z)
    # footprints by geometry: hinge parts (measure.hinge_part_kind), decals (every vertex within DECAL_NEAR_MM of
    # the body, not a hinge part)
    hinge, decals = [], []
    for p in ps_:
        if p is body or side * float(p["V"][:, 0].mean()) <= 0:
            continue
        lo, hi = p["V"].min(0), p["V"].max(0)
        kind = MS.hinge_part_kind(lo, hi, p["role"], side, axis[0] if axis else None, hinge_z, len(p["V"]))
        Lp = MS.chart_from_world(p["V"], side, hinge_z)
        if kind is not None:
            hinge.append({"name": f"{kind}", "points": Lp[:, :2].tolist(), "part": p["name"]})
            continue
        if p["role"] != "temple" or len(p["V"]) > MS.HINGE_MAX_VERTICES:
            continue
        dist = TT.point_mesh_distance(Lp, L, Fc)
        if float(dist.max()) <= MS.DECAL_NEAR_MM:
            decals.append({"name": p["name"].split("|")[-1].strip(), "points": Lp[:, :2].tolist(), "part": p["name"],
                           "max_dist_mm": float(dist.max())})
    a.out.mkdir(parents=True, exist_ok=True)
    img = DR.chart_image(spec, tb, footprints={"decals": decals, "hinge": hinge},
                         title=f"revision 0 of the Prada original (+X temple)  |  spec {tb.spec.sha256[:8]}  |  "
                               f"thickness {tb.thickness.min():.2f}-{tb.thickness.max():.2f} mm, contours every 0.5 mm")
    img.save(a.out / "chart.png")
    strip = DR.section_strip([("revision 0", tb), ("original", (L, Fc))],
                             title="sections: revision 0 (black) over the Prada original (grey)")
    strip.save(a.out / "sections.png")
    strip2 = DR.section_strip([("revision 0", tb), ("original", (L, Fc))],
                              stations=(95, 102, 110, 120, 128, 134, 135.5),
                              title="sections near the bend and the tip: revision 0 (black) over the original (grey)")
    strip2.save(a.out / "sections_tip.png")
    edge = edge_rounding(L, Fc, EDGE_STATIONS)
    (a.out / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    out = {"proof": "T3 template on the Prada original", "input": {"path": str(SRC), "sha256": sha, "body": BODY,
                                                                    "checkpoint": "c0000"},
           "side": "+x" if side > 0 else "-x", "hinge_z_mm": hinge_z, "axis_xz_mm": axis,
           "spec_sha256": tb.spec.sha256, "template": rep,
           "footprints": {"decals": [{k: d[k] for k in ("name", "part", "max_dist_mm")} for d in decals],
                          "hinge": [{k: h[k] for k in ("name", "part")} for h in hinge]},
           "edge_rounding": edge,
           "roll_suggestion": {"roll_mm": ROLL_SUGGESTED_MM, "by": "coding agent, from the section strips",
                               "basis": "outer-corner bevels ~0.3-0.5 mm; bottom-edge arc fit median "
                                        f"{edge['median_r_bottom_mm']:.2f} mm; the top-edge fit is the upper facet"},
           "images": {"chart": str(a.out / "chart.png"), "sections": str(a.out / "sections.png"),
                      "sections_tip": str(a.out / "sections_tip.png"), "chart_size": list(img.size),
                      "sections_size": list(strip.size)},
           "seconds": time.perf_counter() - t0}
    (a.out / "report.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    r = rep["distance_original_to_rebuild"]
    print(f"spec {tb.spec.sha256[:12]}  knots {rep['knots']['count']}  lost tip {rep['length_lost_mm']['tip']:.2f} mm  "
          f"tip_round {rep['tip_round']['tip_round_mm']}")
    print(f"original->rebuild all mean {r['all']['mean_mm']:.3f} p95 {r['all']['p95_mm']:.3f} "
          f"max {r['all']['max_mm']:.3f}")
    for k, v in r["regions"].items():
        print(f"  {k:11s} d {v['d_mm']}  mean {v['mean_mm']:.3f} p95 {v['p95_mm']:.3f} max {v['max_mm']:.3f}")
    print(f"inner dev p95 {rep['inner_deviation_mm']['p95_mm']:.3f} max {rep['inner_deviation_mm']['max_mm']:.3f}")
    print(f"simplification {rep['simplification_error_mm']}")
    print(f"edge rounding median r top {edge['median_r_top_mm']:.2f} bottom {edge['median_r_bottom_mm']:.2f}")
    print(f"decals {[d['name'] for d in decals]}  hinge {[h['part'] for h in hinge]}")
    print(f"wrote {a.out}  ({out['seconds']:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
