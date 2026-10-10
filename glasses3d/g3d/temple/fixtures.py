"""P0 fixture specs of g3d.temple/2 (plan/WP6_FACETS.md §5 T1) and the Prada-layout helper. numpy only.

Fixtures (each a full "g3d.temple/2" spec on a Prada-like outline unless named otherwise): slab (no facets), wedge
(base thicker at the bottom edge than at the top), pyramid (Prada F2/F3/F4 with a virtual apex C below the bottom
edge), valley (an inverted four-facet pyramid), arm_pyramid (a pyramid on the arm), top_chamfer_arm, rolled (the
pyramid with the core roll), virtual_node, solved_node, crease_rise (F1 by crease + rise), analytic_prism (closed-form
dihedrals on a straight slab), chamfered_slab_30, prada_default (the four-facet Prada layout with declared creases),
fade (a ridge that dies out in a fade), border_ramp (facets meeting the base with a mismatch the border band absorbs);
and the refusal cases (one minimal spec per core refusal code). Fixtures are never edited to make a test pass.

The fixture base is linear in d and constant in y over d 0-65 (INNER and THICK): "base" nodes there lie on one plane,
so a facet whose border nodes are "base" meets the base without a ramp.
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
T_ZONE = 5.0                 # the fixture base thickness over d 0-65


def _yt(d, top=TOP):
    t = np.asarray(top, float)
    return float(np.interp(d, t[:, 0], t[:, 1]))


def _yb(d, bot=BOTTOM):
    b = np.asarray(bot, float)
    return float(np.interp(d, b[:, 0], b[:, 1]))


def base_spec(**over) -> dict:
    s = {"schema": SCHEMA, "hinge": copy.deepcopy(HINGE),
         "outline": {"top": copy.deepcopy(TOP), "bottom": copy.deepcopy(BOTTOM), "tip_round_mm": 0.0},
         "inner": copy.deepcopy(INNER), "base": {"top": copy.deepcopy(THICK), "bottom": copy.deepcopy(THICK)},
         "nodes": {}, "facets": {}, "creases": [], "chamfers": [], "roll_mm": 0.0, "options": {}}
    s.update(copy.deepcopy(over))
    return s


# --------------------------------------------------------------------------------------------- the fixtures
def slab() -> dict:
    """No facets: the base everywhere."""
    return base_spec()


def wedge() -> dict:
    """No facets on a wedge base: 3.5 mm at the top edge, 6.0 mm at the bottom edge, linear in y between them."""
    return base_spec(base={"top": [[0.0, 3.5], [136.5, 3.5]], "bottom": [[0.0, 6.0], [136.5, 6.0]]})


def pyramid(t_c: float = T_ZONE + 3.0, t_h: float = T_ZONE + 0.5) -> dict:
    """Prada pyramid: F2 = A-B-C (A, B on the base under the top band, C virtual below the bottom edge), F3 = A-H-C
    (front-lower, H on the hinge-end bottom corner), F4 = B-C-E (rear-lower, E on the bottom edge)."""
    return base_spec(nodes={"A": [0.0, 10.5, "base"], "B": [46.8, 9.52, "base"], "C": [22.6, -16.3, t_c],
                            "E": [55.0, _yb(55.0), "base"], "H": [0.0, -9.3, t_h],
                            "H_lo": [0.0, -40.0, None], "C_lo": [22.6, -40.0, None], "E_lo": [55.0, -40.0, None]},
                     facets={"F2": ["A", "B", "C"], "F3": ["A", "H", "H_lo", "C_lo", "C"],
                             "F4": ["B", "C", "C_lo", "E_lo", "E"]})


def valley(depth: float = 1.2) -> dict:
    """An inverted four-facet pyramid (a dimple): apex M (d 25, y 2) `depth` mm below the base, corners on the base;
    its four creases are VALLEYS."""
    return base_spec(nodes={"P": [3.0, 2.0, "base"], "U": [25.0, 13.0, "base"], "Q": [47.0, 2.0, "base"],
                            "L": [25.0, -9.0, "base"], "M": [25.0, 2.0, T_ZONE - depth]},
                     facets={"Vnw": ["P", "M", "U"], "Vne": ["U", "M", "Q"], "Vse": ["Q", "M", "L"],
                             "Vsw": ["L", "M", "P"]},
                     creases=[{"facets": pair, "shape": "valley"} for pair in (["Vne", "Vnw"], ["Vne", "Vse"],
                                                                               ["Vse", "Vsw"], ["Vnw", "Vsw"])])


def arm_pyramid(proud: float = 1.5) -> dict:
    """A four-facet pyramid over d 41-65 (corners on the base, apex `proud` mm above it)."""
    return base_spec(nodes={"NW": [41.0, 11.0, "base"], "NE": [65.0, 11.0, "base"], "SE": [65.0, -9.0, "base"],
                            "SW": [41.0, -9.0, "base"], "O": [53.0, 1.0, T_ZONE + proud]},
                     facets={"Zn": ["NW", "O", "NE"], "Ze": ["NE", "O", "SE"], "Zs": ["SE", "O", "SW"],
                             "Zw": ["SW", "O", "NW"]})


def top_chamfer_arm() -> dict:
    """The plain arm with a 1.2 mm x 0.8 mm chamfer along its top edge (d 70-125)."""
    return base_spec(chamfers=[{"edge": "top", "d": [70.0, 125.0], "width_mm": 1.2, "depth_mm": 0.8}])


def rolled(roll_mm: float = 0.6) -> dict:
    """The pyramid with the core roll along both outline edges."""
    s = pyramid()
    s["roll_mm"] = roll_mm
    return s


def virtual_node() -> dict:
    """The pyramid's C further out below the bottom edge (d 24, y -19)."""
    s = pyramid()
    s["nodes"]["C"] = [24.0, -19.0, T_ZONE + 3.4]
    s["nodes"]["C_lo"] = [24.0, -40.0, None]
    return s


def solved_node() -> dict:
    """C is SOLVED (null): F2's plane comes from A, B and D (given, on the A-C edge); C gets F2's value and F3 / F4
    inherit it through the vertices they share with F2 inside the outline."""
    s = pyramid()
    s["nodes"]["C"] = [22.6, -16.3, None]
    t = 0.45
    A, C = np.array([0.0, 10.5]), np.array([22.6, -16.3])
    D = A + t * (C - A)
    s["nodes"]["D"] = [float(D[0]), float(D[1]), T_ZONE + 1.6]
    s["facets"]["F2"] = ["A", "D", "C", "B"]
    return s


def _plane_minus_base_line(coef, top, inner, base_t):
    """(a, b, c) of X_plane - X_base where the base is a plane (inner linear, thickness constant in y): the line where a
    facet plane meets such a base is this difference's zero line."""
    inner = np.asarray(inner, float)
    s = (inner[1, 1] - inner[0, 1]) / (inner[1, 0] - inner[0, 0])
    b0 = inner[0, 1] - s * inner[0, 0] + base_t
    return np.array([coef[0] - b0, coef[1] - s, coef[2]])


def crease_rise(t_a: float = T_ZONE + 1.2, rise: float = -0.25, as_nodes: bool = False) -> dict:
    """F1 by crease A-B + rise (F1 leans inward going up); B is on the base, so F1 meets the base along a line through
    B, which reaches the top edge at G (placed exactly). F2 = A-B-C as the pyramid. `as_nodes` defines the same plane
    from the three nodes A, B, G instead (near-collinear: E_ILL_CONDITIONED)."""
    s = pyramid()
    s["nodes"]["A"][2] = t_a
    A, B = s["nodes"]["A"], s["nodes"]["B"]
    XA = 65.5 + t_a
    XB = 65.5 + (61.8 - 65.5) * B[0] / 65.0 + T_ZONE
    coef, _ = crease_plane(A, B, XA, XB, rise, interior_left=True)
    G = zero_line_on_chain(_plane_minus_base_line(coef, TOP, INNER, T_ZONE), TOP, d_min=1.0, d_max=B[0])
    if G is None:
        raise ValueError("F1 does not meet the base on the top edge ahead of B")
    s["nodes"]["G"] = [G[0], G[1], "base" if as_nodes else None]
    s["nodes"]["G_hi"] = [G[0], 40.0, None]
    s["nodes"]["A_hi"] = [0.0, 40.0, None]
    poly = ["A", "B", "G", "G_hi", "A_hi"]
    s["facets"]["F1"] = poly if as_nodes else {"poly": poly, "plane": {"crease": ["A", "B"], "rise_per_mm": rise}}
    return s


def near_collinear_f1() -> dict:
    """crease_rise's F1 from the three nodes A, B, G (G nearly on the line A-B): E_ILL_CONDITIONED; the same plane as
    crease + rise is well conditioned."""
    return crease_rise(as_nodes=True)


def f4_thin_regression() -> dict:
    """F4's plane, set by B, C and E with C given deep inside the inner face, runs below t_min: E_TOO_THIN names the
    point, F4 and its defining points."""
    s = pyramid()
    s["nodes"]["C"][2] = T_ZONE - 9.0
    return s


def chamfered_slab_30() -> dict:
    """The slab with a 30 deg chamfer along the top edge (d 0-60), 5.6 mm wide: one plane plus a strip."""
    w = 5.6
    return base_spec(chamfers=[{"edge": "top", "d": [0.0, 60.0], "width_mm": w,
                                "depth_mm": w * math.tan(math.radians(30.0))}])


PRISM = {"d": (0.0, 40.0), "y": (-10.0, 10.0), "h": 2.0, "x_in": 60.0, "T": 4.0, "length": 120.0}


def straight(**over) -> dict:
    """A straight slab: outline y -10..10 for every d in [0, 120], inner face X 60, base thickness 4."""
    L = PRISM["length"]
    s = {"schema": SCHEMA, "hinge": copy.deepcopy(HINGE),
         "outline": {"top": [[0.0, 10.0], [L, 10.0]], "bottom": [[0.0, -10.0], [L, -10.0]], "tip_round_mm": 0.0},
         "inner": [[0.0, PRISM["x_in"]], [L, PRISM["x_in"]]],
         "base": {"top": [[0.0, PRISM["T"]], [L, PRISM["T"]]], "bottom": [[0.0, PRISM["T"]], [L, PRISM["T"]]]},
         "nodes": {}, "facets": {}, "creases": [], "chamfers": [], "roll_mm": 0.0, "options": {}}
    s.update(copy.deepcopy(over))
    return s


def analytic_prism(h: float = PRISM["h"]) -> dict:
    """The straight slab with a four-facet pyramid over d 0-40 whose apex (d 20, y 0) is h mm proud: N, E, S, W
    facets; E meets the base along d = 40. Every dihedral and bulge has a closed form (tests)."""
    return straight(nodes={"NW": [0.0, 10.0, "base"], "NE": [40.0, 10.0, "base"], "SE": [40.0, -10.0, "base"],
                           "SW": [0.0, -10.0, "base"], "O": [20.0, 0.0, PRISM["T"] + h]},
                    facets={"N": ["NW", "O", "NE"], "E": ["NE", "O", "SE"], "S": ["SE", "O", "SW"],
                            "W": ["SW", "O", "NW"]})


def fade(d0: float = 30.0, d1: float = 60.0, proud: float = 1.5) -> dict:
    """A ridge along y = 0 from the hinge-end cap that dies out in a fade: N and S facets (ridge `proud` mm above the
    base, their outer corners on it) carry the same fade, the plane at d0, the base at d1."""
    f = {"d0": d0, "d1": d1}
    t = PRISM["T"] + proud
    return straight(nodes={"R0": [0.0, 0.0, t], "R1": [d1, 0.0, t], "N0": [0.0, 10.0, "base"],
                           "N1": [d1, 10.0, "base"], "S0": [0.0, -10.0, "base"], "S1": [d1, -10.0, "base"]},
                    facets={"N": {"poly": ["R0", "R1", "N1", "N0"], "fade": f},
                            "S": {"poly": ["S0", "S1", "R1", "R0"], "fade": f}},
                    creases=[{"facets": ["N", "S"], "shape": "ridge"}])


def border_ramp(proud_b: float = 0.6) -> dict:
    """The pyramid with B `proud_b` mm above the base: F2 and F4 meet the base with a mismatch rising from 0 at A / E
    to proud_b at B, which the border band absorbs (W_BORDER_RAMP above 8 deg)."""
    s = pyramid()
    s["nodes"]["B"][2] = T_ZONE + proud_b
    return s


def prada_default() -> dict:
    """The four-facet Prada layout on the fixture outline with plausible traced values (a P0 fixture, not a fitted
    spec): A 0.17 mm below the base, B and E on it, C 3 mm proud, F1's rise -0.38 (the owner-preferred spec's values;
    B higher than its 9.52 so that F1 reaches the top edge over the fixture's planar base), every crease declared."""
    s = prada_layout(top=TOP, bottom=BOTTOM, inner=INNER, base_t=THICK, B=(46.8, 11.8), t_a=T_ZONE - 0.17,
                     t_c=T_ZONE + 3.0, t_h="base", rise=-0.38)
    s["creases"] = [{"facets": ["F1", "F2"], "shape": "ridge"}, {"facets": ["F1", "base"], "shape": "ridge"},
                    {"facets": ["F2", "F3"], "shape": "ridge"}, {"facets": ["F2", "F4"], "shape": "ridge"},
                    {"facets": ["F4", "base"], "shape": "valley"}]
    return s


FIXTURES = {"slab": slab, "wedge": wedge, "pyramid": pyramid, "valley": valley, "arm_pyramid": arm_pyramid,
            "top_chamfer_arm": top_chamfer_arm, "rolled": rolled, "virtual_node": virtual_node,
            "solved_node": solved_node, "crease_rise": crease_rise, "analytic_prism": analytic_prism,
            "chamfered_slab_30": chamfered_slab_30, "prada_default": prada_default, "fade": fade,
            "border_ramp": border_ramp}


# --------------------------------------------------------------------------------------------- refusal cases
def relief_step() -> dict:
    """Two facets sharing the edge P-Z (Z virtual, null) whose own nodes set different planes: the second facet meets
    the values the first hands to their shared vertices with a step (E_RELIEF_STEP)."""
    return straight(nodes={"P": [20.0, 0.0, 5.0], "Z": [80.0, 0.0, None], "R": [20.0, 8.0, 4.5], "R2": [60.0, 8.0, 4.5],
                           "S": [20.0, -8.0, 4.5], "S2": [60.0, -8.0, 5.5], "Rz": [80.0, 30.0, None],
                           "Sz": [80.0, -30.0, None]},
                    facets={"Fa": ["P", "Z", "Rz", "R2", "R"], "Fb": ["P", "S", "S2", "Sz", "Z"]})


def refusal_cases() -> dict:
    """{code: spec} - every core refusal from a minimal broken spec (E_TARGET and E_MULTI_MATERIAL are Blender-side;
    E_TRIANGULATE and E_NOT_CLOSED are internal guards)."""
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
    s["inner"] = [[0.0, 65.5], [30.0, 63.7], [30.02, 63.7], [65.0, 61.8], [136.5, 47.1]]
    out["E_SHORT_EDGE"] = s
    s = base_spec(nodes={"P": [10.0, 30.0, "base"], "Q": [30.0, 30.0, "base"], "R": [20.0, 40.0, 6.0]},
                  facets={"X": ["P", "Q", "R"]})
    out["E_FACET_OUTSIDE"] = s
    s = pyramid()
    s["nodes"]["J"] = [30.0, 5.0, "base"]
    s["facets"]["X"] = ["A", "C", "J"]
    out["E_FACET_OVERLAP"] = s
    out["E_RELIEF_STEP"] = relief_step()
    s = pyramid()
    s["nodes"]["H"][2] = None                 # F3 knows only A, C and their edge: collinear
    out["E_FACET_UNDERDETERMINED"] = s
    s = solved_node()
    s["nodes"]["C"][2] = T_ZONE + 3.0         # D (1.6 proud, on A-C) and C (3.0) disagree with a plane through A, B
    out["E_FACET_NOT_PLANAR"] = s
    out["E_ILL_CONDITIONED"] = near_collinear_f1()
    s = fade()
    s["facets"]["S"]["fade"] = None           # N fades, its crease neighbour S does not
    out["E_FADE_NEIGHBOUR"] = s
    out["E_TOO_THIN"] = f4_thin_regression()
    s = base_spec(chamfers=[{"edge": "top", "d": [70.0, 125.0], "width_mm": 30.0, "depth_mm": 0.8}])
    out["E_CHAMFER"] = s
    out["E_ROLL"] = rolled(roll_mm=3.5)
    s = pyramid()
    s["creases"] = [{"facets": ["F2", "F3"], "shape": "valley"}]
    out["E_CREASE_SHAPE"] = s
    return out


# --------------------------------------------------------------------------------------------- Prada layout
def zero_line_on_chain(coef, chain, d_min: float = -np.inf, d_max: float = np.inf):
    """First point (by d, within [d_min, d_max]) where a + b d + c y crosses 0 on the polyline `chain` [[d, y]]; None
    when it does not."""
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


def zero_line_from(coef, chain, P0, direction="up"):
    """First crossing of the zero line of a + b d + c y with the polyline `chain`, walking from P0 (on the line) in an
    explicit direction: "up" (+y) or "rearward" (+d). None when the line misses the chain."""
    if direction not in ("up", "rearward"):
        raise ValueError("zero-line direction must be 'up' or 'rearward'")
    a, b, c = (float(x) for x in coef)
    g = np.array([b, c])
    if np.hypot(*g) < 1e-12:
        return None
    u = np.array([-g[1], g[0]]) / np.hypot(*g)
    if u[1 if direction == "up" else 0] < 0:
        u = -u
    P = np.asarray(chain, np.float64)
    best = None
    for i in range(len(P) - 1):
        q0, q1 = P[i], P[i + 1]
        s_ = q1 - q0
        den = u[0] * s_[1] - u[1] * s_[0]
        if abs(den) < 1e-15:
            continue
        w = q0 - P0
        t = (w[0] * s_[1] - w[1] * s_[0]) / den
        v = (w[0] * u[1] - w[1] * u[0]) / den
        if t > 1e-9 and -1e-12 <= v <= 1 + 1e-12 and (best is None or t < best[0]):
            best = (t, P0 + t * u)
    return None if best is None else [float(best[1][0]), float(best[1][1])]


def plane3(p1, p2, p3):
    M = np.array([[1.0, p[0], p[1]] for p in (p1, p2, p3)])
    return np.linalg.solve(M, np.array([p1[2], p2[2], p3[2]], np.float64))


def prada_layout(*, top, bottom, inner, base_t, A=None, B=(46.8, 9.52), C=(22.6, -16.3), d_E: float = 55.0,
                 t_a: float, t_c: float, t_h: float | str = "base", rise: float = -0.25, hinge=None,
                 top_exit: str = "up", roll_mm: float = 0.0) -> dict:
    """The Prada PR 17WS layout as a /2 spec: F1 = the upper band by crease A-B + rise (world X), F2 = A-C-B (the
    logo triangle, C virtual below the bottom edge), F3 = front-lower (A, A_l, H, H_lo, C_lo, C), F4 = rear-lower
    (B, C, C_lo, E_lo, E). B and E lie on the base; F1 meets the base along the line through B where its plane equals
    the base, which reaches the top edge at K ("up": walking up from B; "rearward": walking back). The base must be a
    plane over the facets (inner linear over its first segment, base_t constant there). A defaults to the hinge-end top
    corner; A_l and H lie one mm ahead of the hinge-end cap. Raises ValueError when F1 cannot reach the top edge."""
    top = [list(map(float, p)) for p in top]
    bottom = [list(map(float, p)) for p in bottom]
    d0 = top[0][0]
    A = (d0, _yt(d0, top)) if A is None else (float(A[0]), float(A[1]))
    dl = min(d0, A[0]) - 1.0
    E = (float(d_E), _yb(d_E, bottom))
    inner_a = np.asarray(inner, float)
    bt = np.asarray(base_t, float)
    t0 = float(bt[0, 1])
    s_in = (inner_a[1, 1] - inner_a[0, 1]) / (inner_a[1, 0] - inner_a[0, 0])
    changed = bt[np.abs(bt[:, 1] - t0) > 1e-12, 0]
    plane_end = min(float(inner_a[1, 0]), float(changed.min()) if len(changed) else np.inf)
    x_base = lambda d: inner_a[0, 1] + s_in * (d - inner_a[0, 0]) + t0
    XA = x_base(A[0]) - t0 + t_a
    coef, _ = crease_plane(A, B, XA, x_base(B[0]), rise, interior_left=True)
    diff = _plane_minus_base_line(coef, top, inner, t0)
    K = zero_line_from(diff, top, np.array(B, np.float64), direction=top_exit)
    if K is None:
        raise ValueError(f"F1 does not meet the base on the top edge walking {top_exit} from B")
    if max(K[0], E[0], B[0]) > plane_end:
        raise ValueError(f"the facets reach d {max(K[0], E[0], B[0]):.2f}, beyond d {plane_end:g} where the base "
                         f"stops being one plane")
    lo, hi = -60.0, 60.0
    nodes = {"A": [A[0], A[1], t_a], "B": [float(B[0]), float(B[1]), "base"], "C": [float(C[0]), float(C[1]), t_c],
             "H": [dl, _yb(d0, bottom), t_h], "E": [E[0], E[1], "base"], "K": [K[0], K[1], None],
             "A_hi": [dl, hi, None], "K_hi": [K[0], hi, None], "H_lo": [dl, lo, None], "C_lo": [float(C[0]), lo, None],
             "E_lo": [E[0], lo, None], "A_l": [dl, A[1], None]}
    facets = {"F1": {"poly": ["A", "B", "K", "K_hi", "A_hi", "A_l"], "plane": {"crease": ["A", "B"],
                                                                                "rise_per_mm": float(rise)}},
              "F2": {"poly": ["A", "C", "B"], "plane": "nodes"},
              "F3": {"poly": ["A", "A_l", "H", "H_lo", "C_lo", "C"], "plane": "nodes"},
              "F4": {"poly": ["B", "C", "C_lo", "E_lo", "E"], "plane": "nodes"}}
    return {"schema": SCHEMA, "hinge": copy.deepcopy(hinge or HINGE),
            "outline": {"top": top, "bottom": bottom, "tip_round_mm": 0.0}, "inner": [list(p) for p in inner],
            "base": {"top": [list(p) for p in base_t], "bottom": [list(p) for p in base_t]}, "nodes": nodes,
            "facets": facets, "creases": [], "chamfers": [], "roll_mm": roll_mm, "options": {}}
