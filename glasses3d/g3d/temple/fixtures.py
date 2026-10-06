"""P0 fixture specs (WP6_DESIGN.md §8) and the Prada topology helper. numpy only.

Fixtures (each a full "g3d.temple/1" spec): no-facet slab, Prada pyramid, valley crease, facet touching the zone end,
top chamfer along the arm, rolled edges, virtual node, solved node, crease + rise F1, analytic prism, 30 deg chamfered
slab; and the refusal cases (near-collinear F1 -> E_ILL_CONDITIONED, F4 thin regression -> E_TOO_THIN, one minimal
spec per refusal code). Fixtures are never edited to make a test pass.

`prada_spec(...)` writes the Prada PRS 17WS topology the photos show (F1 narrow top facet, F2 logo triangle A-B-C with
C virtual below the bottom edge, F3 front-lower, F4 rear-lower running back to the arm) as a VALID spec: the facets
meet the base (the arm) only along their own zero lines, so the spec writer supplies r_A, r_B, r_C, the F1 rise, r_H
and the F4 foot E, and the helper places K (F1's zero line on the top edge) and K' (where F1's and F4's zero lines
meet, behind B) exactly. It is a spec-writing helper (what trace_temple hands Astra); the tool never moves a node.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from .planes import crease_plane
from .spec import SCHEMA

HINGE = {"z_mm": -10.6, "axis_xz_mm": [64.6, -10.1], "opening_deg": 0.0}
# a Prada-like side outline (d, y) mm (WP6 measurement, side photos 05 / 01) used by the generic fixtures
TOP = [[0.0, 10.5], [15.0, 13.43], [20.0, 14.09], [25.0, 14.86], [30.0, 15.46], [35.0, 15.76], [40.0, 15.58],
       [45.0, 14.62], [50.0, 13.96], [60.0, 12.42], [70.0, 10.74], [80.0, 9.10], [90.0, 8.03], [100.0, 6.69],
       [105.0, 5.09], [110.0, 2.98], [115.0, 0.42], [120.0, -3.66], [125.0, -9.5], [130.0, -14.5], [134.0, -18.5],
       [136.5, -20.5]]
BOTTOM = [[0.0, -9.3], [2.0, -9.6], [5.0, -10.55], [10.0, -12.07], [15.0, -13.48], [20.0, -14.74], [25.0, -14.53],
          [30.0, -13.19], [35.0, -11.88], [40.0, -10.58], [45.0, -9.2], [50.0, -7.71], [60.0, -4.7], [70.0, -2.12],
          [80.0, -0.24], [90.0, 0.56], [100.0, 0.09], [105.0, -1.7], [110.0, -4.82], [115.0, -9.99], [120.0, -15.56],
          [125.0, -21.25], [130.0, -26.6], [134.0, -30.0], [136.5, -30.5]]
INNER = [[0.0, 65.5], [65.0, 61.8], [80.0, 61.07], [90.0, 60.34], [100.0, 59.23], [110.0, 56.65], [120.0, 52.31],
         [130.0, 48.66], [136.5, 47.1]]
THICK = [[0.0, 5.0], [65.0, 5.0], [80.0, 5.34], [90.0, 4.87], [100.0, 4.45], [110.0, 4.23], [120.0, 4.01],
         [136.5, 4.28]]
ZONE_END = 65.0


def _yt(d, top=TOP):
    t = np.asarray(top, float)
    return float(np.interp(d, t[:, 0], t[:, 1]))


def _yb(d, bot=BOTTOM):
    b = np.asarray(bot, float)
    return float(np.interp(d, b[:, 0], b[:, 1]))


def base_spec(**over) -> dict:
    s = {"schema": SCHEMA, "hinge": copy.deepcopy(HINGE),
         "outline": {"top": copy.deepcopy(TOP), "bottom": copy.deepcopy(BOTTOM), "tip_round_mm": 0.0},
         "inner": copy.deepcopy(INNER), "thickness": copy.deepcopy(THICK), "zone_end_d": ZONE_END,
         "nodes": {}, "facets": {}, "chamfers": [], "roll_mm": 0.0, "options": {}}
    s.update(copy.deepcopy(over))
    return s


# --------------------------------------------------------------------------------------------- the fixtures
def slab() -> dict:
    """No facets: the base everywhere (zero relief)."""
    return base_spec()


def pyramid(r_c: float = 3.0, r_h: float = 0.5) -> dict:
    """Prada pyramid: F2 = A-B-C (A, B at r 0 on the crease under the top band, C virtual below the bottom edge),
    F3 = A-C-H (front-lower, H on the hinge-end bottom corner), F4 = B-C-E (rear-lower, E on the bottom edge)."""
    return base_spec(nodes={"A": [0.0, 10.5, 0.0], "B": [46.8, 9.52, 0.0], "C": [22.6, -16.3, r_c],
                            "E": [55.0, _yb(55.0), 0.0], "H": [0.0, -9.3, r_h],
                            "H_lo": [0.0, -40.0, None], "C_lo": [22.6, -40.0, None], "E_lo": [55.0, -40.0, None]},
                     facets={"F2": ["A", "B", "C"], "F3": ["A", "H", "H_lo", "C_lo", "C"],
                             "F4": ["B", "C", "C_lo", "E_lo", "E"]})


def valley(depth: float = 1.2) -> dict:
    """An inverted four-facet pyramid (a dimple): apex M (d 25, y 2) `depth` mm below the base, corners on r = 0;
    its four creases are VALLEYS."""
    return base_spec(nodes={"P": [3.0, 2.0, 0.0], "U": [25.0, 13.0, 0.0], "Q": [47.0, 2.0, 0.0],
                            "L": [25.0, -9.0, 0.0], "M": [25.0, 2.0, -depth]},
                     facets={"Vnw": ["P", "M", "U"], "Vne": ["U", "M", "Q"], "Vse": ["Q", "M", "L"],
                             "Vsw": ["L", "M", "P"]})


def zone_end_facet(r: float = 1.5) -> dict:
    """A four-facet pyramid whose east facet lies on the zone end: its edge d = zone_end_d is its zero line (clipped
    by the bottom edge), the arm begins right behind it."""
    ze = ZONE_END
    return base_spec(nodes={"NW": [ze - 24.0, 11.0, 0.0], "NE": [ze, 11.0, 0.0], "SE": [ze, -9.0, 0.0],
                            "SW": [ze - 24.0, -9.0, 0.0], "O": [ze - 12.0, 1.0, r]},
                     facets={"Zn": ["NW", "O", "NE"], "Ze": ["NE", "O", "SE"], "Zs": ["SE", "O", "SW"],
                             "Zw": ["SW", "O", "NW"]})


def top_chamfer_arm() -> dict:
    """The plain arm with a 1.2 mm x 0.8 mm chamfer along its top edge (d 70-125)."""
    return base_spec(chamfers=[{"edge": "top", "d": [70.0, 125.0], "width_mm": 1.2, "depth_mm": 0.8}])


def rolled() -> dict:
    """The Prada pyramid with rolled outer long edges (roll_mm 0.6: Blender's Bevel; the core is unchanged)."""
    s = pyramid()
    s["roll_mm"] = 0.6
    return s


def virtual_node() -> dict:
    """The pyramid's C is a virtual node (below the bottom edge); this variant puts it further out (d 24, y -19)."""
    s = pyramid()
    s["nodes"]["C"] = [24.0, -19.0, 3.4]
    s["nodes"]["C_lo"] = [24.0, -40.0, None]
    return s


def solved_node() -> dict:
    """C's relief is SOLVED (null): F2's plane comes from A, B and D (given, on the A-C edge); C gets F2's value and
    F3 / F4 inherit it through the vertices they share with F2 inside the outline."""
    s = pyramid()
    s["nodes"]["C"] = [22.6, -16.3, None]
    t = 0.45
    A, C = np.array([0.0, 10.5]), np.array([22.6, -16.3])
    D = A + t * (C - A)
    s["nodes"]["D"] = [float(D[0]), float(D[1]), 1.6]
    s["facets"]["F2"] = ["A", "D", "C", "B"]
    return s


def crease_rise(r_a: float = 1.2, rise: float = -0.25, as_nodes: bool = False) -> dict:
    """F1 by crease A-B + rise (F1 leans inward going up); B is at r 0, so F1 meets the base along its zero line
    through B, which reaches the top edge at G (placed exactly). F2 = A-B-C as the pyramid. `as_nodes` defines the
    same plane from the three nodes A, B, G instead (near-collinear: E_ILL_CONDITIONED)."""
    s = pyramid()
    s["nodes"]["A"][2] = r_a
    A, B = s["nodes"]["A"], s["nodes"]["B"]
    coef, _ = crease_plane(A, B, A[2], B[2], rise, interior_left=True)
    G = zero_line_on_chain(coef, TOP, d_min=1.0, d_max=B[0])
    if G is None:
        raise ValueError("F1's zero line does not reach the top edge ahead of B")
    s["nodes"]["G"] = [G[0], G[1], 0.0 if as_nodes else None]
    s["nodes"]["G_hi"] = [G[0], 40.0, None]
    s["nodes"]["A_hi"] = [0.0, 40.0, None]
    poly = ["A", "B", "G", "G_hi", "A_hi"]
    s["facets"]["F1"] = poly if as_nodes else {"poly": poly, "plane": {"crease": ["A", "B"], "rise_per_mm": rise}}
    return s


def near_collinear_f1() -> dict:
    """crease_rise's F1 from the three nodes A, B, G (G nearly on the line A-B): E_ILL_CONDITIONED (WP6 design: such
    a facet tilted 26 deg from a 3 mm node error; crease + rise is the well-conditioned form)."""
    return crease_rise(as_nodes=True)


def f4_thin_regression() -> dict:
    """F4's plane, set by B, C and a node F given deep inside the base, runs below t_min: E_TOO_THIN names the point,
    F4 and its defining points (the WP6 prototype regression: -4.72 mm at (58, -5.30))."""
    s = pyramid()
    s["nodes"]["C"][2] = -9.0
    return s


def chamfered_slab_30() -> dict:
    """The slab with a 30 deg chamfer along the top edge of the facet zone (d 0-60), 5.6 mm wide (the WP6 design's
    30 deg top facet over the band above the A-B crease): the bulge band reads ~2.2 mm, but it is one plane plus a
    strip, so SLAB must fire (the PLAN's bulge band would pass it)."""
    w = 5.6
    return base_spec(chamfers=[{"edge": "top", "d": [0.0, 60.0], "width_mm": w,
                                "depth_mm": w * math.tan(math.radians(30.0))}])


PRISM = {"d": (0.0, 40.0), "y": (-10.0, 10.0), "h": 2.0, "x_in": 60.0, "T": 4.0, "ze": 50.0, "length": 120.0}


def analytic_prism(h: float = PRISM["h"]) -> dict:
    """A straight slab (outline y -10..10 for every d, inner face X 60, thickness 4) with a four-facet pyramid over
    d 0-40 whose apex (d 20, y 0) is h mm proud: N, E, S, W facets meet the base along d = 40 only (r = 0 there).
    Every share, dihedral and bulge has a closed form (tests/test_temple_measure.py)."""
    L = PRISM["length"]
    return {"schema": SCHEMA, "hinge": copy.deepcopy(HINGE),
            "outline": {"top": [[0.0, 10.0], [L, 10.0]], "bottom": [[0.0, -10.0], [L, -10.0]], "tip_round_mm": 0.0},
            "inner": [[0.0, PRISM["x_in"]], [L, PRISM["x_in"]]], "thickness": [[0.0, PRISM["T"]], [L, PRISM["T"]]],
            "zone_end_d": PRISM["ze"],
            "nodes": {"NW": [0.0, 10.0, 0.0], "NE": [40.0, 10.0, 0.0], "SE": [40.0, -10.0, 0.0],
                      "SW": [0.0, -10.0, 0.0], "O": [20.0, 0.0, h]},
            "facets": {"N": ["NW", "O", "NE"], "E": ["NE", "O", "SE"], "S": ["SE", "O", "SW"], "W": ["SW", "O", "NW"]},
            "chamfers": [], "roll_mm": 0.0, "options": {}}


FIXTURES = {"slab": slab, "pyramid": pyramid, "valley": valley, "zone_end_facet": zone_end_facet,
            "top_chamfer_arm": top_chamfer_arm, "rolled": rolled, "virtual_node": virtual_node,
            "solved_node": solved_node, "crease_rise": crease_rise, "analytic_prism": analytic_prism,
            "chamfered_slab_30": chamfered_slab_30}


# --------------------------------------------------------------------------------------------- refusal cases
def refusal_cases() -> dict:
    """{code: spec} - every core refusal of §3 from a minimal broken spec (E_TARGET and E_ROLL are Blender-side)."""
    out = {}
    s = slab()
    s["outline"]["top"] = "not a list"
    out["E_SPEC"] = s
    s = slab()
    s["outline"]["top"] = [[0.0, 10.5]]
    out["E_OUTLINE"] = s
    s = slab()
    s["outline"]["top"] = [[0.0, 10.5], [20.0, 14.0], [19.0, 14.5], [136.5, -20.5]]
    out["E_OUTLINE_NOT_SINGLE_VALUED"] = s
    s = slab()
    s["outline"]["top"] = [[0.0, 10.5], [60.0, -8.0], [136.5, -20.5]]
    out["E_OUTLINE_NOT_SIMPLE"] = s
    s = slab()
    s["inner"] = [[0.0, 65.5], [30.0, 63.0], [65.0, 61.8], [136.5, 47.1]]
    out["E_ZONE"] = s
    s = pyramid()
    s["nodes"]["E"] = [80.0, _yb(80.0), 0.0]
    s["nodes"]["E_lo"] = [80.0, -40.0, None]
    out["E_FACET_ON_BEND"] = s
    s = base_spec(nodes={"P": [10.0, 30.0, 0.0], "Q": [30.0, 30.0, 0.0], "R": [20.0, 40.0, 1.0]},
                  facets={"X": ["P", "Q", "R"]})
    out["E_FACET_OUTSIDE"] = s
    s = pyramid()
    s["nodes"]["J"] = [30.0, 5.0, 0.0]
    s["facets"]["X"] = ["A", "C", "J"]
    out["E_FACET_OVERLAP"] = s
    s = pyramid()
    s["nodes"]["B"][2] = 0.8
    out["E_RELIEF_STEP"] = s
    s = pyramid()
    s["nodes"]["H"][2] = None                 # F3 knows only A, C and their edge: collinear
    out["E_FACET_UNDERDETERMINED"] = s
    s = solved_node()
    s["nodes"]["C"][2] = 3.0                  # D (1.6 on A-C) and C (3.0) disagree with a plane through A, B
    out["E_FACET_NOT_PLANAR"] = s
    out["E_ILL_CONDITIONED"] = near_collinear_f1()
    out["E_TOO_THIN"] = f4_thin_regression()
    s = base_spec(chamfers=[{"edge": "top", "d": [70.0, 125.0], "width_mm": 30.0, "depth_mm": 0.8}])
    out["E_CHAMFER"] = s
    return out


# --------------------------------------------------------------------------------------------- Prada topology
def zero_line_on_chain(coef, chain, d_min: float = -np.inf, d_max: float = np.inf):
    """First point (by d, within [d_min, d_max]) where the relief plane r = a + b d + c y crosses r = 0 on the
    polyline `chain` [[d, y]]; None when it does not."""
    a, b, c = (float(x) for x in coef)
    P = np.asarray(chain, np.float64)
    f = a + b * P[:, 0] + c * P[:, 1]
    hits = []
    for i in range(len(P) - 1):
        if f[i] == 0.0:
            hits.append(P[i])
        elif f[i] * f[i + 1] < 0:
            t = f[i] / (f[i] - f[i + 1])
            hits.append(P[i] + t * (P[i + 1] - P[i]))
    hits = [h for h in hits if d_min <= h[0] <= d_max]
    return None if not hits else [float(hits[0][0]), float(hits[0][1])]


def _zero_line_from(coef, chain, P0):
    """Where the facet's zero line through P0 (r(P0) = 0) first meets the polyline `chain` going upward from P0."""
    a, b, c = (float(x) for x in coef)
    g = np.array([b, c])
    if np.hypot(*g) < 1e-12:
        return None
    u = np.array([-g[1], g[0]]) / np.hypot(*g)
    if u[1] < 0:
        u = -u
    P = np.asarray(chain, np.float64)
    best = None
    for i in range(len(P) - 1):
        q0, q1 = P[i], P[i + 1]
        r_, s_ = u, q1 - q0
        den = r_[0] * s_[1] - r_[1] * s_[0]
        if abs(den) < 1e-15:
            continue
        w = q0 - P0
        t = (w[0] * s_[1] - w[1] * s_[0]) / den
        v = (w[0] * r_[1] - w[1] * r_[0]) / den
        if t > 1e-9 and -1e-12 <= v <= 1 + 1e-12 and (best is None or t < best[0]):
            best = (t, P0 + t * u)
    return None if best is None else [float(best[1][0]), float(best[1][1])]


def _line_isect(c1, c2):
    """Intersection of the zero lines of two relief planes (None when parallel)."""
    A = np.array([[c1[1], c1[2]], [c2[1], c2[2]]], np.float64)
    if abs(np.linalg.det(A)) < 1e-12:
        return None
    return np.linalg.solve(A, -np.array([c1[0], c2[0]], np.float64))


def plane3(p1, p2, p3):
    M = np.array([[1.0, p[0], p[1]] for p in (p1, p2, p3)])
    return np.linalg.solve(M, np.array([p1[2], p2[2], p3[2]], np.float64))


def prada_spec(*, top, bottom, zone_end: float, x_in0: float, x_in_ze: float, t_zone: float, arm: list,
               A=None, B=(46.8, 9.52), C=(22.6, -16.3), d_E: float = 55.0, r_A: float = 0.5, r_B: float = 0.5,
               r_C: float = 3.0, rise: float = -0.25, r_H: float | None = None, hinge=None,
               tip_round_mm: float = 0.0, roll_mm: float = 0.0) -> dict:
    """The Prada temple as a valid g3d.temple/1 spec (module docstring). `arm` = [[d, x_in, T], ...] knots beyond
    the zone; the zone is linear from (d0, x_in0) to (zone_end, x_in_ze) with thickness t_zone. A defaults to the
    hinge-end top corner, H to the hinge-end bottom corner (r_H defaults to r_A). Raises ValueError when the facets
    cannot meet the arm without a step (the zero lines miss the outline or cross ahead of B)."""
    top = [list(map(float, p)) for p in top]
    bottom = [list(map(float, p)) for p in bottom]
    d0 = top[0][0]
    yt = lambda d: _yt(d, top)
    yb = lambda d: _yb(d, bottom)
    A = (d0, yt(d0)) if A is None else tuple(A)
    H = (d0, yb(d0))
    r_H = r_A if r_H is None else r_H
    E = (float(d_E), yb(d_E))
    # F1 by crease A-B + rise; F4 by B, C, E (E on the arm: r 0)
    c1, _ = crease_plane(A, B, r_A, r_B, rise, interior_left=True)
    c4 = plane3((B[0], B[1], r_B), (C[0], C[1], r_C), (E[0], E[1], 0.0))
    if abs(r_B) < 1e-12:
        Kp = np.array(B, np.float64)
    else:
        Kp = _line_isect(c1, c4)
        if Kp is None or Kp[0] <= B[0] + 0.5 or not (yb(Kp[0]) < Kp[1] < yt(Kp[0])):
            raise ValueError(f"F1's and F4's zero lines meet at {None if Kp is None else Kp.round(3).tolist()}, "
                             f"not behind B inside the outline: change r_B, the F1 rise or E")
    if abs(r_B) < 1e-12:
        # B on the base: F1's zero line runs from B to the top edge, forward or backward of B
        K = _zero_line_from(c1, top, np.array(B, np.float64))
    else:
        K = zero_line_on_chain(c1, top, d_min=float(Kp[0]))
    if K is None:
        raise ValueError("F1's zero line does not reach the top edge from K'")
    if K[0] <= d0 or max(K[0], E[0], Kp[0]) >= zone_end:
        raise ValueError(f"the facets reach d {max(K[0], E[0], Kp[0]):.2f}, beyond zone_end_d {zone_end}")
    lo, hi = -60.0, 60.0
    nodes = {"A": [A[0], A[1], r_A], "B": [B[0], B[1], r_B], "C": [C[0], C[1], r_C], "H": [H[0], H[1], r_H],
             "E": [E[0], E[1], 0.0], "K": [K[0], K[1], None],
             "A_hi": [d0 - 1.0, hi, None], "K_hi": [K[0], hi, None], "H_lo": [d0 - 1.0, lo, None],
             "C_lo": [C[0], lo, None], "E_lo": [E[0], lo, None], "A_l": [d0 - 1.0, A[1], None]}
    f1 = ["A", "B", "K", "K_hi", "A_hi", "A_l"]
    f4 = ["B", "C", "C_lo", "E_lo", "E"]
    if abs(r_B) >= 1e-12:
        nodes["Kp"] = [float(Kp[0]), float(Kp[1]), None]
        f1 = ["A", "B", "Kp", "K", "K_hi", "A_hi", "A_l"]
        f4 = ["B", "C", "C_lo", "E_lo", "E", "Kp"]
    facets = {"F1": {"poly": f1, "plane": {"crease": ["A", "B"], "rise_per_mm": float(rise)}},
              "F2": {"poly": ["A", "C", "B"], "plane": "nodes"},
              "F3": {"poly": ["A", "A_l", "H_lo", "C_lo", "C"], "plane": "nodes"},
              "F4": {"poly": f4, "plane": "nodes"}}
    # F3's plane: A, C and H; H is a corner of F3 one mm ahead of the hinge-end cap at the bottom edge's height
    nodes["H"] = [d0 - 1.0, H[1], r_H]
    facets["F3"]["poly"] = ["A", "A_l", "H", "H_lo", "C_lo", "C"]
    inner = [[d0, x_in0], [zone_end, x_in_ze]] + [[float(k[0]), float(k[1])] for k in arm if k[0] > zone_end]
    thick = [[d0, t_zone], [zone_end, t_zone]] + [[float(k[0]), float(k[2])] for k in arm if k[0] > zone_end]
    return {"schema": SCHEMA, "hinge": copy.deepcopy(hinge or HINGE),
            "outline": {"top": top, "bottom": bottom, "tip_round_mm": tip_round_mm},
            "inner": inner, "thickness": thick, "zone_end_d": float(zone_end), "nodes": nodes, "facets": facets,
            "chamfers": [], "roll_mm": roll_mm, "options": {}}


def prada_default() -> dict:
    """The Prada topology on the fixture outline with plausible traced values (a P0 fixture, not a fitted spec)."""
    arm = [[k[0], float(np.interp(k[0], np.array(INNER)[:, 0], np.array(INNER)[:, 1])),
            float(np.interp(k[0], np.array(THICK)[:, 0], np.array(THICK)[:, 1]))] for k in INNER if k[0] > ZONE_END]
    return prada_spec(top=TOP, bottom=BOTTOM, zone_end=ZONE_END, x_in0=65.5, x_in_ze=61.8, t_zone=5.0, arm=arm,
                      r_A=0.5, r_B=0.6, r_C=3.0, rise=-0.25)


FIXTURES["prada_default"] = prada_default
