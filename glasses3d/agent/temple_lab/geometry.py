"""Host geometry of the facet lab (plan/WP6_FACETS.md §4.1-4.2): the edits union on g3d.temple/2 specs, spec diffs,
geometry deltas between builds, and revision 0 from the original's export. numpy only, no Blender.

    apply_edits(spec, edits, reset_layout=False) -> (spec, notes)
        the §4.2 edits (dicts of the strict tool union) applied in order to a COPY of a /2 spec, atomically: the first
        failing edit raises EditError(code, message, index); node / facet references are checked after the last
        edit (a node may be deleted together with the facets that use it). reset_layout clears nodes, facets and
        creases first. Deleting a facet deletes its creases (a note says so).
    spec_to_edits(spec) -> edits
        the edits that rebuild spec's layout, chains and scalars from any parent with reset_layout (the converse).
    diff_specs(a, b) -> dict, diff_text(diff) -> str
        what changed between two specs (nodes, facets, creases, chains, scalars), compact.
    surface_grid(tb, step) / geometry_delta(tb_a, tb_b)
        the outer surface X on a (d, y) lattice, and between two builds: max |dX| over their common area, the
        largest outline shift (y_top, y_bot, the end d), and whether the facet set or a built crease shape changed
        (compared by node geometry, never by label: crease_signature; a rename is no change).
    revision_zero(glb_path) -> {spec, report, side, hinge_z, axis, chart_mesh, footprints, body}
        template_from_mesh on the +X temple body of the exported head, with the decal and hinge footprints by
        geometry (the rule of tests/proofs/wp6v2_template_report.py) and the body's chart mesh (section strips).
"""
from __future__ import annotations

import copy
import math
import re

import numpy as np

from glasses3d.g3d.temple import measure as MS
from glasses3d.g3d.temple import template as TT
from glasses3d.g3d.temple.build import OUTER_KINDS
from glasses3d.g3d.temple.spec import canonical

CHAINS = ("outline.top", "outline.bottom", "inner", "base.top", "base.bottom")
SCALARS = ("roll_mm", "tip_round_mm")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")
EDITS_MAX = 64
CHAIN_POINTS_MAX = 160
D_LIMIT_MM, Y_LIMIT_MM, T_LIMIT_MM = 400.0, 200.0, 40.0     # engineering bounds of a lab spec, not quality limits
RISE_LIMIT, DELTA_LIMIT_MM, SCALAR_LIMIT_MM = 5.0, 20.0, 10.0
GRID_STEP_MM = 0.5
HEAD_DISTINCT_MM = 0.2          # §4.1 finish precondition 1: a head differs from its predecessor by >= this ...
VARIANT_DISTINCT_MM = 0.3       # ... and precondition 2: variants pairwise >= this
SIGNATURE_DECIMALS = 2          # facet / crease signatures compare node (d, y) rounded to 0.01 mm (crease_signature)
DIFF_NAMES_MAX = 6              # diff_text: names listed per kind before "+N more" (§4.5 text budget)
TEMPLE_MIN_REACH_MM, TEMPLE_MIN_OFFSET_MM = 60.0, 20.0     # the one body rule (g3d_host/pose.temple_body)


class EditError(ValueError):
    """An edit the lab refuses before any build: code E_EDIT, the 0-based edit index (None for the call)."""

    def __init__(self, message: str, index: int | None = None, code: str = "E_EDIT", **fields):
        super().__init__(message)
        self.code, self.message, self.index, self.fields = code, message, index, fields

    def to_dict(self) -> dict:
        return {"status": "refused", "code": self.code, "edit": self.index, "message": self.message,
                **({"fields": self.fields} if self.fields else {})}


# --------------------------------------------------------------------------------------------- chains
def _chain(spec: dict, name: str) -> list:
    if name == "outline.top":
        return spec["outline"]["top"]
    if name == "outline.bottom":
        return spec["outline"]["bottom"]
    if name == "inner":
        return spec["inner"]
    return spec["base"][name.split(".")[1]]


def _set_chain(spec: dict, name: str, pts: list) -> None:
    if name in ("outline.top", "outline.bottom"):
        spec["outline"][name.split(".")[1]] = pts
    elif name == "inner":
        spec["inner"] = pts
    else:
        spec["base"][name.split(".")[1]] = pts


def _interp(pts: list, d: float) -> float:
    a = np.asarray(pts, float)
    return float(np.interp(d, a[:, 0], a[:, 1]))


def _num(x, what: str, i: int, limit: float | None = None) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(float(x)):
        raise EditError(f"{what} must be a finite number", i)
    if limit is not None and abs(float(x)) > limit:
        raise EditError(f"{what} {float(x):g} is outside +-{limit:g}", i)
    return float(x)


def _name(x, what: str, i: int) -> str:
    if not isinstance(x, str) or not NAME_RE.match(x) or x == "base":
        raise EditError(f"{what} {x!r}: names start with a letter, then up to 23 letters, digits, _ or -, and are "
                        "not 'base'", i)
    return x


def _pair(facets, i: int) -> tuple[str, str]:
    if not isinstance(facets, list) or len(facets) != 2:
        raise EditError("facets must name two facets (or a facet and 'base')", i)
    a, b = facets
    if a == "base":
        a, b = b, a
    a = _name(a, "facet", i)
    if b != "base":
        b = _name(b, "facet", i)
        a, b = sorted((a, b))
    if a == b:
        raise EditError("a crease needs two different facets", i)
    return a, b


def _crease_key(c: dict) -> tuple[str, str]:
    a, b = c["facets"]
    if a == "base":
        a, b = b, a
    return (a, b) if b == "base" else tuple(sorted((a, b)))


def _splice(pts: list, d_from: float, d_to: float, points: list, i: int) -> list:
    if not d_from < d_to:
        raise EditError("splice_chain needs d_from_mm < d_to_mm", i)
    new = []
    for k, p in enumerate(points):
        if not isinstance(p, list) or len(p) != 2:
            raise EditError(f"points[{k}] must be [d, value]", i)
        d, v = _num(p[0], f"points[{k}] d", i, D_LIMIT_MM), _num(p[1], f"points[{k}] value", i, Y_LIMIT_MM)
        if not d_from <= d <= d_to:
            raise EditError(f"points[{k}] d {d:g} lies outside d_from..d_to {d_from:g}..{d_to:g}", i)
        new.append([d, v])
    if any(b[0] <= a[0] for a, b in zip(new, new[1:])):
        raise EditError("points must have strictly increasing d", i)
    out = [p for p in pts if p[0] < d_from] + new + [p for p in pts if p[0] > d_to]
    if len(out) < 2:
        raise EditError("the chain needs at least 2 knots after the splice", i)
    return out


def _offset(pts: list, d_from: float, d_to: float, delta: float, taper: float, i: int) -> list:
    """Add delta x w(d) to the chain, w = 1 over [d_from, d_to] falling linearly to 0 over taper_mm outside; knots
    are inserted at the breakpoints inside the chain (no geometry change by themselves)."""
    if not d_from <= d_to or taper < 0:
        raise EditError("offset_chain needs d_from_mm <= d_to_mm and taper_mm >= 0", i)
    d0, d1 = pts[0][0], pts[-1][0]
    have = [p[0] for p in pts]
    out = [list(p) for p in pts]
    for d in sorted({d_from - taper, d_from, d_to, d_to + taper}):
        if d0 < d < d1 and min(abs(d - h) for h in have) > 1e-9:
            out.append([d, _interp(pts, d)])
            have.append(d)
    out.sort(key=lambda p: p[0])

    def w(d):
        if d_from <= d <= d_to:
            return 1.0
        if taper > 0 and d_from - taper < d < d_from:
            return (d - (d_from - taper)) / taper
        if taper > 0 and d_to < d < d_to + taper:
            return ((d_to + taper) - d) / taper
        return 0.0
    return [[d, v + delta * w(d)] for d, v in out]


# --------------------------------------------------------------------------------------------- edits
def apply_edits(spec: dict, edits: list, *, reset_layout: bool = False) -> tuple[dict, list[str]]:
    """The edits applied to a copy of ``spec`` (module doc). Returns (canonical spec, notes); raises EditError."""
    if not isinstance(edits, list) or len(edits) > EDITS_MAX:
        raise EditError(f"edits must be a list of at most {EDITS_MAX} operations")
    s = copy.deepcopy(canonical(spec))
    if reset_layout:
        s["nodes"], s["facets"], s["creases"] = {}, {}, []
    notes, deleted_nodes, set_facets, set_creases = [], {}, {}, {}
    for i, e in enumerate(edits):
        op = e.get("op") if isinstance(e, dict) else None
        if op == "set_node":
            name = _name(e["name"], "node", i)
            d = _num(e["d_mm"], "d_mm", i, D_LIMIT_MM)
            y = _num(e["y_mm"], "y_mm", i, Y_LIMIT_MM)
            t = e.get("t") or {}
            mode, mm = t.get("mode"), t.get("mm")
            if mode == "mm":
                if mm is None:
                    raise EditError("t.mode 'mm' needs t.mm (thickness from the inner face)", i)
                tv = _num(mm, "t.mm", i, T_LIMIT_MM)
                if tv < 0:
                    raise EditError("t.mm is a thickness from the inner face and cannot be negative", i)
            elif mode in ("base", "solve"):
                if mm is not None:
                    raise EditError(f"t.mode '{mode}' takes t.mm null", i)
                tv = "base" if mode == "base" else None
            else:
                raise EditError("t.mode must be mm, base or solve", i)
            s["nodes"][name] = [d, y, tv]
            deleted_nodes.pop(name, None)
        elif op == "delete_node":
            name = e.get("name")
            if name not in s["nodes"]:
                raise EditError(f"there is no node {name!r}", i)
            del s["nodes"][name]
            deleted_nodes[name] = i
        elif op == "set_facet":
            label = _name(e["label"], "facet", i)
            poly = e.get("poly")
            if not isinstance(poly, list) or not 3 <= len(poly) <= 32 or len(set(poly)) != len(poly):
                raise EditError("poly must list 3..32 distinct node names", i)
            for n in poly:
                _name(n, "node", i)
            plane = e.get("plane") or {}
            fade = e.get("fade")
            if plane.get("mode") == "nodes":
                if plane.get("crease") is not None or plane.get("rise_per_mm") is not None:
                    raise EditError("plane.mode 'nodes' takes crease null and rise_per_mm null", i)
                pl = "nodes"
            elif plane.get("mode") == "crease":
                cr = plane.get("crease")
                if not isinstance(cr, list) or len(cr) != 2 or cr[0] == cr[1] or any(n not in poly for n in cr):
                    raise EditError("plane.crease must name two different corners of poly", i)
                a, b = poly.index(cr[0]), poly.index(cr[1])
                if (a - b) % len(poly) not in (1, len(poly) - 1):
                    raise EditError(f"plane.crease {cr} must be an edge of poly (adjacent corners)", i)
                if plane.get("rise_per_mm") is None:
                    raise EditError("plane.mode 'crease' needs rise_per_mm", i)
                pl = {"crease": [cr[0], cr[1]], "rise_per_mm": _num(plane["rise_per_mm"], "rise_per_mm", i,
                                                                    RISE_LIMIT)}
            else:
                raise EditError("plane.mode must be nodes or crease", i)
            if fade is not None:
                f0 = _num(fade.get("d0_mm"), "fade.d0_mm", i, D_LIMIT_MM)
                f1 = _num(fade.get("d1_mm"), "fade.d1_mm", i, D_LIMIT_MM)
                if f0 == f1:
                    raise EditError("fade d0_mm and d1_mm must differ", i)
                fade = {"d0": f0, "d1": f1}
            s["facets"][label] = {"poly": list(poly), "plane": pl, "fade": fade}
            set_facets[label] = i
        elif op == "delete_facet":
            label = e.get("label")
            if label not in s["facets"]:
                raise EditError(f"there is no facet {label!r}", i)
            del s["facets"][label]
            gone = [c for c in s["creases"] if label in c["facets"]]
            s["creases"] = [c for c in s["creases"] if label not in c["facets"]]
            notes += [f"crease {'|'.join(c['facets'])} deleted with facet {label}" for c in gone]
        elif op == "set_crease":
            pair = _pair(e.get("facets"), i)
            if e.get("shape") not in ("ridge", "valley"):
                raise EditError("shape must be ridge or valley", i)
            s["creases"] = [c for c in s["creases"] if _crease_key(c) != pair]
            s["creases"].append({"facets": list(pair), "shape": e["shape"]})
            set_creases[pair] = i
        elif op == "delete_crease":
            pair = _pair(e.get("facets"), i)
            if not any(_crease_key(c) == pair for c in s["creases"]):
                raise EditError(f"there is no declared crease {'|'.join(pair)}", i)
            s["creases"] = [c for c in s["creases"] if _crease_key(c) != pair]
            set_creases.pop(pair, None)
        elif op in ("splice_chain", "offset_chain"):
            chain = e.get("chain")
            if chain not in CHAINS:
                raise EditError(f"chain must be one of {list(CHAINS)}", i)
            d_from = _num(e.get("d_from_mm"), "d_from_mm", i, D_LIMIT_MM)
            d_to = _num(e.get("d_to_mm"), "d_to_mm", i, D_LIMIT_MM)
            pts = [list(p) for p in _chain(s, chain)]
            if op == "splice_chain":
                pts = e.get("points")
                if not isinstance(pts, list) or len(pts) > CHAIN_POINTS_MAX:
                    raise EditError(f"points must be a list of at most {CHAIN_POINTS_MAX} [d, value]", i)
                _set_chain(s, chain, _splice([list(p) for p in _chain(s, chain)], d_from, d_to, pts, i))
            else:
                delta = _num(e.get("delta_mm"), "delta_mm", i, DELTA_LIMIT_MM)
                taper = _num(e.get("taper_mm"), "taper_mm", i, D_LIMIT_MM)
                _set_chain(s, chain, _offset(pts, d_from, d_to, delta, taper, i))
        elif op == "set_scalar":
            key = e.get("key")
            value = _num(e.get("value"), "value", i, SCALAR_LIMIT_MM)
            if key not in SCALARS or value < 0:
                raise EditError(f"set_scalar key must be one of {list(SCALARS)} with value >= 0", i)
            if key == "roll_mm":
                s["roll_mm"] = value
            else:
                s["outline"]["tip_round_mm"] = value
        else:
            raise EditError(f"unknown op {op!r}", i)
    for label, f in s["facets"].items():                   # references, after the last edit
        for n in f["poly"]:
            if n not in s["nodes"]:
                at = deleted_nodes.get(n, set_facets.get(label))
                why = f"node {n} is deleted but facet {label} still uses it" if n in deleted_nodes else \
                    f"facet {label} uses node {n}, which does not exist"
                raise EditError(why, at)
    for c in s["creases"]:
        for f in c["facets"]:
            if f != "base" and f not in s["facets"]:
                raise EditError(f"crease {'|'.join(c['facets'])} names facet {f}, which does not exist",
                                set_creases.get(_crease_key(c)))
    return canonical(s), notes


def spec_to_edits(spec: dict) -> list[dict]:
    """Edits that rebuild ``spec``'s layout (with reset_layout), chains and scalars from any parent."""
    s = canonical(spec)
    out = []
    for chain in CHAINS:
        pts = _chain(s, chain)
        out.append({"op": "splice_chain", "chain": chain, "d_from_mm": -D_LIMIT_MM, "d_to_mm": D_LIMIT_MM,
                    "points": [list(p) for p in pts]})
    out.append({"op": "set_scalar", "key": "roll_mm", "value": s["roll_mm"]})
    out.append({"op": "set_scalar", "key": "tip_round_mm", "value": s["outline"]["tip_round_mm"]})
    for name, (d, y, t) in s["nodes"].items():
        tv = {"mode": "mm", "mm": t} if isinstance(t, float) else {"mode": "base" if t == "base" else "solve",
                                                                    "mm": None}
        out.append({"op": "set_node", "name": name, "d_mm": d, "y_mm": y, "t": tv})
    for label, f in s["facets"].items():
        pl = f["plane"]
        plane = {"mode": "nodes", "crease": None, "rise_per_mm": None} if pl == "nodes" else \
            {"mode": "crease", "crease": list(pl["crease"]), "rise_per_mm": pl["rise_per_mm"]}
        fade = None if f["fade"] is None else {"d0_mm": f["fade"]["d0"], "d1_mm": f["fade"]["d1"]}
        out.append({"op": "set_facet", "label": label, "poly": list(f["poly"]), "plane": plane, "fade": fade})
    for c in s["creases"]:
        out.append({"op": "set_crease", "facets": list(c["facets"]), "shape": c["shape"]})
    return out


# --------------------------------------------------------------------------------------------- diffs
def _r(x, nd=2):
    return None if x is None else round(float(x), nd) + 0.0


def diff_specs(a: dict, b: dict) -> dict:
    """What changed from spec a to spec b (both canonical-able)."""
    a, b = canonical(a), canonical(b)
    out: dict = {}
    na, nb = a["nodes"], b["nodes"]
    nodes = {"added": sorted(set(nb) - set(na)), "removed": sorted(set(na) - set(nb)), "changed": []}
    for n in sorted(set(na) & set(nb)):
        (d0, y0, t0), (d1, y1, t1) = na[n], nb[n]
        parts = []
        if math.hypot(d1 - d0, y1 - y0) > 1e-9:
            parts.append(f"at {_r(d1)},{_r(y1)}")
        if t0 != t1:
            ta, tb = (_r(t) if isinstance(t, float) else t for t in (t0, t1))
            parts.append(f"t {ta}->{tb}")
        if parts:
            nodes["changed"].append(f"{n} " + " ".join(parts))
    fa, fb = a["facets"], b["facets"]
    facets = {"added": sorted(set(fb) - set(fa)), "removed": sorted(set(fa) - set(fb)),
              "changed": sorted(k for k in set(fa) & set(fb) if fa[k] != fb[k])}
    ca = {_crease_key(c): c["shape"] for c in a["creases"]}
    cb = {_crease_key(c): c["shape"] for c in b["creases"]}
    creases = {"set": [f"{'|'.join(k)} {v}" for k, v in sorted(cb.items()) if ca.get(k) != v],
               "removed": ["|".join(k) for k in sorted(set(ca) - set(cb))]}
    chains = []
    for chain in CHAINS:
        pa, pb = _chain(a, chain), _chain(b, chain)
        if pa != pb:
            ds = sorted({p[0] for p in pa} ^ {p[0] for p in pb} | {q[0] for p, q in zip(pa, pb) if p != q})
            chains.append(f"{chain} d {_r(ds[0], 1)}-{_r(ds[-1], 1)}" if ds else chain)
    scalars = [f"{k} {_r(x)}->{_r(y)}" for k, x, y in (("roll_mm", a["roll_mm"], b["roll_mm"]),
                                                         ("tip_round_mm", a["outline"]["tip_round_mm"],
                                                          b["outline"]["tip_round_mm"])) if x != y]
    for key, val in (("nodes", nodes), ("facets", facets), ("creases", creases)):
        if any(val.values()):
            out[key] = {k: v for k, v in val.items() if v}
    if chains:
        out["chains"] = chains
    if scalars:
        out["scalars"] = scalars
    return out


def diff_text(diff: dict, limit: int = 360, names: int = DIFF_NAMES_MAX) -> str:
    """One compact line of a diff_specs result; a list of more than ``names`` items shows its first names - 1 and
    "+N more" (e.g. the layout reset_layout cleared)."""
    parts = []
    for key in ("nodes", "facets", "creases"):
        for kind, items in diff.get(key, {}).items():
            sign = {"added": "+", "removed": "-", "changed": "~", "set": "+"}[kind]
            shown = items if len(items) <= names else items[:names - 1] + [f"+{len(items) - names + 1} more"]
            parts.append(f"{sign}{key[:-1] if key != 'creases' else 'crease'} " + ", ".join(shown))
    parts += diff.get("chains", []) + diff.get("scalars", [])
    text = "; ".join(parts) or "no change"
    return text if len(text) <= limit else text[:limit - 3] + "..."


# --------------------------------------------------------------------------------------------- deltas
def surface_grid(tb, step: float = GRID_STEP_MM, origin: tuple = (0.0, 0.0)) -> tuple[np.ndarray, np.ndarray,
                                                                                         np.ndarray]:
    """(d values, y values, X[i_d, i_y]) of the outer surface (outer and roll polygons, which tile the outline in
    (d, y)) on the lattice origin + k x step; NaN outside the outline."""
    V = np.asarray(tb.V, float)
    kind = np.array([k in OUTER_KINDS for k in tb.poly_kind])[np.asarray(tb.tri_poly)]
    T = np.asarray(tb.tris)[kind]
    P = V[:, :2]
    lo, hi = P[np.unique(T)].min(0), P[np.unique(T)].max(0)
    i0 = np.ceil((lo - origin) / step).astype(int)
    i1 = np.floor((hi - origin) / step).astype(int)
    ds = origin[0] + step * np.arange(i0[0], i1[0] + 1)
    ys = origin[1] + step * np.arange(i0[1], i1[1] + 1)
    X = np.full((len(ds), len(ys)), np.nan)
    A, B, Cc = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    tmin, tmax = np.minimum(np.minimum(A, B), Cc), np.maximum(np.maximum(A, B), Cc)
    ja = np.ceil((tmin - origin) / step - 1e-9).astype(int) - i0
    jb = np.floor((tmax - origin) / step + 1e-9).astype(int) - i0
    for t in range(len(T)):
        if jb[t, 0] < ja[t, 0] or jb[t, 1] < ja[t, 1]:
            continue
        gd = ds[max(ja[t, 0], 0):jb[t, 0] + 1]
        gy = ys[max(ja[t, 1], 0):jb[t, 1] + 1]
        if not len(gd) or not len(gy):
            continue
        Dg, Yg = np.meshgrid(gd, gy, indexing="ij")
        a, b, c = A[t], B[t], Cc[t]
        den = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(den) < 1e-14:
            continue
        l1 = ((b[1] - c[1]) * (Dg - c[0]) + (c[0] - b[0]) * (Yg - c[1])) / den
        l2 = ((c[1] - a[1]) * (Dg - c[0]) + (a[0] - c[0]) * (Yg - c[1])) / den
        l3 = 1.0 - l1 - l2
        inside = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
        if inside.any():
            x = l1 * V[T[t, 0], 2] + l2 * V[T[t, 1], 2] + l3 * V[T[t, 2], 2]
            sub = X[max(ja[t, 0], 0):jb[t, 0] + 1, max(ja[t, 1], 0):jb[t, 1] + 1]
            sub[inside] = x[inside]
    return ds, ys, X


def facet_key(ps, label: str) -> str:
    """A facet by geometry, never by name: the sorted (d, y) of its polygon's nodes, rounded to 0.01 mm."""
    pts = sorted((round(float(ps.nodes[n][0]), SIGNATURE_DECIMALS) + 0.0,
                  round(float(ps.nodes[n][1]), SIGNATURE_DECIMALS) + 0.0) for n in ps.facets[label]["poly"])
    return ";".join(f"{d:.{SIGNATURE_DECIMALS}f},{y:.{SIGNATURE_DECIMALS}f}" for d, y in pts)


def crease_signature(tb) -> tuple:
    """(facets, built creases) by geometry (§4.1 precondition 1): each facet as its facet_key, each built crease as
    its shape and the keys of its two regions ("base" for the base). Renaming facets or nodes changes neither; a
    change of either is a topology / crease-shape change."""
    keys = {label: facet_key(tb.spec, label) for label in tb.spec.facets}
    facets = tuple(sorted(keys.values()))
    creases = tuple(sorted((c["shape"],) + tuple(sorted(keys.get(r, r) for r in c["regions"]))
                           for c in tb.tables["creases"] if c["kind"] == "crease"))
    return facets, creases


def geometry_delta(a, b, step: float = GRID_STEP_MM) -> dict:
    """Between two builds: max |dX| over the common outline area (lattice step mm), the largest outline shift
    (y_top / y_bot at every lattice d of the common range, and the end d), the topology / crease-shape change, and
    value_mm = max(max_dx_mm, outline_mm)."""
    da, ya, Xa = surface_grid(a, step)
    db, yb, Xb = surface_grid(b, step)
    dd = np.intersect1d(np.round(da / step).astype(int), np.round(db / step).astype(int))
    yy = np.intersect1d(np.round(ya / step).astype(int), np.round(yb / step).astype(int))
    max_dx = 0.0
    if len(dd) and len(yy):
        ia = np.searchsorted(np.round(da / step).astype(int), dd)
        ib = np.searchsorted(np.round(db / step).astype(int), dd)
        ja = np.searchsorted(np.round(ya / step).astype(int), yy)
        jb = np.searchsorted(np.round(yb / step).astype(int), yy)
        diff = np.abs(Xa[np.ix_(ia, ja)] - Xb[np.ix_(ib, jb)])
        if np.isfinite(diff).any():
            max_dx = float(np.nanmax(diff))
    pa, pb = a.spec, b.spec
    lo, hi = max(pa.d0, pb.d0), min(pa.d1, pb.d1)
    outline = max(abs(pa.d0 - pb.d0), abs(pa.d1 - pb.d1))
    if hi > lo:
        ds = np.linspace(lo, hi, max(2, int((hi - lo) / step) + 1))
        outline = max(outline, float(np.abs(pa.y_top(ds) - pb.y_top(ds)).max()),
                      float(np.abs(pa.y_bot(ds) - pb.y_bot(ds)).max()))
    sa, sb = crease_signature(a), crease_signature(b)
    return {"max_dx_mm": round(max_dx, 4), "outline_mm": round(outline, 4),
            "value_mm": round(max(max_dx, outline), 4), "facets_changed": sa[0] != sb[0],
            "creases_changed": sa[1] != sb[1]}


def distinct_heads(delta: dict) -> bool:
    """§4.1 precondition 1: >= HEAD_DISTINCT_MM max |dX| (or outline shift), or a topology / crease-shape change."""
    return delta["value_mm"] >= HEAD_DISTINCT_MM or delta["facets_changed"] or delta["creases_changed"]


def spread_triple(labels: list, pairs: dict) -> list | None:
    """Three labels pairwise >= VARIANT_DISTINCT_MM apart (pairs = {(a, b): value_mm}), or None."""
    n = len(labels)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                trio = (labels[i], labels[j], labels[k])
                if all(pairs.get(tuple(sorted(p)), 0.0) >= VARIANT_DISTINCT_MM
                       for p in ((trio[0], trio[1]), (trio[0], trio[2]), (trio[1], trio[2]))):
                    return list(trio)
    return None


# --------------------------------------------------------------------------------------------- revision 0
def glb_parts(glb_path) -> list[dict]:
    """Every triangle primitive of a GLB: {name, role, V (mm), F}."""
    from glasses3d.g3d_host import glb as GLB
    g = GLB.load(glb_path)
    out = []
    for p in g.primitives:
        if p.mode != 4 or not len(p.triangles):
            continue
        role = (g.doc["nodes"][p.node_index].get("extras") or {}).get("partRole")
        out.append({"name": p.node_name, "role": role, "V": np.asarray(p.positions, float) * 1000.0,
                    "F": np.asarray(p.triangles, np.int64)})
    return out


def temple_body(parts: list, side: int = 1) -> dict:
    """The temple body of one side by geometry: role temple, reaching >= 60 mm back, centred >= 20 mm from x = 0
    on that side; the longest one."""
    cands = []
    for p in parts:
        V = p["V"]
        cx = 0.5 * float(V[:, 0].min() + V[:, 0].max())
        reach = float(np.ptp(V[:, 2]))
        if p["role"] == "temple" and reach >= TEMPLE_MIN_REACH_MM and side * cx >= TEMPLE_MIN_OFFSET_MM:
            cands.append((float(np.ptp(V[:, 2])), p))
    if not cands:
        raise ValueError(f"no temple body on side {side:+d} (role temple, >= {TEMPLE_MIN_REACH_MM:g} mm long)")
    return max(cands, key=lambda c: c[0])[1]


def footprints(parts: list, body: dict, side: int, hinge_z: float, axis, L, Fc) -> dict:
    """Hinge parts (measure.hinge_part_kind) and decals (every vertex within DECAL_NEAR_MM of the body) as (d, y)
    point sets for the chart."""
    hinge, decals = [], []
    for p in parts:
        if p is body or side * float(p["V"][:, 0].mean()) <= 0:
            continue
        lo, hi = p["V"].min(0), p["V"].max(0)
        kind = MS.hinge_part_kind(lo, hi, p["role"], side, axis[0] if axis else None, hinge_z, len(p["V"]))
        Lp = MS.chart_from_world(p["V"], side, hinge_z)
        if kind is not None:
            hinge.append({"name": kind, "points": Lp[:, :2].tolist(), "part": p["name"]})
            continue
        if p["role"] != "temple" or len(p["V"]) > MS.HINGE_MAX_VERTICES:
            continue
        dist = TT.point_mesh_distance(Lp, L, Fc)
        if float(dist.max()) <= MS.DECAL_NEAR_MM:
            decals.append({"name": p["name"].split("|")[-1].strip(), "points": Lp[:, :2].tolist(), "part": p["name"]})
    return {"decals": decals, "hinge": hinge}


def revision_zero(glb_path, side: int = 1) -> dict:
    """Revision 0 of the facet lab: template_from_mesh on the exported head's temple body (module doc)."""
    parts = glb_parts(glb_path)
    body = temple_body(parts, side)
    V, F = body["V"], body["F"]
    hinge_z = float(V[:, 2].max())
    axis = MS.knuckle_axis([(p["V"].min(0), p["V"].max(0), p["role"]) for p in parts], side, hinge_z)
    spec, report = TT.template_from_mesh(V, F, side, hinge_z, axis)
    L, Fc = MS.chart_mesh(V, F, side, hinge_z)
    return {"spec": spec, "report": report, "side": side, "hinge_z": hinge_z, "axis": axis, "body": body["name"],
            "chart_mesh": (L, Fc), "footprints": footprints(parts, body, side, hinge_z, axis, L, Fc)}
