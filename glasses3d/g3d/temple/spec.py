"""Spec of g3d.faceted_temple v2 (plan/WP6_FACETS.md §2.1): schema "g3d.temple/2", defaults, refusal and warning
codes, the canonical form (and its sha256), knot canonicalisation, and validation into a ParsedSpec. numpy only.

Spec keys (JSON):
    schema    "g3d.temple/2"
    hinge     {z_mm, axis_xz_mm | null, opening_deg}: d = z_mm - z at opening 0; the opening (deg, + = tip outward)
              turns the temple about the vertical axis through (axis_x, axis_z). A null axis is filled by the Blender
              layer from the knuckles; the core needs one only for a non-zero opening.
    outline   {top: [[d, y]], bottom: [[d, y]], tip_round_mm}: d strictly increasing, first d shared (hinge-end cap)
              and last d shared (tip cap); tip_round_mm rounds the two tip corners
    inner     [[d, x_in]]: the vertical inner face, piecewise linear, any shape
    base      {top: [[d, t]], bottom: [[d, t]]}: base thickness at the top and bottom outline edges; between them the
              thickness is linear in y (a wedge)
    nodes     {name: [d, y, t]}: t = mm thickness from the inner face (X = x_in(d) + t), null (solved from the facet
              planes) or "base" (the base thickness at (d, y)). Virtual nodes outside the outline are allowed.
    facets    {label: {poly: [nodes], plane: "nodes" | {crease: [n1, n2], rise_per_mm}, fade: null | {d0, d1}}}
              (a bare list = poly with plane "nodes" and no fade). rise_per_mm = mm of world X gained per mm moved
              perpendicular from the crease into the facet (negative falls toward the inner face). A faded facet is
              (1 - s) plane + s base with s = smoothstep((d - d0) / (d1 - d0)): the plane at d0, the base at d1.
              Facets with different fades (or none) may share creases: a shared vertex takes the least-faded one. Where
              that holds a faded facet at least FADE_HOLD_MIN_MM from its own fade and one of its triangles tilts more
              than FADE_CLIFF_DEG off its own fade (a wall inside one row of triangles), the build refuses
              E_FADE_CLIFF. A fade that moves none of its facet's vertices FADE_HOLD_MIN_MM warns W_FADE_INERT.
    creases   [{facets: [f1, f2 | "base"], shape: "ridge" | "valley"}]: declared crease shapes, checked after the build
    chamfers  [{edge: top | bottom, d: [d0, d1], width_mm, depth_mm, taper_mm?}] (v1: a band cut along an outline edge)
    roll_mm   core edge rounding (8 stacked quarter-arc chamfer sub-bands along both outline edges); 0 = crisp
    options   t_min_mm 1.0, station_step_mm 2.0, planar_tol_mm 1e-6, kink_sharp_deg 25, border_band_mm 2.0,
              snap_mm 0.05 (0 = no snapping)

Extension: every PL table (inner, base.top, base.bottom) extends linearly beyond its end segments, so a virtual node is
never clamped; the outline chains extend the same way when a "base" node outside the outline needs y_top / y_bot.

Knot canonicalisation (parse): every table knot (outline, inner, base), node d and fade d0 / d1 within snap_mm of an
earlier value snaps onto it (outline knots first, then the other tables, nodes, fades; ascending d within a class), so
the build sees one shared d set. Two knots of one table that would share a d are refused (E_SHORT_EDGE). Nodes within
snap_mm (vertically) of the top or bottom outline edge are re-snapped onto it. A facet edge that crosses the outline
within snap_mm of a station source is resolved by moving the outline knot on that chain onto the crossing (or moving
another table's knot to its d); a node, fade, chamfer end or second crossing there cannot move and is E_SHORT_EDGE.
Every snap is reported (ParsedSpec.snaps); ParsedSpec.snapped is the snapped canonical spec (a fixed point of parse).

The spec sha is the sha256 of the canonical JSON (before snapping). Every refusal is a TempleError(code, message,
**fields): nothing is built, and the fields name the facet, node, point and numbers that failed.
"""
from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
from dataclasses import dataclass, field

import numpy as np

SCHEMA = "g3d.temple/2"

REFUSALS = ("E_SPEC", "E_TARGET", "E_MULTI_MATERIAL", "E_OUTLINE", "E_OUTLINE_NOT_SINGLE_VALUED",
            "E_OUTLINE_NOT_SIMPLE", "E_SHORT_EDGE", "E_FACET_OUTSIDE", "E_FACET_OVERLAP", "E_RELIEF_STEP",
            "E_FACET_UNDERDETERMINED", "E_FACET_NOT_PLANAR", "E_ILL_CONDITIONED", "E_TOO_THIN",
            "E_CHAMFER", "E_ROLL", "E_FADE_CLIFF", "E_CREASE_SHAPE", "E_TRIANGULATE", "E_NOT_CLOSED")
WARNINGS = ("W_CONDITIONING", "W_FLAT_CREASE", "W_BORDER_RAMP", "W_FADE_HOLD", "W_FADE_INERT",
            "W_ARM_NORMAL_SPREAD", "W_DECAL_SPANS_CREASE", "W_HINGE_LEAF", "W_KNUCKLE_PENETRATION")

DEFAULT_OPTIONS = {"t_min_mm": 1.0, "station_step_mm": 2.0, "planar_tol_mm": 1e-6, "kink_sharp_deg": 25.0,
                   "border_band_mm": 2.0, "snap_mm": 0.05}
ILL_CONDITIONED_DEG_PER_MM = 10.0       # E_ILL_CONDITIONED
CONDITIONING_WARN_DEG_PER_MM = 6.0      # W_CONDITIONING
FLAT_CREASE_DEG = 2.0                   # a crease pair below this is "flat" (W_FLAT_CREASE; a declaration is refused)
SHARP_DIHEDRAL_DEG = 6.0                # faded, ramp and chamfer edges are sharp at or above this dihedral
CREASE_STANDS_DEG = 0.5                 # a crease edge beside a faded cell is sharp while its design dihedral >= this
FADE_ZERO = 1e-12                       # a fade weight below this is 0 (the face is a facet cell, its values the plane)
FADE_CLIFF_DEG = 25.0                   # E_FADE_CLIFF: a held faded triangle tilted more than this off its own fade
FADE_HOLD_WARN_DEG = 10.0               # W_FADE_HOLD: the largest such tilt at or above this (one receipt line)
FADE_HOLD_MIN_MM = 0.05                 # the smallest hold (built minus the faded facet's own value) that counts
#                                         (E_FADE_CLIFF / W_FADE_HOLD) and the smallest move a fade must make (else
#                                         W_FADE_INERT): the photos show the temple at ~3-8 px/mm, so 0.05 mm is
#                                         0.15-0.4 px, well under one pixel; a smaller step cannot be seen, and a
#                                         large tilt there is a micron-wide sliver triangle's, not a wall
BORDER_RAMP_WARN_DEG = 8.0              # W_BORDER_RAMP
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
ROLL_BANDS = 8                          # core roll: stacked chamfer sub-bands per quarter arc
ROLL_HEIGHT_SHARE = 0.45                # roll_mm <= 0.45 x outline height - chamfer widths


class TempleError(ValueError):
    """A refusal: nothing is built or written. `fields` name what failed (facet, node, point, numbers)."""

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


def _keys(d, allowed, where: str):
    bad = sorted(set(d) - set(allowed))
    if bad:
        raise TempleError("E_SPEC", f"unknown {where} keys {bad}; known: {list(allowed)}", unknown=bad, where=where)


TOP_KEYS = ("schema", "hinge", "outline", "inner", "base", "nodes", "facets", "creases", "chamfers", "roll_mm",
            "options")
REQUIRED_KEYS = ("hinge", "outline", "inner", "base", "nodes", "facets")


def canonical(spec: dict) -> dict:
    """The canonical spec: every key present with its default, numbers as float, the facet shorthand expanded, nodes,
    facets and creases sorted. Raises TempleError(E_SPEC) on a malformed spec (types, unknown keys, unknown names)."""
    if not isinstance(spec, dict):
        raise TempleError("E_SPEC", f"the spec must be a JSON object, got {type(spec).__name__}")
    schema = spec.get("schema", SCHEMA)
    if schema != SCHEMA:
        hint = " (convert a g3d.temple/1 spec with g3d.temple.convert.v1_to_v2)" if schema == "g3d.temple/1" else ""
        raise TempleError("E_SPEC", f"schema must be {SCHEMA!r}, got {schema!r}{hint}", schema=schema)
    _keys(spec, TOP_KEYS, "spec")
    for k in REQUIRED_KEYS:
        if k not in spec:
            raise TempleError("E_SPEC", f"missing spec key {k!r}", missing=k)
    h = spec["hinge"]
    if not isinstance(h, dict) or "z_mm" not in h:
        raise TempleError("E_SPEC", "hinge must be {z_mm, axis_xz_mm, opening_deg}")
    _keys(h, ("z_mm", "axis_xz_mm", "opening_deg"), "hinge")
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
    _keys(o, ("top", "bottom", "tip_round_mm"), "outline")
    if "top" not in o or "bottom" not in o:
        raise TempleError("E_SPEC", "outline needs top and bottom chains")
    outline = {"top": _pairs(o["top"], "outline.top"), "bottom": _pairs(o["bottom"], "outline.bottom"),
               "tip_round_mm": _num(o.get("tip_round_mm", 0.0), "outline.tip_round_mm")}
    b = spec["base"]
    if not isinstance(b, dict) or set(b) != {"top", "bottom"}:
        raise TempleError("E_SPEC", "base must be {top: [[d, t]], bottom: [[d, t]]}")
    base = {"top": _pairs(b["top"], "base.top"), "bottom": _pairs(b["bottom"], "base.bottom")}
    nodes_in = spec["nodes"]
    if not isinstance(nodes_in, dict):
        raise TempleError("E_SPEC", "nodes must be {name: [d, y, t | null | \"base\"]}")
    nodes = {}
    for name in sorted(nodes_in, key=str):
        v = nodes_in[name]
        if not isinstance(name, str) or not name or name == "base":
            raise TempleError("E_SPEC", f"node names must be non-empty strings other than 'base', got {name!r}")
        if not isinstance(v, (list, tuple)) or len(v) != 3:
            raise TempleError("E_SPEC", f"node {name}: must be [d, y, t | null | \"base\"]", node=name)
        t = v[2]
        if t is not None and t != "base":
            t = _num(t, f"nodes.{name}[2]")
        nodes[name] = [_num(v[0], f"nodes.{name}[0]"), _num(v[1], f"nodes.{name}[1]"), t]
    fac_in = spec["facets"]
    if not isinstance(fac_in, dict):
        raise TempleError("E_SPEC", "facets must be {label: {poly, plane, fade}}")
    facets = {}
    for label in sorted(fac_in, key=str):
        f = fac_in[label]
        if not isinstance(label, str) or not label or label == "base" or "|" in label or "/" in label:
            raise TempleError("E_SPEC", f"facet labels must be non-empty strings other than 'base' without '|' or "
                              f"'/', got {label!r}")
        if isinstance(f, (list, tuple)):
            f = {"poly": list(f)}
        if not isinstance(f, dict) or "poly" not in f:
            raise TempleError("E_SPEC", f"facet {label}: must be {{poly, plane, fade}} or a list of nodes", facet=label)
        _keys(f, ("poly", "plane", "fade"), f"facet {label}")
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
        fade = f.get("fade")
        if fade is not None:
            if not isinstance(fade, dict) or set(fade) != {"d0", "d1"}:
                raise TempleError("E_SPEC", f"facet {label}: fade must be null or {{d0, d1}}", facet=label)
            fade = {"d0": _num(fade["d0"], f"facets.{label}.fade.d0"),
                    "d1": _num(fade["d1"], f"facets.{label}.fade.d1")}
            if fade["d0"] == fade["d1"]:
                raise TempleError("E_SPEC", f"facet {label}: fade d0 and d1 must differ", facet=label)
        facets[label] = {"poly": [str(n) for n in poly], "plane": plane, "fade": fade}
    creases, seen = [], set()
    cin = spec.get("creases", []) or []
    if not isinstance(cin, (list, tuple)):
        raise TempleError("E_SPEC", "creases must be a list of {facets: [f1, f2 | 'base'], shape}")
    for i, c in enumerate(cin):
        if not isinstance(c, dict) or set(c) != {"facets", "shape"}:
            raise TempleError("E_SPEC", f"creases[{i}] must be {{facets: [f1, f2 | 'base'], shape}}", crease=i)
        pair = c["facets"]
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or not all(isinstance(x, str) for x in pair):
            raise TempleError("E_SPEC", f"creases[{i}].facets must name two facets (or a facet and 'base')", crease=i)
        a, b = pair
        if a == "base":
            a, b = b, a
        if a not in facets or (b != "base" and b not in facets) or a == b:
            raise TempleError("E_SPEC", f"creases[{i}].facets {list(pair)}: unknown or repeated facet", crease=i,
                              facets=list(pair))
        if b != "base":
            a, b = sorted((a, b))
        if c["shape"] not in ("ridge", "valley"):
            raise TempleError("E_SPEC", f"creases[{i}].shape must be 'ridge' or 'valley', got {c['shape']!r}",
                              crease=i)
        if (a, b) in seen:
            raise TempleError("E_SPEC", f"crease {a}|{b} is declared twice", facets=[a, b])
        seen.add((a, b))
        creases.append({"facets": [a, b], "shape": c["shape"]})
    creases.sort(key=lambda c: c["facets"])
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
    _keys(oin, DEFAULT_OPTIONS, "options")
    for k, v in oin.items():
        opts[k] = _num(v, f"options.{k}")
    for k in ("t_min_mm", "station_step_mm", "planar_tol_mm", "border_band_mm"):
        if opts[k] <= 0:
            raise TempleError("E_SPEC", f"options.{k} must be > 0, got {opts[k]}", option=k)
    if opts["snap_mm"] < 0:
        raise TempleError("E_SPEC", f"options.snap_mm must be >= 0, got {opts['snap_mm']}", option="snap_mm")
    if not 0 < opts["kink_sharp_deg"] < 180:
        raise TempleError("E_SPEC", f"options.kink_sharp_deg must be in (0, 180), got {opts['kink_sharp_deg']}",
                          option="kink_sharp_deg")
    roll = _num(spec.get("roll_mm", 0.0), "roll_mm")
    if roll < 0:
        raise TempleError("E_SPEC", f"roll_mm must be >= 0, got {roll}")
    return {"schema": SCHEMA, "hinge": hinge, "outline": outline, "inner": _pairs(spec["inner"], "inner"),
            "base": base, "nodes": nodes, "facets": facets, "creases": creases, "chamfers": chamfers, "roll_mm": roll,
            "options": opts}


def canonical_json(spec: dict) -> str:
    """Compact, key-sorted JSON of the canonical spec (floats in Python's round-trip repr)."""
    return json.dumps(canonical(spec), sort_keys=True, separators=(",", ":"), allow_nan=False)


def spec_sha256(spec: dict) -> str:
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------------- piecewise linear
class PL:
    """A piecewise-linear table [[d, v]] (d strictly increasing), extended linearly beyond its end segments (one knot:
    constant). np.interp alone would clamp, which moves virtual nodes."""

    def __init__(self, pairs, name: str):
        a = np.asarray(pairs, np.float64).reshape(-1, 2)
        if len(a) < 1:
            raise TempleError("E_SPEC", f"{name} is empty")
        if len(a) > 1 and np.any(np.diff(a[:, 0]) <= 0):
            i = int(np.nonzero(np.diff(a[:, 0]) <= 0)[0][0])
            raise TempleError("E_SPEC", f"{name}: d must strictly increase (knot {i + 1} at d {a[i + 1, 0]:g} after "
                              f"{a[i, 0]:g})", table=name, knot=i + 1)
        self.d, self.v, self.name = a[:, 0].copy(), a[:, 1].copy(), name
        if len(a) > 1:
            self.s0 = (self.v[1] - self.v[0]) / (self.d[1] - self.d[0])
            self.s1 = (self.v[-1] - self.v[-2]) / (self.d[-1] - self.d[-2])
        else:
            self.s0 = self.s1 = 0.0

    def __call__(self, d):
        x = np.asarray(d, np.float64)
        if len(self.d) == 1:
            out = np.full(x.shape, self.v[0])
        else:
            out = np.interp(x, self.d, self.v)
            out = np.where(x < self.d[0], self.v[0] + self.s0 * (x - self.d[0]), out)
            out = np.where(x > self.d[-1], self.v[-1] + self.s1 * (x - self.d[-1]), out)
        return out if out.ndim else float(out)

    def slope(self, d0: float, d1: float) -> float:
        return (self(d1) - self(d0)) / (d1 - d0)

    def knots_inside(self, d0: float, d1: float) -> np.ndarray:
        return self.d[(self.d > d0 + 1e-9) & (self.d < d1 - 1e-9)]


def smoothstep(x):
    x = np.clip(np.asarray(x, np.float64), 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


# --------------------------------------------------------------------------------------------- parsed spec
@dataclass
class ParsedSpec:
    canonical: dict                 # the spec as written (canonical form; its sha is sha256)
    snapped: dict                   # the canonical spec after knot canonicalisation (what is built)
    top: np.ndarray                 # (n, 2) (d, y) after tip rounding
    bottom: np.ndarray
    d0: float
    d1: float
    inner: PL
    t_top: PL
    t_bot: PL
    nodes: dict                     # name -> (d, y, t: float | None | "base")
    facets: dict                    # label -> {"poly", "plane", "fade"}
    creases: list
    chamfers: list
    options: dict
    hinge: dict
    roll_mm: float
    sha256: str = ""
    snaps: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def _chain(self, chain: np.ndarray, d):
        x = np.asarray(d, np.float64)
        out = np.interp(x, chain[:, 0], chain[:, 1])
        s0 = (chain[1, 1] - chain[0, 1]) / (chain[1, 0] - chain[0, 0])
        s1 = (chain[-1, 1] - chain[-2, 1]) / (chain[-1, 0] - chain[-2, 0])
        out = np.where(x < chain[0, 0], chain[0, 1] + s0 * (x - chain[0, 0]), out)
        out = np.where(x > chain[-1, 0], chain[-1, 1] + s1 * (x - chain[-1, 0]), out)
        return out if out.ndim else float(out)

    def y_top(self, d):
        return self._chain(self.top, d)

    def y_bot(self, d):
        return self._chain(self.bottom, d)

    def base_t(self, d, y):
        """Base thickness at (d, y): t_bot + (t_top - t_bot) (y - y_bot) / (y_top - y_bot), linear in y (extended)."""
        d, y = np.asarray(d, np.float64), np.asarray(y, np.float64)
        yb, yt = self.y_bot(d), self.y_top(d)
        h = np.asarray(yt - yb)
        if np.any(h <= 1e-9):
            raise TempleError("E_SPEC", "a base value is needed where the outline (extended) has no height",
                              d=np.asarray(d)[h <= 1e-9].ravel()[:3].tolist() if np.ndim(d) else float(d))
        out = self.t_bot(d) + (self.t_top(d) - self.t_bot(d)) * (y - yb) / h
        return out if np.ndim(out) else float(out)

    def x_base(self, d, y):
        """Base outer surface X = x_in(d) + base_t(d, y)."""
        return self.inner(d) + self.base_t(d, y)

    def node_X(self, name: str):
        """World X of a node's given value (a number t or "base"); None for a solved node."""
        d, y, t = self.nodes[name]
        if t is None:
            return None
        return float(self.x_base(d, y)) if t == "base" else float(self.inner(d) + t)

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


def _outline(c: dict) -> tuple[np.ndarray, np.ndarray]:
    """Validated outline chains after tip rounding (v1 rules)."""
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
        top, _ = _round_corner(top, np.array([0.0, -1.0]), r, "top")
        bot, _ = _round_corner(bot, np.array([0.0, 1.0]), r, "bottom")
        cap = float(top[-1, 1] - bot[-1, 1])
        if cap <= MIN_HEIGHT_MM:
            raise TempleError("E_OUTLINE", f"tip_round_mm {r:g} leaves a tip cap of {cap:.3f} mm (rounds overlap)",
                              cap_mm=cap)
    return top, bot


# --------------------------------------------------------------------------------------------- knot canonicalisation
TABLES = ("outline.top", "outline.bottom", "inner", "base.top", "base.bottom")


def _table(c: dict, name: str) -> list:
    a, _, b = name.partition(".")
    if a == "outline":
        return c["outline"][b]
    if a == "base":
        return c["base"][b]
    return c["inner"]


def _d_entries(c: dict) -> list:
    """(class, d, ref) of every given d value: 0 outline knots, 1 other table knots, 2 node d, 3 fade ends."""
    out = []
    for name in TABLES:
        cls = 0 if name.startswith("outline") else 1
        out += [(cls, p[0], (name, i)) for i, p in enumerate(_table(c, name))]
    out += [(2, v[0], ("node", n)) for n, v in c["nodes"].items()]
    for label, f in c["facets"].items():
        if f["fade"] is not None:
            out += [(3, f["fade"][k], ("fade", label, k)) for k in ("d0", "d1")]
    return out


def _ref_name(ref) -> str:
    if ref[0] in TABLES:
        return f"{ref[0]}[{ref[1]}]"
    if ref[0] == "node":
        return f"node {ref[1]}"
    if ref[0] == "fade":
        return f"facet {ref[1]} fade.{ref[2]}"
    if ref[0] == "chamfer":
        return f"chamfers[{ref[1]}] break"
    return f"{ref[1]} tip-round point"


def _set_d(c: dict, ref, d: float) -> None:
    if ref[0] in TABLES:
        _table(c, ref[0])[ref[1]][0] = d
    elif ref[0] == "node":
        c["nodes"][ref[1]][0] = d
    else:
        c["facets"][ref[1]]["fade"][ref[2]] = d


def _snap_d(c: dict, snap: float, snaps: list) -> None:
    """Greedy single pass: each value (class order, then ascending d) joins the nearest representative within snap,
    else becomes one. Two knots of one table on one representative: E_SHORT_EDGE."""
    reps: list[float] = []
    used: dict = {}
    for cls, d, ref in sorted(_d_entries(c), key=lambda e: (e[0], e[1], repr(e[2]))):
        k = bisect.bisect_left(reps, d)
        best = None
        for j in (k - 1, k):
            if 0 <= j < len(reps) and abs(reps[j] - d) <= snap and (best is None or abs(reps[j] - d) < abs(best - d)):
                best = reps[j]
        if best is None:
            best = d
            bisect.insort(reps, d)
        if ref[0] in TABLES:
            key = (ref[0], best)
            if key in used:
                i0 = used[key]
                raise TempleError("E_SHORT_EDGE", f"{ref[0]} knots {i0} and {ref[1]} lie within snap_mm {snap:g} of "
                                  f"d {best:g}: remove one or move it at least {snap:g} mm away", table=ref[0],
                                  knots=[i0, ref[1]], d=[_table(c, ref[0])[i0][0], d], snap_mm=snap)
            used[key] = ref[1]
        if best != d:
            _set_d(c, ref, best)
            snaps.append({"what": _ref_name(ref), "field": "d", "from": d, "to": best})
    for name in TABLES:
        t = _table(c, name)
        dd = np.diff([p[0] for p in t])
        if np.any(dd <= 0):
            i = int(np.nonzero(dd <= 0)[0][0])
            raise TempleError("E_SHORT_EDGE", f"{name}: snapping leaves knots {i} and {i + 1} out of order",
                              table=name, knots=[i, i + 1], snap_mm=snap)


def _snap_nodes_onto_outline(c: dict, top, bot, snap: float, snaps: list) -> None:
    d0, d1 = float(top[0, 0]), float(top[-1, 0])
    for name, v in c["nodes"].items():
        d, y = v[0], v[1]
        if not d0 <= d <= d1:
            continue
        yt, yb = float(np.interp(d, top[:, 0], top[:, 1])), float(np.interp(d, bot[:, 0], bot[:, 1]))
        et, eb = abs(y - yt), abs(y - yb)
        target = None
        if et <= snap and et <= eb:
            target = yt
        elif eb <= snap:
            target = yb
        if target is not None and target != y:
            v[1] = target
            snaps.append({"what": f"node {name}", "field": "y", "from": y, "to": target,
                          "note": "re-snapped onto the outline"})


def _facet_crossings(c: dict, top, bot) -> list:
    """(d, y, chain) where a facet edge crosses (or touches) the top or bottom outline edge away from its nodes."""
    from .arrange import clip_segment
    boundary = np.vstack([bot, top[::-1]])
    d0, d1 = float(top[0, 0]), float(top[-1, 0])
    out = []
    for f in c["facets"].values():
        P = [np.array(c["nodes"][n][:2], np.float64) for n in f["poly"]]
        for p, q in zip(P, P[1:] + P[:1]):
            for s0, s1 in clip_segment(p, q, boundary):
                for s in (s0, s1):
                    x = p + s * (q - p)
                    if min(np.hypot(*(x - p)), np.hypot(*(x - q))) <= 1e-6:
                        continue                               # the node itself (inside, or on the outline)
                    if not d0 < x[0] < d1:
                        continue
                    yt, yb = np.interp(x[0], top[:, 0], top[:, 1]), np.interp(x[0], bot[:, 0], bot[:, 1])
                    chain = "top" if abs(x[1] - yt) <= abs(x[1] - yb) else "bottom"
                    if not any(abs(o[0] - x[0]) <= 1e-6 and abs(o[1] - x[1]) <= 1e-6 for o in out):
                        out.append((float(x[0]), float(x[1]), chain))
    return out


def _station_refs(c: dict, top, bot) -> dict:
    """d -> refs of the given values that become stations (outline incl. tip-round points, tables, nodes, fades,
    chamfer breaks) inside the outline's d range."""
    d0, d1 = float(top[0, 0]), float(top[-1, 0])
    refs: dict = {}
    raw = {"outline.top": {p[0] for p in c["outline"]["top"]}, "outline.bottom": {p[0] for p in c["outline"]["bottom"]}}
    for chain, arr in (("outline.top", top), ("outline.bottom", bot)):
        for i, (d, _) in enumerate(arr.tolist()):
            if d0 < d < d1:
                if d in raw[chain]:
                    j = [p[0] for p in c["outline"][chain.split(".")[1]]].index(d)
                    refs.setdefault(d, []).append((chain, j))
                else:
                    refs.setdefault(d, []).append(("tip_round", chain))
    for name in TABLES[2:]:
        for i, p in enumerate(_table(c, name)):
            if d0 < p[0] < d1:
                refs.setdefault(p[0], []).append((name, i))
    for n, v in c["nodes"].items():
        if d0 < v[0] < d1:
            refs.setdefault(v[0], []).append(("node", n))
    for label, f in c["facets"].items():
        if f["fade"] is not None:
            for k in ("d0", "d1"):
                if d0 < f["fade"][k] < d1:
                    refs.setdefault(f["fade"][k], []).append(("fade", label, k))
    for i, ch in enumerate(c["chamfers"]):
        for d in chamfer_breaks(ch):
            if d0 < d < d1:
                refs.setdefault(d, []).append(("chamfer", i))
    return refs


def _snap_crossings(c: dict, snap: float, snaps: list) -> tuple[np.ndarray, np.ndarray]:
    """Resolve facet-edge/outline crossings within snap of another station source (module docstring). Each pass moves
    the knots of every crossing whose station source no earlier crossing of the pass took (a moved knot becomes an
    outline vertex at its crossing, so a pass resolves at least one crossing); the passes are bounded by the first
    pass's crossing count. Returns the final outline chains."""
    passes = None
    k_pass = 0
    while passes is None or k_pass < passes:
        k_pass += 1
        top, bot = _outline(c)
        cross = sorted(_facet_crossings(c, top, bot))
        if passes is None:
            passes = len(cross) + 1
        refs = _station_refs(c, top, bot)
        keys = sorted(refs)
        moved = False
        taken: set = set()
        for i, (dc, yc, chain) in enumerate(cross):
            for dj, yj, _ in cross[i + 1:]:
                if 1e-6 < abs(dj - dc) <= snap:
                    raise TempleError("E_SHORT_EDGE", f"two facet edges cross the outline at d {dc:.4f} and {dj:.4f} "
                                      f"(< snap_mm {snap:g} apart): move a node", at=[[dc, yc], [dj, yj]],
                                      snap_mm=snap)
            k = bisect.bisect_left(keys, dc)
            near = [keys[j] for j in (k - 1, k) if 0 <= j < len(keys) and 1e-6 < abs(keys[j] - dc) <= snap]
            if any(s in taken for s in near):
                continue                                   # its knot moved this pass: judged again on the next one
            for s in near:
                fixed = [r for r in refs[s] if r[0] not in TABLES]
                caps = [r for r in refs[s] if r[0].startswith("outline")
                        and r[1] in (0, len(c["outline"][r[0].split(".")[1]]) - 1)]
                if fixed or caps:
                    what = [_ref_name(r) for r in fixed + caps]
                    raise TempleError("E_SHORT_EDGE", f"a facet edge crosses the {chain} edge at d {dc:.4f}, "
                                      f"{abs(dc - s):.4f} mm from d {s:g} ({what}), which cannot move: move a node "
                                      f"at least snap_mm {snap:g} away", at=[dc, yc], d=s, refs=what, snap_mm=snap)
                for r in refs[s]:
                    row = _table(c, r[0])[r[1]]
                    if r[0] == f"outline.{chain}":
                        snaps.append({"what": _ref_name(r), "field": "d,y", "from": list(row), "to": [dc, yc],
                                      "note": "outline knot moved onto a facet-edge crossing"})
                        row[0], row[1] = dc, yc
                    else:
                        snaps.append({"what": _ref_name(r), "field": "d", "from": row[0], "to": dc,
                                      "note": "knot moved to a facet-edge crossing"})
                        row[0] = dc
                taken.add(s)
                moved = True
        if not moved:
            return top, bot
    raise TempleError("E_SHORT_EDGE", f"facet-edge crossings could not be snapped onto the outline knots in {passes} "
                      f"passes", snap_mm=snap, passes=passes)


def parse(spec: dict) -> ParsedSpec:
    """Validate a spec (raises TempleError with the failing field) and return the ParsedSpec the builder uses."""
    c = canonical(spec)
    _outline(c)                                         # the outline and table rules before any snapping
    for name in TABLES[2:]:
        PL(_table(c, name), name)
    s = copy.deepcopy(c)
    snap = s["options"]["snap_mm"]
    snaps: list = []
    if snap > 0:
        _snap_d(s, snap, snaps)
    top, bot = _outline(s)
    if snap > 0:
        _snap_nodes_onto_outline(s, top, bot, snap, snaps)
        top, bot = _snap_crossings(s, snap, snaps)
    d0, d1 = float(top[0, 0]), float(top[-1, 0])
    inner = PL(s["inner"], "inner")
    t_top = PL(s["base"]["top"], "base.top")
    t_bot = PL(s["base"]["bottom"], "base.bottom")
    nodes = {k: (v[0], v[1], v[2]) for k, v in s["nodes"].items()}
    ps = ParsedSpec(canonical=c, snapped=s, top=top, bottom=bot, d0=d0, d1=d1, inner=inner, t_top=t_top, t_bot=t_bot,
                    nodes=nodes, facets=s["facets"], creases=s["creases"], chamfers=s["chamfers"],
                    options=s["options"], hinge=s["hinge"], roll_mm=s["roll_mm"], sha256=spec_sha256(spec),
                    snaps=snaps)
    _check_chamfers(ps)
    _check_roll_height(ps)
    if s["hinge"]["opening_deg"] != 0.0 and s["hinge"]["axis_xz_mm"] is None:
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
    knots = _height_knots(ps)
    h = ps.y_top(knots) - ps.y_bot(knots)
    wt, wb = chamfer_widths(ps, knots)
    left = h - wt - wb
    if np.any(left < MIN_HEIGHT_MM):
        i = int(np.argmin(left))
        raise TempleError("E_CHAMFER", f"chamfer widths leave {left[i]:.3f} mm of the {h[i]:.3f} mm outline height "
                          f"at d {knots[i]:g}", d=float(knots[i]), height_mm=float(h[i]))


def _height_knots(ps: ParsedSpec) -> np.ndarray:
    return np.unique(np.r_[ps.top[:, 0], ps.bottom[:, 0], [d for ch in ps.chamfers for d in chamfer_breaks(ch)]])


def chamfer_widths(ps: ParsedSpec, d) -> tuple[np.ndarray, np.ndarray]:
    """(top, bottom) total chamfer width at d."""
    d = np.asarray(d, np.float64)
    wt = np.zeros(d.shape) + sum((chamfer_width(ch, d) for ch in ps.chamfers if ch["edge"] == "top"), 0.0)
    wb = np.zeros(d.shape) + sum((chamfer_width(ch, d) for ch in ps.chamfers if ch["edge"] == "bottom"), 0.0)
    return wt, wb


def roll_height_limit(ps: ParsedSpec) -> tuple[float, float]:
    """(limit, at d): the height part of the roll limit, min over outline knots and chamfer breaks of
    ROLL_HEIGHT_SHARE x height - the chamfer widths there."""
    knots = _height_knots(ps)
    wt, wb = chamfer_widths(ps, knots)
    lim = ROLL_HEIGHT_SHARE * (ps.y_top(knots) - ps.y_bot(knots)) - wt - wb
    i = int(np.argmin(lim))
    return float(lim[i]), float(knots[i])


def _check_roll_height(ps: ParsedSpec) -> None:
    r = ps.roll_mm
    if r <= 0:
        return
    lim, at = roll_height_limit(ps)
    if r > lim:
        raise TempleError("E_ROLL", f"roll_mm {r:g} does not fit: {ROLL_HEIGHT_SHARE} x the outline height minus the "
                          f"chamfer widths is {lim:.3f} mm at d {at:g}", roll_mm=r, limit_mm=lim, at_d=at,
                          reason="height")


def roll_profile(r: float) -> tuple[np.ndarray, np.ndarray]:
    """(s, c) of the core roll: ROLL_BANDS + 1 points of the quarter arc c(s) = r - sqrt(r^2 - (r - s)^2) at equal
    angles, s = vertical offset from the outline edge (s ascending: s[0] = 0 at the edge, depth r; s[-1] = r,
    depth 0)."""
    phi = np.linspace(0.0, math.pi / 2.0, ROLL_BANDS + 1)         # 0 at s = r (outer face) ... 90 deg at the edge
    s = r * (1.0 - np.sin(phi))
    cdep = r * (1.0 - np.cos(phi))
    s[0], cdep[0] = r, 0.0
    s[-1], cdep[-1] = 0.0, r
    return s[::-1].copy(), cdep[::-1].copy()
