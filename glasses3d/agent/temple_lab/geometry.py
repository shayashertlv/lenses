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
        what changed between two specs (nodes, facets, creases, chains, scalars), compact; a solved t reads "solve".
    surface_grid / surface_at / crease_edges / crease_pieces / crease_delta / geometry_delta / distinct_heads
        between two BUILT temples (g3d.temple builds, never their specs, labels or node names): the outer surface X
        and its gradient on a (d, y) lattice, the max normal distance |dX| / sqrt(1 + |grad X|^2) over the lattice
        points inside both outlines, the largest outline shift (y_top, y_bot, the end d), and the visible creases:
        each head's built crease edges folding >= FLAT_CREASE_DEG (measured over the border band, crease_pieces) as a
        ridge set and a valley set of (d, y) segments, compared by symmetric Hausdorff distance (crease_delta).
        distinct_heads is the one rule of finish preconditions 1 and 4: >= HEAD_DISTINCT_MM of normal surface
        distance or outline shift (the crease comparison is reported, never decisive). Flat facets, coplanar splits,
        slivers, renames, reordered polygons, corners outside the outline and nudges under 0.2 mm are no change by
        construction.
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
from glasses3d.g3d.temple.spec import FLAT_CREASE_DEG, canonical

CHAINS = ("outline.top", "outline.bottom", "inner", "base.top", "base.bottom")
SCALARS = ("roll_mm", "tip_round_mm")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")
EDITS_MAX = 64
CHAIN_POINTS_MAX = 160
D_LIMIT_MM, Y_LIMIT_MM, T_LIMIT_MM = 400.0, 200.0, 40.0     # engineering bounds of a lab spec, not quality limits
RISE_LIMIT, DELTA_LIMIT_MM, SCALAR_LIMIT_MM = 5.0, 20.0, 10.0
GRID_STEP_MM = 0.5
HEAD_DISTINCT_MM = 0.2          # §4.1 finish preconditions 1 and 4: two heads are distinct when their built temples
                                # differ by >= this normal surface distance or outline shift ...
VARIANT_DISTINCT_MM = 0.3       # ... and precondition 2: variants pairwise >= this (value_mm)
CREASE_MATCH_DEG = 0.5 * FLAT_CREASE_DEG    # a crease piece folding >= FLAT_CREASE_DEG is matched against the other
                                # head's pieces of its shape from this fold on (hysteresis at the visibility threshold)
CREASE_EMPTY_MIN_MM = 2.0       # a crease set against none of its shape differs when it totals this length
CREASE_PIECE_MM = 0.1           # crease_pieces: crease edges are measured in pieces of at most this length
CREASE_SAMPLE_MM = 0.02         # directed_mm: sample spacing along a crease (the value is short by < half of it)
SURFACE_CELL_MM = 1.0           # surface_at: the triangle bucket grid
CREASE_NEAR_MM = 1.0            # directed_mm: candidate segments within this of a chunk of samples
CREASE_CHUNK = 256              # directed_mm: samples per chunk
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
            ta, tb = (_r(t) if isinstance(t, float) else "solve" if t is None else t for t in (t0, t1))
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


def diff_text(diff: dict, limit: int = 360, names: int = DIFF_NAMES_MAX, reset: bool = False) -> str:
    """One compact line of a diff_specs result; a list of more than ``names`` items shows its first names - 1 and
    "+N more". With ``reset`` (reset_layout cleared the parent's layout) the removed items are counted, not named."""
    parts = []
    for key in ("nodes", "facets", "creases"):
        for kind, items in diff.get(key, {}).items():
            sign = {"added": "+", "removed": "-", "changed": "~", "set": "+"}[kind]
            if reset and kind == "removed":
                parts.append(f"-{len(items)} {key if len(items) != 1 else key[:-1]}")
                continue
            shown = items if len(items) <= names else items[:names - 1] + [f"+{len(items) - names + 1} more"]
            parts.append(f"{sign}{key[:-1] if key != 'creases' else 'crease'} " + ", ".join(shown))
    parts += diff.get("chains", []) + diff.get("scalars", [])
    text = "; ".join(parts) or "no change"
    return text if len(text) <= limit else text[:limit - 3] + "..."


# --------------------------------------------------------------------------------------------- deltas
def _tri_gradients(V: np.ndarray, T: np.ndarray) -> np.ndarray:
    """(k, 2) gradient (dX/dd, dX/dy) of each triangle's plane (outer triangles are a graph over (d, y))."""
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    with np.errstate(divide="ignore", invalid="ignore"):
        return -n[:, :2] / n[:, 2:3]


def surface_grid(tb, step: float = GRID_STEP_MM, origin: tuple = (0.0, 0.0)) -> tuple[np.ndarray, np.ndarray,
                                                                                         np.ndarray, np.ndarray]:
    """(d values, y values, X[i_d, i_y], grad[i_d, i_y] = (dX/dd, dX/dy)) of the outer surface (outer and roll
    polygons, which tile the outline in (d, y)) on the lattice origin + k x step, each point from the triangle that
    holds it; NaN outside the outline."""
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
    Gr = np.full((len(ds), len(ys), 2), np.nan)
    grads = _tri_gradients(V, T)
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
            box = (slice(max(ja[t, 0], 0), jb[t, 0] + 1), slice(max(ja[t, 1], 0), jb[t, 1] + 1))
            X[box][inside] = x[inside]
            Gr[box][inside] = grads[t]
    return ds, ys, X, Gr


def _regions(tb) -> np.ndarray:
    """Per polygon, an id of its region (the facet label or "base" before any "/chamfer<i>"), within one build."""
    names: dict = {}
    return np.array([names.setdefault(r.partition("/")[0], len(names)) for r in tb.poly_region], np.int64)


def crease_edges(tb) -> dict:
    """Every BUILT crease edge of the outer surface: an edge between two outer / roll polygons of different regions
    (two facets, or a facet and the base; the chamfer and roll sub-bands of one region are not creases), as
    {"seg": (m, 2, 2) (d, y) ends a -> b, "left", "right": (m,) the region ids (_regions) on either side of a -> b}
    (outer polygons run counter-clockwise in (d, y)). Topology only: how much an edge folds is crease_pieces'."""
    region = _regions(tb)
    outer = [p for p, k in enumerate(tb.poly_kind) if k in OUTER_KINDS]
    polys = [list(tb.polys[p]) for p in outer]
    A = np.fromiter((v for q in polys for v in q), np.int64)
    B = np.fromiter((v for q in polys for v in q[1:] + q[:1]), np.int64)
    P = np.repeat(np.asarray(outer, np.int64), [len(q) for q in polys])
    n = len(tb.V)
    key, rev = A * n + B, B * n + A
    order = np.argsort(key, kind="stable")
    ks = key[order]
    pos = np.minimum(np.searchsorted(ks, rev), len(ks) - 1)
    hit = (ks[pos] == rev) & (A < B)
    A, B, P1, P2 = A[hit], B[hit], P[hit], P[order[pos[hit]]]
    cr = region[P1] != region[P2]
    V = np.asarray(tb.V, float)
    return {"seg": np.stack([V[A[cr], :2], V[B[cr], :2]], axis=1), "left": region[P1[cr]], "right": region[P2[cr]]}


def surface_at(tb, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(X, region id) of the built outer surface (outer and roll polygons) at the (d, y) points P, by barycentric
    lookup in the build's own triangulation (triangles bucketed on a SURFACE_CELL_MM grid); NaN and -1 outside the
    outline. A point on an edge takes either triangle (X is continuous)."""
    V = np.asarray(tb.V, float)
    sel = np.array([k in OUTER_KINDS for k in tb.poly_kind])[np.asarray(tb.tri_poly)]
    T = np.asarray(tb.tris)[sel]
    reg = _regions(tb)[np.asarray(tb.tri_poly)[sel]]
    P = np.asarray(P, float).reshape(-1, 2)
    out, rid = np.full(len(P), np.nan), np.full(len(P), -1, np.int64)
    if not len(T) or not len(P):
        return out, rid
    C = V[T][:, :, :2]
    lo = C.reshape(-1, 2).min(0)
    c0 = np.floor((C.min(axis=1) - lo) / SURFACE_CELL_MM).astype(np.int64)
    c1 = np.floor((C.max(axis=1) - lo) / SURFACE_CELL_MM).astype(np.int64)
    ny = c1[:, 1] - c0[:, 1] + 1
    cnt = (c1[:, 0] - c0[:, 0] + 1) * ny
    tri = np.repeat(np.arange(len(T)), cnt)
    k = np.arange(len(tri)) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    W = int(c1[:, 1].max()) + 2
    cid = (c0[tri, 0] + k // ny[tri]) * W + c0[tri, 1] + k % ny[tri]
    order = np.argsort(cid, kind="stable")
    cid, tri = cid[order], tri[order]
    fin = np.isfinite(P).all(axis=1)
    q = np.floor((np.where(fin[:, None], P, lo) - lo) / SURFACE_CELL_MM).astype(np.int64)
    ok = fin & (q >= 0).all(axis=1) & (q[:, 1] < W)
    qid = np.where(ok, q[:, 0] * W + q[:, 1], -1)
    s0, s1 = np.searchsorted(cid, qid, "left"), np.searchsorted(cid, qid, "right")
    m = np.where(ok, s1 - s0, 0)
    qi = np.repeat(np.arange(len(P)), m)
    ti = tri[np.repeat(s0, m) + np.arange(len(qi)) - np.repeat(np.cumsum(m) - m, m)]
    a, b, c = C[ti, 0], C[ti, 1], C[ti, 2]
    p = P[qi]
    den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        l1 = ((b[:, 1] - c[:, 1]) * (p[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (p[:, 1] - c[:, 1])) / den
        l2 = ((c[:, 1] - a[:, 1]) * (p[:, 0] - c[:, 0]) + (a[:, 0] - c[:, 0]) * (p[:, 1] - c[:, 1])) / den
    l3 = 1.0 - l1 - l2
    inside = (np.abs(den) > 1e-14) & (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)
    X = V[T[ti], 2]
    out[qi[inside]] = (l1 * X[:, 0] + l2 * X[:, 1] + l3 * X[:, 2])[inside]
    rid[qi[inside]] = reg[ti[inside]]
    return out, rid


def crease_pieces(tb) -> dict:
    """The built crease edges (crease_edges) cut into pieces of <= CREASE_PIECE_MM, each with its signed fold in
    degrees (+ ridge, - valley; NaN where it cannot be measured inside the outline). At the piece's midpoint p, across
    the edge (unit m toward its left region), each side's slope is the built surface's secant over the largest of
    s, s / 2, s / 4, s / 8 (s = the spec's border_band_mm) whose end still lies in THAT side's region (else s / 8):
    fold = -(atan((X(p + s+ m) - X(p)) / s+) + atan((X(p - s- m) - X(p)) / s-)). So a facet proud by h meets the base
    with atan(h / s) however the build triangulates its ramp, two facets fold by their dihedral, and a flat (coplanar)
    split folds 0 even beside another crease: a secant never crosses into a third region."""
    ce = crease_edges(tb)
    seg = ce["seg"]
    L = np.hypot(*(seg[:, 1] - seg[:, 0]).T)
    keep = L > 1e-9
    seg, L, left, right = seg[keep], L[keep], ce["left"][keep], ce["right"][keep]
    k = np.maximum(1, np.ceil(L / CREASE_PIECE_MM)).astype(np.int64)
    idx = np.repeat(np.arange(len(seg)), k)
    j = np.arange(len(idx)) - np.repeat(np.cumsum(k) - k, k)
    e = seg[idx, 1] - seg[idx, 0]
    pieces = np.stack([seg[idx, 0] + (j / k[idx])[:, None] * e, seg[idx, 0] + ((j + 1) / k[idx])[:, None] * e], axis=1)
    mid = pieces.mean(axis=1)
    m = np.stack([-e[:, 1], e[:, 0]], axis=1) / L[idx][:, None]
    s = float(tb.spec.options["border_band_mm"])
    scales = (s, 0.5 * s, 0.25 * s, 0.125 * s)
    pts = [mid] + [mid + side * sc * m for side in (1.0, -1.0) for sc in scales]
    X, R = surface_at(tb, np.concatenate(pts))
    X, R = X.reshape(len(pts), len(mid)), R.reshape(len(pts), len(mid))
    slopes = []
    for side, region in ((0, left[idx]), (1, right[idx])):
        sl = np.full(len(mid), np.nan)
        for i, sc in enumerate(scales):
            row = 1 + len(scales) * side + i
            v = (X[row] - X[0]) / sc
            last = i == len(scales) - 1
            sl = np.where(np.isnan(sl) & ((R[row] == region) | last), v, sl)
        slopes.append(sl)
    fold = -np.degrees(np.arctan(slopes[0]) + np.arctan(slopes[1]))
    return {"seg": pieces, "fold": fold, "edge": idx}


def _runs(pieces: dict, mask: np.ndarray) -> np.ndarray:
    """(r, 2, 2) the pieces in mask merged into maximal runs of consecutive pieces of one edge (collinear)."""
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return np.zeros((0, 2, 2))
    start = np.ones(len(idx), bool)
    start[1:] = (np.diff(idx) != 1) | (np.diff(pieces["edge"][idx]) != 0)
    first = np.nonzero(start)[0]
    last = np.r_[first[1:], len(idx)] - 1
    return np.stack([pieces["seg"][idx[first], 0], pieces["seg"][idx[last], 1]], axis=1)


def _length(S: np.ndarray) -> float:
    return float(np.hypot(*(S[:, 1] - S[:, 0]).T).sum()) if len(S) else 0.0


def _samples(S: np.ndarray, step: float) -> np.ndarray:
    """Points every <= step mm along each segment of S, both ends included."""
    L = np.hypot(*(S[:, 1] - S[:, 0]).T)
    k = np.maximum(1, np.ceil(L / step)).astype(np.int64)
    idx = np.repeat(np.arange(len(S)), k + 1)
    start = np.repeat(np.cumsum(k + 1) - (k + 1), k + 1)
    t = (np.arange(len(idx)) - start) / k[idx]
    return S[idx, 0] + t[:, None] * (S[idx, 1] - S[idx, 0])


def _point_segment_mm(P: np.ndarray, S: np.ndarray) -> np.ndarray:
    """(len(P), len(S)) distances from the points P to the segments S."""
    a = S[None, :, 0]
    e = S[None, :, 1] - a
    w = P[:, None, :] - a
    t = np.clip((w * e).sum(-1) / np.maximum((e * e).sum(-1), 1e-30), 0.0, 1.0)
    return np.hypot(w[..., 0] - t * e[..., 0], w[..., 1] - t * e[..., 1])


def directed_mm(S: np.ndarray, T: np.ndarray, step: float = CREASE_SAMPLE_MM) -> float:
    """The directed Hausdorff distance from the segments S to the segments T (non-empty), (d, y) mm: the largest
    distance from a point of S (sampled every <= step mm, so short of the exact value by < step / 2) to T. A chunk of
    nearby samples is measured against the segments of T within CREASE_NEAR_MM of its box; a sample farther than that
    from all of them is measured against all of T (exact either way)."""
    mid = S.mean(axis=1)
    P = _samples(S[np.lexsort((mid[:, 1], mid[:, 0]))], step)       # spatially coherent chunks
    lo_t, hi_t = T.min(axis=1), T.max(axis=1)
    out, far = 0.0, []
    for s in range(0, len(P), CREASE_CHUNK):
        Q = P[s:s + CREASE_CHUNK]
        lo, hi = Q.min(0) - CREASE_NEAR_MM, Q.max(0) + CREASE_NEAR_MM
        near = np.nonzero(((lo_t <= hi) & (hi_t >= lo)).all(axis=1))[0]
        d = _point_segment_mm(Q, T[near]).min(axis=1) if len(near) else np.full(len(Q), np.inf)
        ok = d <= CREASE_NEAR_MM
        if ok.any():
            out = max(out, float(d[ok].max()))
        if not ok.all():
            far.append(Q[~ok])
    for F in far:
        for s in range(0, len(F), CREASE_CHUNK):
            out = max(out, float(_point_segment_mm(F[s:s + CREASE_CHUNK], T).min(axis=1).max()))
    return out


def crease_delta(a, b) -> dict:
    """The visible-crease part of §4.1 between two builds: per shape (ridge, valley), each head's set is its built
    crease pieces of that shape folding >= FLAT_CREASE_DEG (crease_pieces); the two sets' symmetric Hausdorff distance
    in (d, y), each set measured against the OTHER head's pieces of that shape from CREASE_MATCH_DEG on (hysteresis: a
    crease at 1.9 deg in one head and 2.1 deg in the other is the same crease). A set against no pieces of its shape
    at all differs (mm None) when it totals >= CREASE_EMPTY_MIN_MM, else counts nothing. Returns {shape: {"mm",
    "length_mm": [a, b]}}: flat facets, coplanar splits, slivers and labels never enter a set; a ridge <-> valley flip
    moves a whole crease from one set to the other."""
    pieces = (crease_pieces(a), crease_pieces(b))
    out = {}
    for shape in ("ridge", "valley"):
        sets = []
        for p in pieces:
            f = np.nan_to_num(p["fold"], nan=0.0) * (1.0 if shape == "ridge" else -1.0)
            sets.append((_runs(p, f >= FLAT_CREASE_DEG), _runs(p, f >= CREASE_MATCH_DEG)))
        (sa, la), (sb, lb) = sets
        dirs = []
        for s, other in ((sa, lb), (sb, la)):
            if not len(s):
                dirs.append(0.0)
            elif not len(other):
                dirs.append(None if _length(s) >= CREASE_EMPTY_MIN_MM else 0.0)
            else:
                dirs.append(directed_mm(s, other))
        out[shape] = {"mm": None if None in dirs else round(max(dirs), 4),
                      "length_mm": [round(_length(sa), 2), round(_length(sb), 2)]}
    return out


def geometry_delta(a, b, step: float = GRID_STEP_MM) -> dict:
    """Between two builds, by their built geometry only (module doc):
        max_dx_mm    max |dX| of the outer surface (roll and chamfers included) on the common lattice (step mm) inside
                     both outlines, each build sampled by barycentric lookup in its own triangulation (surface_grid)
        surface_mm   the max NORMAL distance there: |dX| / sqrt(1 + |g|^2), g the mean of the two surfaces' gradients
                     at the point (a nearly vertical roll band does not magnify an outline move)
        outline_mm   the largest outline shift (y_top / y_bot at every lattice d of the common range, the end d0 / d1)
        value_mm     max(surface_mm, outline_mm) (also finish precondition 2's variant spread)
        creases      crease_delta; crease_mm the largest Hausdorff distance of the two shapes, crease_new the shapes
                     whose crease set appeared or disappeared (mm None)."""
    da, ya, Xa, Ga = surface_grid(a, step)
    db, yb, Xb, Gb = surface_grid(b, step)
    dd = np.intersect1d(np.round(da / step).astype(int), np.round(db / step).astype(int))
    yy = np.intersect1d(np.round(ya / step).astype(int), np.round(yb / step).astype(int))
    max_dx = surface = 0.0
    if len(dd) and len(yy):
        ia = np.searchsorted(np.round(da / step).astype(int), dd)
        ib = np.searchsorted(np.round(db / step).astype(int), dd)
        ja = np.searchsorted(np.round(ya / step).astype(int), yy)
        jb = np.searchsorted(np.round(yb / step).astype(int), yy)
        diff = np.abs(Xa[np.ix_(ia, ja)] - Xb[np.ix_(ib, jb)])
        g = 0.5 * (Ga[np.ix_(ia, ja)] + Gb[np.ix_(ib, jb)])
        normal = diff / np.sqrt(1.0 + (g * g).sum(-1))
        if np.isfinite(diff).any():
            max_dx = float(np.nanmax(diff))
            surface = float(np.nanmax(normal))
    pa, pb = a.spec, b.spec
    lo, hi = max(pa.d0, pb.d0), min(pa.d1, pb.d1)
    outline = max(abs(pa.d0 - pb.d0), abs(pa.d1 - pb.d1))
    if hi > lo:
        ds = np.linspace(lo, hi, max(2, int((hi - lo) / step) + 1))
        outline = max(outline, float(np.abs(pa.y_top(ds) - pb.y_top(ds)).max()),
                      float(np.abs(pa.y_bot(ds) - pb.y_bot(ds)).max()))
    creases = crease_delta(a, b)
    return {"max_dx_mm": round(max_dx, 4), "surface_mm": round(surface, 4), "outline_mm": round(outline, 4),
            "value_mm": round(max(surface, outline), 4), "creases": creases,
            "crease_mm": max([c["mm"] for c in creases.values() if c["mm"] is not None], default=0.0),
            "crease_new": [k for k, c in creases.items() if c["mm"] is None]}


def distinct_heads(delta: dict) -> bool:
    """THE head-distinctness rule (§4.1 finish preconditions 1 and 4), on the built temples only: the outer surface
    moves >= HEAD_DISTINCT_MM along its normal somewhere, or the outline by that much (value_mm). Crease geometry is
    reported (crease_delta) but never decides: every threshold on crease visibility had a knife edge that 0.05 mm
    thickness nudges could cross (pf/gf/vc reviews, 2026-10-11), while a real layout change or ridge <-> valley flip
    moves the surface itself. A rename, a reordered polygon, a corner outside the outline, a flat (flush or coplanar)
    facet or split, and any nudge that moves the surface less than HEAD_DISTINCT_MM never count."""
    return bool(delta["value_mm"] >= HEAD_DISTINCT_MM)


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
