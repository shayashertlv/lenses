"""Receipts of g3d.faceted_temple /2 (plan/WP6_FACETS.md §2.2 step 9, §4.5). numpy only.

`receipt(tb, include_stations=False)` is the compact receipt Astra reads (numbers rounded: mm 0.01, rise 0.001,
deg 0.1):
    lines         one line per crease first ("F1|F2 ridge 12.2° declared ridge"; beside a warp facet with its range,
                  on a soft border "soft", beside a W_BORDER_RAMP step the step: crease_line), then one per warning
    creases       pair, built shape, length-weighted dihedral (facet|base: the fold across the border against the
                  base surface, WP6_V3 §3's angle), declared shape; range [lo, hi] beside a warp, soft, step when set;
                  the shape, the mean and the range over the same face-to-face edges (build.face_edge: roll and
                  chamfer bands left out), so a printed mean lies inside its printed range under the word those
                  edges vote; angles rounded by build.deg_round (never across the 2 deg internal flat line).
                  A shallow built angle says "built angle 1.9°", not the measurement label "flat".
    facets        lean against the local base normal (a warp facet's: its least-squares plane's, with warp_mm = the
                  largest corner distance from that plane, and with 4 corners corner_off = the fit's W_FACE_NOT_FLAT
                  measure, "K +1.52 mm from the plane of A, E, H": build._corner_off), the crease rise relative to
                  the local base gradient, soft (only when true)
    thickness     min / max and where
    border_ramps  per facet/base border run: facet, d range, signed ramp height (+ = the facet stands proud of the
                  base, - = sunk below it), slope (deg) over border_band_mm, run length; as text (a receipt line,
                  the lab's ramp_text): "shoulder proud 0.81 mm: a 7.7° ramp at d 75.5-76.7" (ramp_clause:
                  crease_line's step clause, with the d)
    stations      only with include_stations=True (facet_inspect; §4.5 keeps edit receipts short): a 10 mm table of
                  d, y_top, y_bot, x_in, t_top, t_bot
    snaps         every knot canonicalisation move
    checks        closed, Euler, volume, planarity
The Blender layer adds decal motion and the hinge leaf gap / knuckle contact per side, and de-duplicates lines across
sides (`merge_lines`).
`model_edges(tb)` (build.model_edges, WP6_V3 §5 interface I1) gives the build's creases per connected run with their
signed angle per point, as the compares draw them.
"""
from __future__ import annotations

import numpy as np

from . import CORE_VERSION, SCHEMA
from .build import deg_round, model_edges  # noqa: F401  (model_edges: the compares' crease runs)
from .spec import plain

RECEIPT_STATION_STEP_MM = 10.0
STATION_COLUMNS = ["d", "y_top", "y_bot", "x_in", "t_top", "t_bot"]


def _r(x, nd: int = 2):
    if x is None:
        return None
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_r(v, nd) for v in x]
    return round(float(x), nd) + 0.0                     # + 0.0: no "-0.0"


def crease_line(c: dict) -> str:
    """One crease record of build.tables["creases"] as a receipt line: "F1|F2 shape deg°", then beside a warp facet
    its face-to-face range "(lo-hi)" (a crease beside a warp changes angle along its length: run 7's crown|logo 17.3
    built over most of it, 27.4 beside K, review-builder-design F4; the mean is over the same edges, so it lies inside
    the range: over every edge the roll band pulled crown|shoulder to "36.9° (37.7-39.3)", v3 probe1 §5.8), "soft" on
    a soft facet's border, the step beside
    a facet|base fold when W_BORDER_RAMP reports one "(sunk 1.24 mm: a 31.9° ramp)", and the declaration. Mean and
    range by build.deg_round at 0.1: a shallow crease at 1.9997 prints "built angle 1.9°", never 2.0° (< 2;
    review2-builder2-1 P3), and the printed mean still lies inside the printed range (deg_round never decreases)."""
    label = "built angle" if c["shape"] == "flat" else c["shape"]
    line = f"{'|'.join(c['regions'])} {label} {deg_round(c['mean_dihedral_deg'], 1):.1f}°"
    if c.get("warp_beside") and c.get("range_deg"):
        line += f" ({deg_round(c['range_deg'][0], 1):.1f}-{deg_round(c['range_deg'][1], 1):.1f})"
    if c.get("soft"):
        line += " soft"
    if c.get("step"):
        line += f" ({ramp_clause(c['step']['height_mm'], c['step']['slope_deg'])})"
    return line + (f" declared {c['declared']}" if c.get("declared") else " undeclared")


def ramp_clause(height_mm: float, slope_deg: float, d_mm=None) -> str:
    """A border ramp in words and units, the one form every ramp text uses: "proud 0.81 mm: a 7.7° ramp" (+ = the
    facet stands proud of the base, else "sunk" below it), with d_mm [d0, d1] "... at d 75.5-76.7" (one d when the
    two print alike: "at d 75.5"). crease_line's step clause is this in brackets. Why: the lab printed its ramp runs
    as "shoulder +0.81 7.7° d 75.5-76.7", a bare signed number and an angle with no unit or word, beside crease lines
    that say "proud 0.81 mm: a 7.7° ramp" of the same run (v3 session2 item 8)."""
    text = f"{'proud' if height_mm > 0 else 'sunk'} {abs(height_mm):.2f} mm: a {slope_deg:.1f}° ramp"
    if d_mm is not None and len(d_mm):
        a, b = f"{float(d_mm[0]):.1f}", f"{float(d_mm[-1]):.1f}"
        text += f" at d {a if a == b else f'{a}-{b}'}"
    return text


def crease_lines(tb) -> list[str]:
    return [crease_line(c) for c in tb.tables["creases"] if c["kind"] == "crease"]


def warning_lines(tb) -> list[str]:
    return [f"{w['code']}: {w['message']}" for w in tb.warnings]


def merge_lines(*side_lines) -> list[str]:
    """Lines of several sides in order, each distinct line once (the -X temple is the +X temple's mirror)."""
    out, seen = [], set()
    for lines in side_lines:
        for ln in lines:
            if ln not in seen:
                seen.add(ln)
                out.append(ln)
    return out


def station_table(ps, step: float = RECEIPT_STATION_STEP_MM) -> list[list[float]]:
    """[d, y_top, y_bot, x_in, t_top, t_bot] every `step` mm from the hinge-end cap, plus the tip cap."""
    ds = list(np.arange(ps.d0, ps.d1, step)) + [ps.d1]
    return [[float(d), float(ps.y_top(d)), float(ps.y_bot(d)), float(ps.inner(d)), float(ps.t_top(d)),
             float(ps.t_bot(d))] for d in ds]


def receipt(tb, *, core_ms: float | None = None, include_stations: bool = False) -> dict:
    """The compact receipt (module docstring); the 10 mm station table only with include_stations."""
    t = tb.tables
    creases = []
    for c in t["creases"]:
        if c["kind"] != "crease":
            continue
        rec = {"pair": "|".join(c["regions"]), "shape": c["shape"], "deg": deg_round(c["mean_dihedral_deg"], 1),
               "declared": c.get("declared")}
        if c.get("warp_beside") and c.get("range_deg"):
            rec["range_deg"] = [deg_round(x, 1) for x in c["range_deg"]]
        if c.get("soft"):
            rec["soft"] = True
        if c.get("step"):
            rec["step"] = {"height_mm": _r(c["step"]["height_mm"]), "slope_deg": _r(c["step"]["slope_deg"], 1)}
        creases.append(rec)
    facets = []
    for f in t["facets"]:
        rec = {"label": f["label"], "lean_deg": _r(f["lean_deg"], 1)}
        if "rise_per_mm" in f:
            rec["rise"] = _r(f["rise_per_mm"], 3)
            rec["rise_vs_base"] = _r(f["rise_rel_base_per_mm"], 3)
        if "warp_mm" in f:
            rec["warp_mm"] = _r(f["warp_mm"])
            co = f.get("corner_off")
            if co:
                rec["corner_off"] = f"{co['node']} {co['mm']:+.2f} mm from the plane of {', '.join(co['plane_of'])}"
        if f.get("soft"):
            rec["soft"] = True
        facets.append(rec)
    th = t["thickness"]
    out = {"tool": "g3d.faceted_temple", "schema": SCHEMA, "core_version": CORE_VERSION,
           "spec_sha256": tb.spec.sha256, "lines": crease_lines(tb) + warning_lines(tb), "creases": creases,
           "facets": facets,
           "thickness": {"min_mm": _r(th["min_mm"]), "min_at": _r(th["min_at"]), "max_mm": _r(th["max_mm"]),
                         "max_at": _r(th["max_at"])},
           "border_ramps": [{"facet": r["facet"], "d_mm": _r(r["d_mm"], 1), "height_mm": _r(r["height_mm"]),
                             "slope_deg": _r(r["slope_deg"], 1), "length_mm": _r(r["length_mm"])}
                            for r in t["border_ramps"]],
           "snaps": [{"what": s["what"], "from": _r(s["from"], 4), "to": _r(s["to"], 4)} for s in tb.spec.snaps],
           "feature_snaps": plain(tb.arr.feature_snaps),
           "checks": {"closed": tb.checks["closed"], "euler": tb.checks["euler"],
                      "float32_degenerate_triangles": tb.checks["float32_degenerate_triangles"],
                      "volume_mm3": _r(tb.checks["volume_mm3"], 1),
                      "planar": tb.checks["planarity_max_mm"] <= tb.spec.options["planar_tol_mm"]},
           "budget": {"core_ms": _r(core_ms, 1)}}
    if include_stations:
        out["stations"] = {"columns": STATION_COLUMNS, "rows": [_r(row) for row in station_table(tb.spec)]}
    if t.get("roll"):
        out["roll"] = {"roll_mm": _r(t["roll"]["roll_mm"]), "limit_mm": _r(t["roll"]["limit_mm"])}
    return plain(out)
