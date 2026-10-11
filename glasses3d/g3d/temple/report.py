"""Receipts of g3d.faceted_temple /2 (plan/WP6_FACETS.md §2.2 step 9, §4.5). numpy only.

`receipt(tb, include_stations=False)` is the compact receipt Astra reads (numbers rounded: mm 0.01, rise 0.001,
deg 0.1):
    lines         one line per crease first ("F1|F2 ridge 12.2° declared ridge"), then one per warning
    creases       pair, built shape, length-weighted dihedral, declared shape
    facets        lean against the local base normal, the crease rise relative to the local base gradient, fade
    thickness     min / max and where
    border_ramps  per facet/base border run: facet, d range, signed ramp height (+ = the facet stands proud of the
                  base, - = sunk below it), slope (deg) over border_band_mm, run length
    stations      only with include_stations=True (facet_inspect; §4.5 keeps edit receipts short): a 10 mm table of
                  d, y_top, y_bot, x_in, t_top, t_bot
    snaps         every knot canonicalisation move
    checks        closed, Euler, volume, planarity
The Blender layer adds decal motion and the hinge leaf gap / knuckle contact per side, and de-duplicates lines across
sides (`merge_lines`). `receipt_full(tb)` holds the unrounded tables for a file, never for Astra.
"""
from __future__ import annotations

import numpy as np

from . import CORE_VERSION, SCHEMA
from . import measure as MS
from .spec import plain

RECEIPT_STATION_STEP_MM = 10.0
STATION_COLUMNS = ["d", "y_top", "y_bot", "x_in", "t_top", "t_bot"]


def _r(x, nd: int = 2):
    if x is None:
        return None
    if isinstance(x, (list, tuple, np.ndarray)):
        return [_r(v, nd) for v in x]
    return round(float(x), nd) + 0.0                     # + 0.0: no "-0.0"


def crease_lines(tb) -> list[str]:
    out = []
    for c in tb.tables["creases"]:
        if c["kind"] != "crease":
            continue
        dec = f"declared {c['declared']}" if c.get("declared") else "undeclared"
        out.append(f"{'|'.join(c['regions'])} {c['shape']} {c['mean_dihedral_deg']:.1f}° {dec}")
    return out


def warning_lines(tb) -> list[str]:
    return [f"{w['code']}: {w['message']}" for w in tb.warnings]


def refusal_line(err) -> str:
    """One receipt line for a TempleError."""
    return f"{err.code}: {err.message}"


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
    creases = [{"pair": "|".join(c["regions"]), "shape": c["shape"], "deg": _r(c["mean_dihedral_deg"], 1),
                "declared": c.get("declared")} for c in t["creases"] if c["kind"] == "crease"]
    facets = []
    for f in t["facets"]:
        rec = {"label": f["label"], "lean_deg": _r(f["lean_deg"], 1)}
        if "rise_per_mm" in f:
            rec["rise"] = _r(f["rise_per_mm"], 3)
            rec["rise_vs_base"] = _r(f["rise_rel_base_per_mm"], 3)
        if f["fade"] is not None:
            rec["fade"] = [_r(f["fade"]["d0"]), _r(f["fade"]["d1"])]
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
           "checks": {"closed": tb.checks["closed"], "euler": tb.checks["euler"],
                      "volume_mm3": _r(tb.checks["volume_mm3"], 1),
                      "planar": tb.checks["planarity_max_mm"] <= 1e-9},
           "budget": {"core_ms": _r(core_ms, 1)}}
    if include_stations:
        out["stations"] = {"columns": STATION_COLUMNS, "rows": [_r(row) for row in station_table(tb.spec)]}
    if t.get("roll"):
        out["roll"] = {"roll_mm": _r(t["roll"]["roll_mm"]), "limit_mm": _r(t["roll"]["limit_mm"])}
    return plain(out)


def receipt_full(tb, *, core_ms: float | None = None) -> dict:
    """Every table unrounded (for a file next to the run, never for Astra)."""
    out = {"tool": "g3d.faceted_temple", "schema": SCHEMA, "core_version": CORE_VERSION,
           "spec_sha256": tb.spec.sha256, "canonical_spec": tb.spec.canonical, "snapped_spec": tb.spec.snapped,
           "lines": crease_lines(tb) + warning_lines(tb), "warnings": tb.warnings, "checks": tb.checks,
           "stations": {"columns": STATION_COLUMNS, "rows": station_table(tb.spec, 1.0)},
           "sharp_edges": len(tb.sharp),
           "mesh": {"vertices": int(len(tb.V)), "polygons": len(tb.polys), "triangles": int(len(tb.tris))},
           "budget": {"core_ms": core_ms}}
    out.update(tb.tables)
    return plain(out)


# --------------------------------------------------------------------------------------------- measures (information)
def facet_planes_chart(tb) -> list:
    """[(label, unit normal, offset)] of the outer plane of every facet with a facet cell (fade weight 0) in the chart
    (n . x = c)."""
    out = []
    for label in sorted(tb.spec.facets):
        ids = [i for i, (k, rg, c) in enumerate(zip(tb.poly_kind, tb.poly_region, tb.poly_cell))
               if k == "outer" and rg == label and c == "facet"]
        if not ids:
            continue
        n = tb.normals[ids[0]]
        c = float(n @ tb.V[tb.polys[ids[0]][0]])
        out.append((label, n, c))
    return out


def geometry_numbers(tb, photo_creases: int | None = None, zone_end: float = 50.0) -> dict:
    """measure.measure_temple on the build's own mesh with its facet planes (information, no gate)."""
    m = MS.measure_temple(tb.V, tb.tris, zone_end=zone_end, d_last=tb.spec.d1, facet_planes=facet_planes_chart(tb),
                          photo_creases=photo_creases, station_step=5.0, with_facets=False)
    m.pop("facets", None)
    return m
