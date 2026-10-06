"""Spec of g3d.faceted_temple (WP6_DESIGN.md §3): schema "g3d.temple/1", defaults, refusal and warning codes, the
canonical form (and its sha256), and validation into a ParsedSpec the builder works from. numpy only.

Spec keys (JSON):
    schema      "g3d.temple/1"
    hinge       {z_mm, axis_xz_mm, opening_deg}: d = z_mm - z at opening 0; the opening (deg, + = tip outward) turns
                the temple about the vertical axis through (axis_x, axis_z) (+X temple; the -X one is its mirror)
    outline     {top: [[d, y]], bottom: [[d, y]], tip_round_mm}: d strictly increasing, first d shared (hinge-end
                cap) and last d shared (tip cap); tip_round_mm rounds the two tip corners (arcs with
                max(8, ceil(8 theta / 90 deg)) segments)
    inner       [[d, x_in]]: the vertical inner face, linear over [d_first, zone_end_d]
    thickness   [[d, T]]: base thickness, linear over the same span (the base outer surface is a plane there)
    zone_end_d  end of the facet zone (facets beyond it: E_FACET_ON_BEND)
    nodes       {name: [d, y, r | null]}: r = mm outward from the base surface; null = solved from the planes
    facets      {label: {poly: [nodes], plane: "nodes" | {crease: [n1, n2], rise_per_mm: s}}} (a bare list = poly
                with plane "nodes"); clipped by the outline, no overlaps; the rest of the zone is base (r = 0)
    chamfers    [{edge: top | bottom, d: [d0, d1], width_mm, depth_mm, taper_mm?}]: a strip along an outline edge
                whose depth falls linearly from depth_mm at the edge to 0 at width_mm inside it; the width tapers
                linearly to 0 over taper_mm at both ends (default min(4 width, (d1 - d0) / 2)) so the chamfer never
                makes a step; planar on every cell of the arrangement
    roll_mm     0 = crisp (default); > 0 = Blender Bevel on the outer long edges (applied by g3d/temple_bpy.py)
    options     t_min_mm 1.0, station_step_mm 2.0, planar_tol_mm 1e-6, crease_sharp_deg 6, kink_sharp_deg 25

Every refusal is a TempleError(code, message, **fields): nothing is built, and the fields name the facet, point and
numbers that failed.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

import numpy as np

SCHEMA = "g3d.temple/1"

REFUSALS = ("E_SPEC", "E_TARGET", "E_OUTLINE", "E_OUTLINE_NOT_SINGLE_VALUED", "E_OUTLINE_NOT_SIMPLE", "E_ZONE",
            "E_FACET_ON_BEND", "E_FACET_OUTSIDE", "E_FACET_OVERLAP", "E_RELIEF_STEP", "E_FACET_UNDERDETERMINED",
            "E_FACET_NOT_PLANAR", "E_ILL_CONDITIONED", "E_TOO_THIN", "E_CHAMFER", "E_ROLL", "E_TRIANGULATE",
            "E_NOT_CLOSED")
WARNINGS = ("W_CONDITIONING", "W_FLAT_CREASE", "W_ARM_NORMAL_SPREAD", "W_DECAL_SPANS_CREASE", "W_HINGE_LEAF",
            "W_KNUCKLE_PENETRATION")

DEFAULT_OPTIONS = {"t_min_mm": 1.0, "station_step_mm": 2.0, "planar_tol_mm": 1e-6, "crease_sharp_deg": 6.0,
                   "kink_sharp_deg": 25.0}
ILL_CONDITIONED_DEG_PER_MM = 10.0       # E_ILL_CONDITIONED
CONDITIONING_WARN_DEG_PER_MM = 6.0      # W_CONDITIONING
FLAT_CREASE_DEG = 2.0                   # W_FLAT_CREASE
ARM_NORMAL_SPREAD_DEG = 30.0            # W_ARM_NORMAL_SPREAD
HINGE_LEAF_GAP_MM = 0.2                 # W_HINGE_LEAF: leaf-to-inner-face gap above this ...
HINGE_LEAF_EMBED_MM = 0.5               # ... or the leaf embedded deeper than this
KNUCKLE_PENETRATION_MM = 0.1            # W_KNUCKLE_PENETRATION
DEDUPE_MM = 1e-7                        # vertices closer than this are one vertex
TILE_TOL_MM2 = 1e-6                     # region areas must tile the outline within this
MIN_HEIGHT_MM = 0.3                     # outline height (side view) below this anywhere: E_OUTLINE
CHAMFER_TAPER_RATIO = 4.0               # default taper_mm = min(4 width, (d1 - d0) / 2)
ROUND_SEGMENTS_PER_90 = 8               # the round-corner rule: >= 8 points per 90 deg, never fewer than 8 segments
COLLINEAR_AREA_MM2 = 1e-6               # three plane points spanning less than this are collinear


class TempleError(ValueError):
    """A refusal: nothing is built or written. `fields` name what failed (facet, point, numbers)."""

    def __init__(self, code: str, message: str, **fields):
        super().__init__(f"{code}: {message}")
        if code not in REFUSALS:
            raise ValueError(f"unknown refusal code {code!r}")
        self.code, self.message, self.fields = code, message, _plain(fields)

    def to_dict(self) -> dict:
        return {"status": "refused", "code": self.code, "message": self.message, "fields": self.fields}


def _plain(x):
    """JSON-safe copy (numpy scalars and arrays to Python)."""
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.ndarray):
        return _plain(x.tolist())
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


plain = _plain


# --------------------------------------------------------------------------------------------- canonical form
def _num(x, where: str) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float, np.integer, np.floating)):
        raise TempleError("E_SPEC", f"{where} must be a number, got {type(x).__name__}", where=where)
    v = float(x)
    if not math.isfinite(v):
        raise TempleError("E_SPEC", f"{where} must be finite, got {v}", where=where)
    return v


def _pairs(x, where: str) -> list:
    if not isinstance(x, (list, tuple)) or not x:
        raise TempleError("E_SPEC", f"{where} must be a non-empty list of [d, value] pairs", where=where)
    out = []
    for i, p in enumerate(x):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise TempleError("E_SPEC", f"{where}[{i}] must be [d, value]", where=f"{where}[{i}]")
        out.append([_num(p[0], f"{where}[{i}][0]"), _num(p[1], f"{where}[{i}][1]")])
    return out


TOP_KEYS = ("schema", "hinge", "outline", "inner", "thickness", "zone_end_d", "nodes", "facets", "chamfers",
            "roll_mm", "options")


def canonical(spec: dict) -> dict:
    """The canonical spec: every key present with its default, numbers as float, the facet shorthand expanded, nodes
    and facets sorted by name. Raises TempleError(E_SPEC) on a malformed spec (types, unknown keys)."""
    if not isinstance(spec, dict):
        raise TempleError("E_SPEC", f"the spec must be a JSON object, got {type(spec).__name__}")
    unknown = sorted(set(spec) - set(TOP_KEYS))
    if unknown:
        raise TempleError("E_SPEC", f"unknown spec keys {unknown}; known: {list(TOP_KEYS)}", unknown=unknown)
    schema = spec.get("schema", SCHEMA)
    if schema != SCHEMA:
        raise TempleError("E_SPEC", f"schema must be {SCHEMA!r}, got {schema!r}")
    for k in ("hinge", "outline", "inner", "thickness", "zone_end_d", "nodes", "facets"):
        if k not in spec:
            raise TempleError("E_SPEC", f"missing spec key {k!r}", missing=k)
    h = spec["hinge"]
    if not isinstance(h, dict) or "z_mm" not in h:
        raise TempleError("E_SPEC", "hinge must be {z_mm, axis_xz_mm, opening_deg}")
    bad = sorted(set(h) - {"z_mm", "axis_xz_mm", "opening_deg"})
    if bad:
        raise TempleError("E_SPEC", f"unknown hinge keys {bad}", unknown=bad)
    axis = h.get("axis_xz_mm")
    if axis is not None:
        if not isinstance(axis, (list, tuple)) or len(axis) != 2:
            raise TempleError("E_SPEC", "hinge.axis_xz_mm must be [x, z] or null")
        axis = [_num(axis[0], "hinge.axis_xz_mm[0]"), _num(axis[1], "hinge.axis_xz_mm[1]")]
    hinge = {"z_mm": _num(h["z_mm"], "hinge.z_mm"), "axis_xz_mm": axis,
             "opening_deg": _num(h.get("opening_deg", 0.0), "hinge.opening_deg")}
    o = spec["outline"]
    if not isinstance(o, dict):
        raise TempleError("E_SPEC", "outline must be {top, bottom, tip_round_mm}")
    bad = sorted(set(o) - {"top", "bottom", "tip_round_mm"})
    if bad:
        raise TempleError("E_SPEC", f"unknown outline keys {bad}", unknown=bad)
    if "top" not in o or "bottom" not in o:
        raise TempleError("E_SPEC", "outline needs top and bottom chains")
    outline = {"top": _pairs(o["top"], "outline.top"), "bottom": _pairs(o["bottom"], "outline.bottom"),
               "tip_round_mm": _num(o.get("tip_round_mm", 0.0), "outline.tip_round_mm")}
    nodes_in = spec["nodes"]
    if not isinstance(nodes_in, dict):
        raise TempleError("E_SPEC", "nodes must be {name: [d, y, r | null]}")
    nodes = {}
    for name in sorted(nodes_in, key=str):
        v = nodes_in[name]
        if not isinstance(name, str) or not name:
            raise TempleError("E_SPEC", f"node names must be non-empty strings, got {name!r}")
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise TempleError("E_SPEC", f"node {name}: must be [d, y, r | null]", node=name)
        r = None if v[2] is None else _num(v[2], f"nodes.{name}[2]")
        nodes[name] = [_num(v[0], f"nodes.{name}[0]"), _num(v[1], f"nodes.{name}[1]"), r]
    fac_in = spec["facets"]
    if not isinstance(fac_in, dict):
        raise TempleError("E_SPEC", "facets must be {label: {poly, plane}}")
    facets = {}
    for label in sorted(fac_in, key=str):
        f = fac_in[label]
        if not isinstance(label, str) or not label or label == "base":
            raise TempleError("E_SPEC", f"facet labels must be non-empty strings other than 'base', got {label!r}")
        if isinstance(f, (list, tuple)):
            f = {"poly": list(f), "plane": "nodes"}
        if not isinstance(f, dict) or "poly" not in f:
            raise TempleError("E_SPEC", f"facet {label}: must be {{poly, plane}} or a list of nodes", facet=label)
        bad = sorted(set(f) - {"poly", "plane"})
        if bad:
            raise TempleError("E_SPEC", f"facet {label}: unknown keys {bad}", facet=label)
        poly = f["poly"]
        if not isinstance(poly, (list, tuple)) or len(poly) < 3 or not all(isinstance(n, str) for n in poly):
            raise TempleError("E_SPEC", f"facet {label}: poly must list >= 3 node names", facet=label)
        if len(set(poly)) != len(poly):
            raise TempleError("E_SPEC", f"facet {label}: poly repeats a node", facet=label, poly=list(poly))
        missing = [n for n in poly if n not in nodes]
        if missing:
            raise TempleError("E_SPEC", f"facet {label}: unknown nodes {missing}", facet=label, missing=missing)
        plane = f.get("plane", "nodes")
        if plane != "nodes":
            if not isinstance(plane, dict) or set(plane) != {"crease", "rise_per_mm"}:
                raise TempleError("E_SPEC", f"facet {label}: plane must be 'nodes' or {{crease: [n1, n2], "
                                  f"rise_per_mm: s}}", facet=label)
            cr = plane["crease"]
            if not isinstance(cr, (list, tuple)) or len(cr) != 2 or cr[0] == cr[1]:
                raise TempleError("E_SPEC", f"facet {label}: crease must name two different nodes", facet=label)
            if any(n not in poly for n in cr):
                raise TempleError("E_SPEC", f"facet {label}: crease nodes {list(cr)} must be corners of its poly",
                                  facet=label)
            i, j = poly.index(cr[0]), poly.index(cr[1])
            if (i - j) % len(poly) not in (1, len(poly) - 1):
                raise TempleError("E_SPEC", f"facet {label}: crease {list(cr)} must be an edge of its poly "
                                  f"(adjacent corners)", facet=label)
            plane = {"crease": [str(cr[0]), str(cr[1])],
                     "rise_per_mm": _num(plane["rise_per_mm"], f"facets.{label}.plane.rise_per_mm")}
        facets[label] = {"poly": [str(n) for n in poly], "plane": plane}
    chamfers = []
    for i, c in enumerate(spec.get("chamfers", []) or []):
        if not isinstance(c, dict):
            raise TempleError("E_SPEC", f"chamfers[{i}] must be an object", chamfer=i)
        bad = sorted(set(c) - {"edge", "d", "width_mm", "depth_mm", "taper_mm"})
        if bad or not {"edge", "d", "width_mm", "depth_mm"} <= set(c):
            raise TempleError("E_SPEC", f"chamfers[{i}] needs edge, d, width_mm, depth_mm (taper_mm optional)",
                              chamfer=i, unknown=bad)
        if c["edge"] not in ("top", "bottom"):
            raise TempleError("E_CHAMFER", f"chamfers[{i}].edge must be top or bottom, got {c['edge']!r}", chamfer=i)
        d = c["d"]
        if not isinstance(d, (list, tuple)) or len(d) != 2:
            raise TempleError("E_SPEC", f"chamfers[{i}].d must be [d0, d1]", chamfer=i)
        rec = {"edge": c["edge"], "d": [_num(d[0], f"chamfers[{i}].d[0]"), _num(d[1], f"chamfers[{i}].d[1]")],
               "width_mm": _num(c["width_mm"], f"chamfers[{i}].width_mm"),
               "depth_mm": _num(c["depth_mm"], f"chamfers[{i}].depth_mm")}
        length = rec["d"][1] - rec["d"][0]
        if "taper_mm" in c and c["taper_mm"] is not None:
            rec["taper_mm"] = _num(c["taper_mm"], f"chamfers[{i}].taper_mm")
        else:
            rec["taper_mm"] = min(CHAMFER_TAPER_RATIO * rec["width_mm"], max(length, 0.0) / 2.0)
        chamfers.append(rec)
    opts = dict(DEFAULT_OPTIONS)
    oin = spec.get("options", {}) or {}
    if not isinstance(oin, dict):
        raise TempleError("E_SPEC", "options must be an object")
    bad = sorted(set(oin) - set(DEFAULT_OPTIONS))
    if bad:
        raise TempleError("E_SPEC", f"unknown options {bad}; known: {sorted(DEFAULT_OPTIONS)}", unknown=bad)
    for k, v in oin.items():
        opts[k] = _num(v, f"options.{k}")
    for k in ("t_min_mm", "station_step_mm", "planar_tol_mm"):
        if opts[k] <= 0:
            raise TempleError("E_SPEC", f"options.{k} must be > 0, got {opts[k]}", option=k)
    roll = _num(spec.get("roll_mm", 0.0), "roll_mm")
    if roll < 0:
        raise TempleError("E_SPEC", f"roll_mm must be >= 0, got {roll}")
    return {"schema": SCHEMA, "hinge": hinge, "outline": outline,
            "inner": _pairs(spec["inner"], "inner"), "thickness": _pairs(spec["thickness"], "thickness"),
            "zone_end_d": _num(spec["zone_end_d"], "zone_end_d"), "nodes": nodes, "facets": facets,
            "chamfers": chamfers, "roll_mm": roll, "options": opts}


def canonical_json(spec: dict) -> str:
    """Compact, key-sorted JSON of the canonical spec (floats in Python's round-trip repr)."""
    return json.dumps(canonical(spec), sort_keys=True, separators=(",", ":"), allow_nan=False)


def spec_sha256(spec: dict) -> str:
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------- piecewise linear
class PL:
    """A piecewise-linear table [[d, v]] (d strictly increasing), evaluated by linear interpolation."""

    def __init__(self, pairs, name: str):
        a = np.asarray(pairs, np.float64).reshape(-1, 2)
        if len(a) < 1:
            raise TempleError("E_SPEC", f"{name} is empty")
        if len(a) > 1 and np.any(np.diff(a[:, 0]) <= 0):
            i = int(np.nonzero(np.diff(a[:, 0]) <= 0)[0][0])
            raise TempleError("E_SPEC", f"{name}: d must strictly increase (knot {i + 1} at d {a[i + 1, 0]:g} after "
                              f"{a[i, 0]:g})", table=name, knot=i + 1)
        self.d, self.v, self.name = a[:, 0].copy(), a[:, 1].copy(), name

    def __call__(self, d):
        if len(self.d) == 1:
            return np.full(np.shape(d), self.v[0]) if np.ndim(d) else float(self.v[0])
        out = np.interp(d, self.d, self.v)
        return out if np.ndim(d) else float(out)

    def slope(self, d0: float, d1: float) -> float:
        return (self(d1) - self(d0)) / (d1 - d0)

    def covers(self, d0: float, d1: float) -> bool:
        return self.d[0] <= d0 + 1e-12 and self.d[-1] >= d1 - 1e-12

    def knots_inside(self, d0: float, d1: float) -> np.ndarray:
        return self.d[(self.d > d0 + 1e-9) & (self.d < d1 - 1e-9)]

    def linear_misfit(self, d0: float, d1: float) -> tuple[float, float | None]:
        """Largest distance of a knot inside (d0, d1) from the chord v(d0) -> v(d1): (misfit, knot d)."""
        worst, at = 0.0, None
        v0, v1 = self(d0), self(d1)
        for k in self.knots_inside(d0, d1):
            m = abs(self(k) - (v0 + (v1 - v0) * (k - d0) / (d1 - d0)))
            if m > worst:
                worst, at = m, float(k)
        return worst, at


# --------------------------------------------------------------------------------------------- parsed spec
@dataclass
class ParsedSpec:
    canonical: dict
    top: np.ndarray                 # (n, 2) (d, y) after tip rounding
    bottom: np.ndarray
    d0: float
    d1: float
    inner: PL
    thickness: PL
    zone_end: float
    nodes: dict                     # name -> (d, y, r | None)
    facets: dict                    # label -> {"poly": [...], "plane": ...}
    chamfers: list
    options: dict
    hinge: dict
    roll_mm: float
    sha256: str = ""
    notes: list = field(default_factory=list)

    def y_top(self, d):
        return np.interp(d, self.top[:, 0], self.top[:, 1])

    def y_bot(self, d):
        return np.interp(d, self.bottom[:, 0], self.bottom[:, 1])

    def x_base(self, d):
        """Base outer surface X (inner face + thickness)."""
        return self.inner(d) + self.thickness(d)

    def boundary(self) -> np.ndarray:
        """The outline as one CCW polygon in (d, y): bottom chain (d up), tip cap (up), top chain (d down), hinge cap
        (down). Cap edges are single segments here; arrangement vertices split them later."""
        return np.vstack([self.bottom, self.top[::-1]])


def _round_corner(chain: np.ndarray, cap_dir: np.ndarray, r: float, which: str) -> tuple[np.ndarray, float]:
    """Replace the chain's last corner (where it meets the vertical tip cap) by a tangent arc of radius r.
    Returns (new chain, tangent length along the cap)."""
    if r <= 0:
        return chain, 0.0
    P, Q = chain[-1], chain[-2]
    seg = P - Q
    L = float(np.hypot(*seg))
    u = seg / L
    w = cap_dir
    cosang = float(np.clip(u @ w, -1.0, 1.0))
    theta = math.acos(cosang)                               # deflection angle at the corner
    if theta < 1e-9:
        return chain, 0.0
    t = r * math.tan(theta / 2.0)
    if t >= L - 1e-9:
        raise TempleError("E_OUTLINE", f"tip_round_mm {r:g} needs {t:.3f} mm of the last {which} segment, which is "
                          f"only {L:.3f} mm long", edge=which, tangent_mm=t, segment_mm=L)
    T1 = P - u * t
    turn = u[0] * w[1] - u[1] * w[0]                        # + = left turn
    n1 = np.array([-u[1], u[0]]) if turn > 0 else np.array([u[1], -u[0]])
    centre = T1 + n1 * r
    nseg = max(ROUND_SEGMENTS_PER_90, int(math.ceil(ROUND_SEGMENTS_PER_90 * math.degrees(theta) / 90.0 - 1e-9)))
    a0 = math.atan2(T1[1] - centre[1], T1[0] - centre[0])
    sgn = 1.0 if turn > 0 else -1.0
    arc = [centre + r * np.array([math.cos(a0 + sgn * theta * k / nseg), math.sin(a0 + sgn * theta * k / nseg)])
           for k in range(nseg + 1)]
    arc[-1] = np.array([P[0], (P + w * t)[1]])            # exact tangent point on the cap: d = d_last
    out = np.vstack([chain[:-1], np.array(arc)])
    keep = np.r_[True, np.hypot(*(np.diff(out, axis=0).T)) > DEDUPE_MM]
    return out[keep], t


def parse(spec: dict) -> ParsedSpec:
    """Validate a spec (raises TempleError with the failing field) and return the ParsedSpec the builder uses."""
    c = canonical(spec)
    top = np.asarray(c["outline"]["top"], np.float64)
    bot = np.asarray(c["outline"]["bottom"], np.float64)
    for name, ch in (("top", top), ("bottom", bot)):
        if len(ch) < 2:
            raise TempleError("E_OUTLINE", f"outline.{name} needs >= 2 points", edge=name)
        dd = np.diff(ch[:, 0])
        if np.any(dd <= 0):
            i = int(np.nonzero(dd <= 0)[0][0])
            raise TempleError("E_OUTLINE_NOT_SINGLE_VALUED",
                              f"outline.{name}: d must strictly increase (point {i + 1} at d {ch[i + 1, 0]:g} after "
                              f"d {ch[i, 0]:g}); cable temples and hooks that turn back are not supported",
                              edge=name, index=i + 1, d=[float(ch[i, 0]), float(ch[i + 1, 0])])
    if top[0, 0] != bot[0, 0] or top[-1, 0] != bot[-1, 0]:
        raise TempleError("E_OUTLINE", "outline top and bottom must share their first d (hinge-end cap) and last d "
                          "(tip cap)", top_d=[float(top[0, 0]), float(top[-1, 0])],
                          bottom_d=[float(bot[0, 0]), float(bot[-1, 0])])
    d0, d1 = float(top[0, 0]), float(top[-1, 0])
    knots = np.unique(np.r_[top[:, 0], bot[:, 0]])
    h = np.interp(knots, top[:, 0], top[:, 1]) - np.interp(knots, bot[:, 0], bot[:, 1])
    if np.any(h <= 0):
        i = int(np.argmin(h))
        raise TempleError("E_OUTLINE_NOT_SIMPLE", f"outline top and bottom cross at d {knots[i]:g} (height "
                          f"{h[i]:.3f} mm)", d=float(knots[i]), height_mm=float(h[i]))
    if np.any(h < MIN_HEIGHT_MM):
        i = int(np.argmin(h))
        raise TempleError("E_OUTLINE", f"outline height {h[i]:.3f} mm at d {knots[i]:g} is below "
                          f"{MIN_HEIGHT_MM} mm", d=float(knots[i]), height_mm=float(h[i]))
    r = c["outline"]["tip_round_mm"]
    if r < 0:
        raise TempleError("E_OUTLINE", f"tip_round_mm must be >= 0, got {r}")
    if r > 0:
        top, t_top = _round_corner(top, np.array([0.0, -1.0]), r, "top")
        bot, t_bot = _round_corner(bot, np.array([0.0, 1.0]), r, "bottom")
        cap = float(top[-1, 1] - bot[-1, 1])
        if cap <= MIN_HEIGHT_MM:
            raise TempleError("E_OUTLINE", f"tip_round_mm {r:g} leaves a tip cap of {cap:.3f} mm (rounds overlap)",
                              cap_mm=cap)
    inner = PL(c["inner"], "inner")
    thick = PL(c["thickness"], "thickness")
    for tab in (inner, thick):
        if not tab.covers(d0, d1):
            raise TempleError("E_SPEC", f"{tab.name} must cover the outline's d range [{d0:g}, {d1:g}] (it covers "
                              f"[{tab.d[0]:g}, {tab.d[-1]:g}])", table=tab.name)
    ze = c["zone_end_d"]
    if not (d0 < ze < d1):
        raise TempleError("E_ZONE", f"zone_end_d {ze:g} must lie strictly inside the outline's d range "
                          f"({d0:g}, {d1:g})", zone_end_d=ze, d_range=[d0, d1])
    for tab in (inner, thick):
        m, at = tab.linear_misfit(d0, ze)
        if m > 1e-9:
            raise TempleError("E_ZONE", f"{tab.name} must be linear over the facet zone [{d0:g}, {ze:g}] (flat inner "
                              f"face, planar base): its knot at d {at:g} is {m:.3g} mm off the line", table=tab.name,
                              knot_d=at, misfit_mm=m)
    for k in np.unique(np.r_[knots, inner.d[(inner.d >= d0) & (inner.d <= d1)],
                             thick.d[(thick.d >= d0) & (thick.d <= d1)]]):
        if thick(k) <= 0:
            raise TempleError("E_TOO_THIN", f"thickness {thick(k):.3f} mm at d {k:g}", at=[float(k), None],
                              thickness_mm=float(thick(k)), t_min_mm=c["options"]["t_min_mm"], facet="base",
                              defining_points=[])
    nodes = {k: (v[0], v[1], v[2]) for k, v in c["nodes"].items()}
    ps = ParsedSpec(canonical=c, top=top, bottom=bot, d0=d0, d1=d1, inner=inner, thickness=thick, zone_end=ze,
                    nodes=nodes, facets=c["facets"], chamfers=c["chamfers"], options=c["options"], hinge=c["hinge"],
                    roll_mm=c["roll_mm"], sha256=spec_sha256(spec))
    _check_chamfers(ps)
    if c["hinge"]["opening_deg"] != 0.0 and c["hinge"]["axis_xz_mm"] is None:
        raise TempleError("E_SPEC", "hinge.opening_deg != 0 needs hinge.axis_xz_mm (the vertical hinge axis)")
    return ps


def chamfer_width(ch: dict, d):
    """Width of a chamfer at d: 0 at both ends, full after taper_mm, linear in between (and 0 outside [d0, d1])."""
    d = np.asarray(d, np.float64)
    a, b = ch["d"]
    t = ch["taper_mm"]
    w = ch["width_mm"]
    if t <= 0:
        out = np.where((d >= a) & (d <= b), w, 0.0)
    else:
        out = np.interp(d, [a, a + t, b - t, b], [0.0, w, w, 0.0], left=0.0, right=0.0)
    return out if out.ndim else float(out)


def chamfer_breaks(ch: dict) -> list:
    a, b = ch["d"]
    t = ch["taper_mm"]
    return sorted({a, b} | ({a + t, b - t} if t > 0 else set()))


def _check_chamfers(ps: ParsedSpec) -> None:
    for i, ch in enumerate(ps.chamfers):
        a, b = ch["d"]
        if not (ps.d0 <= a < b <= ps.d1):
            raise TempleError("E_CHAMFER", f"chamfers[{i}].d [{a:g}, {b:g}] must lie inside the outline's d range "
                              f"[{ps.d0:g}, {ps.d1:g}] with d0 < d1", chamfer=i)
        if ch["width_mm"] <= 0 or ch["depth_mm"] <= 0:
            raise TempleError("E_CHAMFER", f"chamfers[{i}]: width_mm and depth_mm must be > 0", chamfer=i)
        if ch["taper_mm"] < 0 or ch["taper_mm"] > (b - a) / 2 + 1e-12:
            raise TempleError("E_CHAMFER", f"chamfers[{i}].taper_mm {ch['taper_mm']:g} must be in [0, (d1 - d0) / 2]",
                              chamfer=i)
        for j, other in enumerate(ps.chamfers[:i]):
            if other["edge"] == ch["edge"] and other["d"][0] < b and a < other["d"][1]:
                raise TempleError("E_CHAMFER", f"chamfers[{j}] and [{i}] overlap on the {ch['edge']} edge",
                                  chamfers=[j, i])
    if not ps.chamfers:
        return
    knots = np.unique(np.r_[ps.top[:, 0], ps.bottom[:, 0],
                            [d for ch in ps.chamfers for d in chamfer_breaks(ch)]])
    h = ps.y_top(knots) - ps.y_bot(knots)
    wt = sum(chamfer_width(ch, knots) for ch in ps.chamfers if ch["edge"] == "top")
    wb = sum(chamfer_width(ch, knots) for ch in ps.chamfers if ch["edge"] == "bottom")
    left = h - np.asarray(wt) - np.asarray(wb)
    if np.any(left < MIN_HEIGHT_MM):
        i = int(np.argmin(left))
        raise TempleError("E_CHAMFER", f"chamfer widths leave {left[i]:.3f} mm of the {h[i]:.3f} mm outline height "
                          f"at d {knots[i]:g}", d=float(knots[i]), height_mm=float(h[i]))
