"""g3d.temple/1 -> g3d.temple/2 conversion (plan/WP6_FACETS.md §2.3; tests and owner sheets only). numpy only.

The only place a /1 spec is still read: `parse_v1` validates the v1 fields the conversion needs (the zone lines),
`v1_to_v2` writes the /2 spec whose surface equals v1's over the v1 zone:

- the v1 base outer surface over [d0, zone_end] is X = x_in_lin(d) + T_lin(d), both lines (v1 refused anything else),
  extended linearly; a node with relief r gets X = x_in_lin(d) + T_lin(d) + r and t = X - x_in_v2(d) (x_in_v2 = the
  same inner table, extended linearly by the /2 core); a solved (null) node stays null;
- a crease plane's rise gains the base gradient across the crease: rise2 = rise1 + (dX_base/dd) n_in,d (n_in = the
  unit normal into the facet, planes.crease_frame);
- base.top = base.bottom = the v1 thickness table; zone_end_d is dropped; options lose crease_sharp_deg (a /2 constant)
  and get snap_mm (default 0: v1 had no snapping, so the converted geometry is v1's exactly).
"""
from __future__ import annotations

import copy

import numpy as np

from .planes import crease_frame, interior_left_of
from .spec import SCHEMA, TempleError, _num, _pairs

SCHEMA_V1 = "g3d.temple/1"
V1_KEYS = ("schema", "hinge", "outline", "inner", "thickness", "zone_end_d", "nodes", "facets", "chamfers", "roll_mm",
           "options")
V1_OPTIONS = ("t_min_mm", "station_step_mm", "planar_tol_mm", "crease_sharp_deg", "kink_sharp_deg")


def _line(pairs, d0: float, ze: float, name: str) -> tuple[float, float]:
    """(value at d0, slope) of a v1 zone table, which must be linear over [d0, ze] (v1 E_ZONE)."""
    a = np.asarray(pairs, np.float64)
    v0, v1 = float(np.interp(d0, a[:, 0], a[:, 1])), float(np.interp(ze, a[:, 0], a[:, 1]))
    s = (v1 - v0) / (ze - d0)
    for d, v in a:
        if d0 < d < ze and abs(v - (v0 + s * (d - d0))) > 1e-9:
            raise TempleError("E_SPEC", f"v1 {name} is not linear over the zone [{d0:g}, {ze:g}] (knot at d {d:g})",
                              table=name, knot_d=float(d))
    if a[0, 0] > d0 + 1e-12 or a[-1, 0] < ze - 1e-12:
        raise TempleError("E_SPEC", f"v1 {name} must cover the zone [{d0:g}, {ze:g}]", table=name)
    return v0, s


def parse_v1(spec1: dict) -> dict:
    """The v1 fields v1_to_v2 needs, validated: a deep copy with numbers as float and the facet shorthand expanded."""
    if not isinstance(spec1, dict) or spec1.get("schema", SCHEMA_V1) != SCHEMA_V1:
        raise TempleError("E_SPEC", f"v1_to_v2 needs a {SCHEMA_V1!r} spec")
    bad = sorted(set(spec1) - set(V1_KEYS))
    if bad:
        raise TempleError("E_SPEC", f"unknown v1 spec keys {bad}", unknown=bad)
    for k in ("hinge", "outline", "inner", "thickness", "zone_end_d", "nodes", "facets"):
        if k not in spec1:
            raise TempleError("E_SPEC", f"missing v1 spec key {k!r}", missing=k)
    s = copy.deepcopy(spec1)
    out = {"hinge": s["hinge"], "outline": s["outline"], "inner": _pairs(s["inner"], "inner"),
           "thickness": _pairs(s["thickness"], "thickness"), "zone_end_d": _num(s["zone_end_d"], "zone_end_d"),
           "chamfers": s.get("chamfers", []) or [], "roll_mm": _num(s.get("roll_mm", 0.0), "roll_mm"),
           "options": dict(s.get("options", {}) or {})}
    bad = sorted(set(out["options"]) - set(V1_OPTIONS))
    if bad:
        raise TempleError("E_SPEC", f"unknown v1 options {bad}", unknown=bad)
    nodes = {}
    for name, v in s["nodes"].items():
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise TempleError("E_SPEC", f"v1 node {name}: must be [d, y, r | null]", node=name)
        nodes[name] = [_num(v[0], f"nodes.{name}[0]"), _num(v[1], f"nodes.{name}[1]"),
                       None if v[2] is None else _num(v[2], f"nodes.{name}[2]")]
    out["nodes"] = nodes
    facets = {}
    for label, f in s["facets"].items():
        if isinstance(f, (list, tuple)):
            f = {"poly": list(f), "plane": "nodes"}
        facets[label] = {"poly": list(f["poly"]), "plane": f.get("plane", "nodes")}
    out["facets"] = facets
    return out


def v1_to_v2(spec1: dict, *, snap_mm: float | None = 0.0) -> dict:
    """The /2 spec of a /1 spec (module docstring). snap_mm: the /2 option to write (None = the /2 default)."""
    v1 = parse_v1(spec1)
    d0 = float(v1["outline"]["top"][0][0])
    ze = v1["zone_end_d"]
    if not d0 < ze:
        raise TempleError("E_SPEC", f"v1 zone_end_d {ze:g} must lie behind the hinge-end cap d {d0:g}")
    xi0, sxi = _line(v1["inner"], d0, ze, "inner")
    T0, sT = _line(v1["thickness"], d0, ze, "thickness")
    inner = np.asarray(v1["inner"], np.float64)

    def x_in_v2(d):                                         # the /2 PL extension of the same inner table
        if d < inner[0, 0] and len(inner) > 1:
            return inner[0, 1] + (inner[1, 1] - inner[0, 1]) / (inner[1, 0] - inner[0, 0]) * (d - inner[0, 0])
        if d > inner[-1, 0] and len(inner) > 1:
            return inner[-1, 1] + (inner[-1, 1] - inner[-2, 1]) / (inner[-1, 0] - inner[-2, 0]) * (d - inner[-1, 0])
        return float(np.interp(d, inner[:, 0], inner[:, 1]))

    nodes = {}
    for name, (d, y, r) in v1["nodes"].items():
        if r is None:
            nodes[name] = [d, y, None]
        else:
            X = xi0 + sxi * (d - d0) + T0 + sT * (d - d0) + r
            nodes[name] = [d, y, X - x_in_v2(d)]
    facets = {}
    sX = sxi + sT
    for label, f in v1["facets"].items():
        plane = f["plane"]
        if plane != "nodes":
            n1, n2 = plane["crease"]
            left = interior_left_of(f["poly"], n1, n2, v1["nodes"])
            _, n_in, _ = crease_frame(v1["nodes"][n1], v1["nodes"][n2], left)
            plane = {"crease": [n1, n2], "rise_per_mm": float(plane["rise_per_mm"]) + sX * float(n_in[0])}
        facets[label] = {"poly": list(f["poly"]), "plane": plane, "fade": None}
    opts = {k: v for k, v in v1["options"].items() if k != "crease_sharp_deg"}
    if snap_mm is not None:
        opts["snap_mm"] = float(snap_mm)
    thick = [list(p) for p in v1["thickness"]]
    return {"schema": SCHEMA, "hinge": v1["hinge"], "outline": v1["outline"], "inner": [list(p) for p in v1["inner"]],
            "base": {"top": thick, "bottom": copy.deepcopy(thick)}, "nodes": nodes, "facets": facets, "creases": [],
            "chamfers": v1["chamfers"], "roll_mm": v1["roll_mm"], "options": opts}
