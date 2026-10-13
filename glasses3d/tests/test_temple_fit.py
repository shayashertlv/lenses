"""WP6 v3 fit (plan/WP6_V3.md §3, g3d/temple/fit.py): weighted least squares over the readings with exact equalities,
an exact active-set inequality solve, crease angles at shared-edge midpoints, declarations against the readings-only
fit, flatness, priors, the report. Fit-only: nothing here needs the builder's coef / warp support (the end-to-end
builds are tests/test_temple_fit_build.py); the refusals need spec.REFUSALS to list E_FIT / E_FIT_SHAPE (WP6_V3 §2).

The real-data tests use run 7's final layout (package_007 revision 7: five faces crown, logo, lower, nose, shoulder on
nodes A C D E H K L) with its outline, inner face and base copied here (data/ is git-ignored), nodes moved to the
settled readings: A, H, K, E the four-photo bundle values of review_20261012/w2/crown-geometry.md §2 (E at d 75.52,
where the photos close the crown, not run 7's d 85); C the crease-line corner C' (w2/midblock-settled.md §2.1); L and D
from target.md table 2.1 (L moved to the hinge plane, as the crown-geometry tool test moved A). The outline is spliced
through the corners, as in that tool test.

They are TOOL TESTS ON MIXED INPUTS (review P2-7): two investigations' readings, and one hinge node A serving both the
crown's vertex (X 68.74) and the logo's front corner (A2, X 69.49; FINDINGS §0.2 calls the shared A a wrong
reference). Their crease rows and refusals describe these inputs, not the temple: never owner-checklist evidence.
test_real_run7_one_source re-reads every corner from one source (crown-geometry §2: its own D and L) and shows that
the logo|nose refusal turns on A alone."""
from __future__ import annotations

import copy
import json
import math
import re
import time

import numpy as np
import pytest

from glasses3d.g3d.temple import fit as FT
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple.build import build_temple, chamfer_depth, points_in_poly, roll_depth
from glasses3d.g3d.temple.planes import ccw_poly, mvc
from glasses3d.g3d.temple.spec import TempleError, parse


def read(X: float, sd: float, name: str = "") -> dict:
    return {"read": {"id": name, "X": float(X), "sd": float(sd)}}


# --------------------------------------------------------------------------------------------- layout 1
# The straight slab (outline y -10..10, d 0..120, x_in 60, base 4: base X 64): a four-facet pyramid N E S W (apex O
# 2 mm proud), a warp quad Q behind it (a 1.8 mm twist) and a flat quad R behind that (four corners on one plane, one
# extra corner), every corner read at its true X.
POS = {"NW": (0.0, 10.0), "NE": (40.0, 10.0), "SE": (40.0, -10.0), "SW": (0.0, -10.0), "O": (20.0, 0.0),
       "Q1": (70.0, 10.0), "Q2": (70.0, -10.0), "R1": (100.0, 10.0), "R2": (100.0, -10.0)}
TRUTH = {"NW": 64.0, "NE": 64.0, "SE": 64.0, "SW": 64.0, "O": 66.0, "Q1": 63.2, "Q2": 65.0, "R1": 64.0, "R2": 65.8}
FACETS = {"N": ["NW", "O", "NE"], "E": ["NE", "O", "SE"], "S": ["SE", "O", "SW"], "W": ["SW", "O", "NW"],
          "Q": {"poly": ["NE", "SE", "Q2", "Q1"], "plane": "warp"}, "R": ["Q1", "Q2", "R2", "R1"]}
SD = {"NW": 0.3, "NE": 0.25, "SE": 0.35, "SW": 0.3, "O": 0.2, "Q1": 0.4, "Q2": 0.3, "R1": 0.25, "R2": 0.3}


def layout1(values=None, creases=()) -> dict:
    values = TRUTH if values is None else values
    return FX.straight(nodes={n: [*POS[n], read(values[n], SD[n], n)] for n in POS}, facets=copy.deepcopy(FACETS),
                       creases=[{"facets": list(p), "shape": s} for p, s in creases])


def plane3(pts) -> np.ndarray:
    M = np.array([[1.0, p[0], p[1]] for p in pts])
    return np.linalg.solve(M, np.array([p[2] for p in pts], np.float64))


def true_plane(label: str) -> np.ndarray:
    poly = FACETS[label] if isinstance(FACETS[label], list) else FACETS[label]["poly"]
    return plane3([(*POS[n], TRUTH[n]) for n in poly[:3]])


def warp_X(p) -> float:
    P = np.array([POS[n] for n in FACETS["Q"]["poly"]])
    return float(FT.mvc_weights(P, p) @ np.array([TRUTH[n] for n in FACETS["Q"]["poly"]]))


def warp_slope(m, n) -> float:
    """dX/dn of the warp Q at m on its edge, from inside along n: second-order one-sided differences of the MVC
    weights (independent of fit.mvc_edge_gradient)."""
    m, n = np.asarray(m, float), np.asarray(n, float)
    h = 1e-3
    X0 = float(np.interp(m[1], [-10.0, 10.0], [TRUTH["SE"], TRUTH["NE"]]) if m[0] == 40.0 else
               np.interp(m[1], [-10.0, 10.0], [TRUTH["Q2"], TRUTH["Q1"]]))
    return (4.0 * warp_X(m + h * n) - warp_X(m + 2 * h * n) - 3.0 * X0) / (2.0 * h)


def deg(g1, g2) -> float:
    return math.degrees(math.atan(g1) - math.atan(g2))


def expected_creases() -> dict:
    """{pair: deg} from the true planes and the warp's one-sided slopes (pair order sorted, n from F1 into F2)."""
    pl = {k: true_plane(k) for k in ("N", "E", "S", "W", "R")}
    g = lambda k, n: float(pl[k][1] * n[0] + pl[k][2] * n[1])
    out = {}
    for f1, f2, a, b, into in (("E", "N", "O", "NE", "N"), ("E", "S", "O", "SE", "S"), ("N", "W", "O", "NW", "W"),
                               ("S", "W", "O", "SW", "W")):
        p, q = np.array(POS[a]), np.array(POS[b])
        u = (q - p) / np.hypot(*(q - p))
        n = np.array([-u[1], u[0]])
        poly = FACETS[into]
        c = np.mean([POS[x] for x in poly], axis=0)
        if (c - p) @ n < 0:
            n = -n
        out[(f1, f2)] = deg(g(f1, n), g(f2, n))
    out[("E", "Q")] = deg(g("E", (1.0, 0.0)), warp_slope((40.0, 0.0), (1.0, 0.0)))
    out[("Q", "R")] = deg(-warp_slope((70.0, 0.0), (-1.0, 0.0)), g("R", (1.0, 0.0)))
    out[("R", "base")] = deg(g("R", (1.0, 0.0)), 0.0)
    return out


def test_exact_recovery_on_consistent_data():
    res = FT.fit_spec(layout1())
    for n, X in TRUTH.items():
        assert res.nodes[n]["X"] == pytest.approx(X, abs=1e-9)
        assert abs(res.nodes[n]["residual_sd"]) <= 1e-8 and res.nodes[n]["source"] == "read"
        assert res.nodes[n]["t"] == pytest.approx(X - 60.0, abs=1e-9)
    assert res.chi2 <= 1e-15 and res.chi2_free <= 1e-15 and res.dof == 1 and res.n_obs == 9
    for k in ("N", "E", "S", "W", "R"):
        assert res.spec["facets"][k]["plane"]["coef"] == pytest.approx(list(true_plane(k)), abs=1e-9)
    assert res.spec["facets"]["Q"]["plane"] == "warp"
    exp = expected_creases()
    got = {tuple(c["pair"]): c for c in res.creases}
    assert set(got) == set(exp)
    for pair, d in exp.items():
        assert got[pair]["deg"] == pytest.approx(d, abs=1e-4), pair
        assert got[pair]["shape"] == ("flat" if abs(d) < 2.0 else "ridge" if d > 0 else "valley")
    assert res.faces["Q"]["kind"] == "warp" and res.faces["R"]["kind"] == "flat" and not res.warnings
    # declaring every true shape (>= MIN_CREASE_DEG) costs nothing and changes nothing
    decl = [(p, "ridge" if d > 0 else "valley") for p, d in exp.items() if abs(d) >= FT.MIN_CREASE_DEG]
    assert len(decl) == 5                                                    # Q|R (-1.3) and R|base (1.5) are flat
    res2 = FT.fit_spec(layout1(creases=decl))
    assert res2.chi2 == pytest.approx(res.chi2, abs=1e-12)
    for n in TRUTH:
        assert res2.nodes[n]["X"] == pytest.approx(TRUTH[n], abs=1e-9)
    assert all(c["dchi2"] == 0.0 for c in res2.creases if c["declared"])


def test_output_is_a_pure_v2_spec_and_the_rest_is_copied():
    design = layout1()
    design["nodes"]["Z"] = [110.0, 5.0, read(63.5, 0.2)]              # a reading on a node in no facet's polygon
    design["facets"]["R"] = {"poly": FACETS["R"], "plane": "nodes", "soft": True}
    before = json.dumps(design, sort_keys=True)
    res = FT.fit_spec(design)
    assert json.dumps(design, sort_keys=True) == before                  # the design is not touched
    out = res.spec
    for n, v in out["nodes"].items():
        assert isinstance(v[2], float) and v[:2] == design["nodes"][n][:2]
    assert out["nodes"]["Z"][2] == pytest.approx(3.5, abs=1e-12)
    assert "Z" not in res.nodes
    for k in ("schema", "hinge", "outline", "inner", "base", "creases", "chamfers", "roll_mm", "options"):
        assert out[k] == design[k]
    assert out["facets"]["R"]["soft"] is True and out["facets"]["R"]["poly"] == FACETS["R"]
    assert all(set(f) >= {"poly", "plane"} for f in out["facets"].values())
    assert "read" not in json.dumps(out)


def test_refitting_a_fitted_spec_returns_it():
    """A fitted spec is a fixed point: its coef planes are hard, its typed values lie on them, its warp is exact."""
    rng = np.random.default_rng(3)
    noisy = {n: X + rng.normal(0.0, SD[n]) for n, X in TRUTH.items()}
    a = FT.fit_spec(layout1(noisy)).spec
    b = FT.fit_spec(a)
    for n in a["nodes"]:
        assert b.spec["nodes"][n][2] == pytest.approx(a["nodes"][n][2], abs=1e-9)
    for k, f in a["facets"].items():
        if f["plane"] != "warp":
            assert b.spec["facets"][k]["plane"]["coef"] == pytest.approx(f["plane"]["coef"], abs=1e-9)
    assert b.chi2 <= 1e-15


# --------------------------------------------------------------------------------------------- least squares
CROWN = {"A": (5.0, 2.0), "K": (47.0, 1.6), "E": (75.0, 1.4), "H": (36.0, 8.0)}
CROWN_SD = {"A": 0.31, "K": 0.13, "E": 0.44, "H": 0.24}


def crown_design(off: float) -> tuple[dict, dict]:
    """One flat 4-corner face with K `off` mm outside the plane through A, H, E (the crown's numbers)."""
    base = {"A": 64.5, "H": 63.6, "E": 64.2}
    c = plane3([(*CROWN[n], base[n]) for n in ("A", "H", "E")])
    obs = dict(base, K=float(c[0] + c[1] * CROWN["K"][0] + c[2] * CROWN["K"][1] + off))
    s = FX.straight(nodes={n: [*CROWN[n], read(obs[n], CROWN_SD[n])] for n in CROWN},
                    facets={"crown": ["A", "K", "E", "H"]})
    return s, obs


def test_least_squares_spreads_a_non_coplanar_flat_face_with_the_right_residuals():
    s, obs = crown_design(1.5)
    res = FT.fit_spec(s)
    names = list(CROWN)
    M = np.array([[1.0, *CROWN[n]] for n in names])
    w = np.array([1.0 / CROWN_SD[n] for n in names])
    x = np.array([obs[n] for n in names])
    coef = np.linalg.lstsq(M * w[:, None], x * w, rcond=None)[0]       # the independent weighted plane
    for n, fitted in zip(names, M @ coef):
        assert res.nodes[n]["X"] == pytest.approx(fitted, abs=1e-9)
        assert res.nodes[n]["residual_mm"] == pytest.approx(fitted - obs[n], abs=1e-9)
        assert res.nodes[n]["residual_sd"] == pytest.approx((fitted - obs[n]) / CROWN_SD[n], abs=1e-8)
    chi2 = float(np.sum(((M @ coef - x) * w) ** 2))
    assert res.chi2 == pytest.approx(chi2, rel=1e-9) and res.dof == 1
    assert res.spec["facets"]["crown"]["plane"]["coef"] == pytest.approx(list(coef), abs=1e-9)
    # every corner's leave-one-out deviation from the exact plane of the other three, and its sd through that plane's
    # barycentric weights (review P1-2: with one extra corner every sigma is the same, so no corner is singled out)
    f = res.faces["crown"]
    loo = {x["node"]: x for x in f["loo"]}
    assert list(loo) == f["corners"] == ["A", "K", "E", "H"]
    sig = []
    for j in names:
        o = [n for n in names if n != j]
        lam = np.linalg.solve(np.array([[1.0, 1.0, 1.0], [CROWN[n][0] for n in o], [CROWN[n][1] for n in o]]),
                              np.array([1.0, *CROWN[j]]))
        dev = obs[j] - sum(l * obs[n] for l, n in zip(lam, o))
        tot = math.sqrt(CROWN_SD[j] ** 2 + sum((l * CROWN_SD[n]) ** 2 for l, n in zip(lam, o)))
        assert loo[j]["mm"] == pytest.approx(dev, abs=1e-8) and loo[j]["sd_mm"] == pytest.approx(tot, rel=1e-8), j
        assert loo[j]["in_line"] == (tot > FT.IN_LINE_RATIO * CROWN_SD[j]), j
        sig.append(abs(dev) / tot)
    assert loo["K"]["mm"] == pytest.approx(1.5, abs=1e-9) and loo["H"]["in_line"]   # A, K, E nearly in line
    assert sig == pytest.approx([math.sqrt(chi2)] * 4, rel=1e-8)            # one extra corner: one sigma for all
    assert f["flat_chi2"] == pytest.approx(chi2, rel=1e-9) and f["chi2"] == pytest.approx(chi2, rel=1e-9)
    assert f["not_flat"] and f["extra"] == 1
    fit = sorted(names, key=lambda n: -abs(res.nodes[n]["residual_sd"]))
    assert f["misfit_at"] == fit[0] and f["flat_misfit_mm"] == pytest.approx(res.nodes[fit[0]]["residual_mm"])
    (w0,) = [x for x in res.warnings if x["code"] == "W_FACE_NOT_FLAT"]
    devs = ", ".join(f"{n} {loo[n]['mm']:+.2f} +- {loo[n]['sd_mm']:.2f}{' mm' if n == 'A' else ''}" for n in "AKE")
    built = ", ".join(f"{n} X {res.nodes[n]['residual_mm']:+.2f} mm ({abs(res.nodes[n]['residual_sd']):.1f} sd)"
                      for n in fit)                                 # read with no (d, y): its X row is its residual
    assert w0["message"] == (f"face crown: its corners do not fit one flat face (chi2 {chi2:.1f} for 1 extra corner): "
                             f"each corner {math.sqrt(chi2):.1f} sd from the plane of the other three (with one extra "
                             f"corner the readings cannot say which corner is off): {devs}, H (others nearly in line); "
                             f"the flat fit (fitted - read): {built}; ways (unranked): warp (one face, no line), a "
                             f"fold (two faces), another reading of a corner, a corner that is two vertices (one per "
                             f"face)")
    # within FLAT_CHI2_PER_CORNER: flat, no warning
    small = 1.9 * loo["K"]["sd_mm"]                                          # chi2 = 3.61 < 4 per extra corner
    res = FT.fit_spec(crown_design(small)[0])
    assert not res.faces["crown"]["not_flat"] and not res.warnings
    assert res.faces["crown"]["flat_chi2"] == pytest.approx(1.9 ** 2, rel=1e-9)
    # the same corners as a warp: every reading kept, the warp reported from the least-squares plane
    s["facets"]["crown"] = {"poly": ["A", "K", "E", "H"], "plane": "warp"}
    res = FT.fit_spec(s)
    assert res.chi2 <= 1e-15 and not res.warnings
    M4 = np.array([[1.0, *CROWN[n]] for n in names])
    ls = np.linalg.lstsq(M4, x, rcond=None)[0]
    assert res.faces["crown"]["warp_mm"] == pytest.approx(float(np.max(np.abs(M4 @ ls - x))), abs=1e-9)


def test_covariance_matches_monte_carlo():
    rng = np.random.default_rng(11)
    draws, first = [], None
    for _ in range(300):
        res = FT.fit_spec(layout1({n: X + rng.normal(0.0, SD[n]) for n, X in TRUTH.items()}))
        first = first or res
        draws.append([res.nodes[n]["X"] for n in TRUTH] + [c["deg"] for c in res.creases])
    D = np.array(draws)
    sd_mc = D.std(axis=0, ddof=1)
    sd_fit = np.array([first.nodes[n]["sd_X"] for n in TRUTH] + [c["sd_deg"] for c in first.creases])
    # 300 draws: an sd's own relative error is ~4 %; the angle sd is a linearisation of atan
    assert np.all(np.abs(sd_mc / sd_fit - 1.0) < 0.15), (sd_mc / sd_fit).round(3).tolist()
    assert np.all(np.abs(D[:, :9].mean(axis=0) - np.array(list(TRUTH.values()))) < 4 * sd_fit[:9] / math.sqrt(300))
    # R's extra corner pulls its sd below the reading's; a corner set only by its own reading keeps the reading's sd
    assert first.nodes["R2"]["sd_X"] < SD["R2"]
    assert first.nodes["O"]["sd_X"] == pytest.approx(SD["O"], rel=1e-9)


def test_solve_lsq_covariance_matches_monte_carlo():
    rng = np.random.default_rng(5)
    A = rng.normal(size=(14, 6))
    C = rng.normal(size=(2, 6))
    e = rng.normal(size=2)
    b0 = rng.normal(size=14)
    sol = FT.solve_lsq(A, b0, C, e)
    Z = np.array([FT.solve_lsq(A, b0 + rng.normal(size=14), C, e).z for _ in range(3000)])
    assert np.allclose(np.cov(Z.T), sol.cov, atol=0.08 * np.abs(sol.cov).max())
    assert np.max(np.abs(C @ sol.z - e)) <= 1e-12 and sol.free_dim == 4


# --------------------------------------------------------------------------------------------- the solver
def _kkt(A, b, C, e, G, h, z):
    """Max stationarity residual and the most negative inequality multiplier of the solution z."""
    act = np.abs(G @ z - h) <= 1e-8
    K = np.vstack([C, G[act]]).T
    grad = 2.0 * A.T @ (A @ z - b)
    lam = np.linalg.lstsq(K, grad, rcond=None)[0]
    return float(np.max(np.abs(K @ lam - grad))), float(lam[len(C):].min(initial=0.0))


def test_qp_matches_scipy_slsqp_on_random_problems():
    from scipy.optimize import minimize
    for seed in range(40):
        r = np.random.default_rng(seed)
        n, m, p, q = 10, 16, 2, 14
        A, b = r.normal(size=(m, n)), 3.0 * r.normal(size=m)
        C = r.normal(size=(p, n))
        zf = r.normal(size=n)
        e = C @ zf
        G = r.normal(size=(q, n))
        h = G @ zf - 0.5 * np.abs(r.normal(size=q))                  # zf is strictly feasible
        sol = FT.solve_lsq(A, b, C, e, G, h)
        ref = minimize(lambda z: np.sum((A @ z - b) ** 2), zf, jac=lambda z: 2.0 * A.T @ (A @ z - b),
                       method="SLSQP", options={"ftol": 1e-14, "maxiter": 1000},
                       constraints=[{"type": "eq", "fun": lambda z: C @ z - e, "jac": lambda z: C},
                                    {"type": "ineq", "fun": lambda z: G @ z - h, "jac": lambda z: G}])
        assert np.max(np.abs(sol.z - ref.x)) <= 1e-4, seed
        assert sol.chi2 == pytest.approx(float(np.sum((A @ ref.x - b) ** 2)), rel=1e-7), seed
        assert np.max(np.abs(C @ sol.z - e)) <= 1e-12 and np.min(G @ sol.z - h) >= -1e-10
        stat, mu = _kkt(A, b, C, e, G, h, sol.z)                     # exact: KKT to rounding
        assert stat <= 1e-8 and mu >= -1e-8, seed
        assert sol.active.sum() == int(np.sum(np.abs(G @ sol.z - h) <= 1e-8))
    with pytest.raises(FT.Infeasible) as x:
        FT.solve_lsq(np.eye(2), np.zeros(2), None, None, np.array([[1.0, 0.0], [-1.0, 0.0]]), np.array([1.0, 0.0]))
    assert x.value.what == "bounds"
    with pytest.raises(FT.Infeasible) as x:
        FT.solve_lsq(np.eye(2), np.zeros(2), np.array([[1.0, 0.0], [1.0, 0.0]]), np.array([1.0, 2.0]))
    assert x.value.what == "equalities"


def test_mvc_weights_and_edge_gradient():
    P = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [5.0, 3.0], [0.0, 10.0]])    # non-convex
    p = np.array([3.0, 2.0])
    lam = FT.mvc_weights(P, p)
    assert lam.sum() == pytest.approx(1.0, abs=1e-14) and lam @ P == pytest.approx(p, abs=1e-12)
    X = np.array([64.0, 65.0, 63.5, 66.2, 64.4])
    for i in range(len(P)):
        a, b = P[i], P[(i + 1) % len(P)]
        m = a + 0.37 * (b - a)
        u = (b - a) / np.hypot(*(b - a))
        nin = np.array([-u[1], u[0]])
        g = FT.mvc_edge_gradient(P, i, m) @ X
        assert g @ u == pytest.approx((X[(i + 1) % len(P)] - X[i]) / np.hypot(*(b - a)), abs=1e-12)
        # one-sided second-order differences of the MVC surface converge to the boundary derivative
        h = 1e-3
        f = lambda s: float(FT.mvc_weights(P, m + s * nin) @ X)
        X0 = X[i] + 0.37 * (X[(i + 1) % len(P)] - X[i])
        assert g @ nin == pytest.approx((4 * f(h) - f(2 * h) - 3 * X0) / (2 * h), abs=1e-5)
        # linear precision of the boundary derivative: a linear function's gradient comes back exactly
        lin = 2.0 + 0.3 * P[:, 0] - 0.7 * P[:, 1]
        assert FT.mvc_edge_gradient(P, i, m) @ lin == pytest.approx([0.3, -0.7], abs=1e-12)


# --------------------------------------------------------------------------------------------- declarations
def crease_pair(sd: float, valley_deg: float = 1.0, shape: str = "ridge") -> dict:
    """Facets D (below y = 0) and U (above) meeting along P1 (20, 0) - P2 (60, 0), read as a slight valley."""
    g = math.tan(math.radians(valley_deg / 2.0))
    X1 = 66.0 + 8.0 * g                                    # D1 and U1 both rise: a valley of valley_deg
    nodes = {"P1": [20.0, 0.0, read(66.0, sd)], "P2": [60.0, 0.0, read(66.0, sd)], "U1": [40.0, 8.0, read(X1, sd)],
             "D1": [40.0, -8.0, read(X1, sd)]}
    return FX.straight(nodes=nodes, facets={"U": ["P1", "P2", "U1"], "D": ["P1", "D1", "P2"]},
                       creases=[{"facets": ["D", "U"], "shape": shape}])


def test_a_declaration_within_accept_dchi2_is_built_and_its_cost_reported():
    res = FT.fit_spec(crease_pair(0.3))
    (c,) = [c for c in res.creases if c["name"] == "D|U"]
    assert c["declared"] == "ridge" and c["shape"] == "ridge"
    assert c["deg"] >= FT.MIN_CREASE_DEG - 1e-9 and c["deg"] <= FT.MIN_CREASE_DEG + 0.05     # held at the minimum
    assert c["free_deg"] == pytest.approx(-1.0, abs=1e-9) and not c["certain"]
    assert 0.0 < c["dchi2"] <= FT.ACCEPT_DCHI2
    assert res.chi2 - res.chi2_free == pytest.approx(c["dchi2"], abs=1e-12)
    assert res.chi2_free <= 1e-15
    line = next(ln for ln in res.report_lines(limit=40) if ln.startswith("D|U"))
    assert f"readings-only -1.0 +- {c['free_sd_deg']:.1f} deg, cost +{c['dchi2']:.1f}" in line   # v3p3: deg said


def test_a_declaration_beyond_accept_dchi2_is_refused_with_facts_only():
    with pytest.raises(TempleError) as e:
        FT.fit_spec(crease_pair(0.05))
    x = e.value
    assert x.code == "E_FIT_SHAPE" and x.fields["dchi2"] > FT.ACCEPT_DCHI2
    (c,) = x.fields["creases"]
    assert c["pair"] == ["D", "U"] and c["declared"] == "ridge"
    assert c["free_deg"] == pytest.approx(-1.0, abs=1e-9) and c["dchi2"] == pytest.approx(x.fields["dchi2"], rel=1e-9)
    moves = c["moves"]
    assert len(moves) == 3 and [abs(m["sd"]) for m in moves] == sorted((abs(m["sd"]) for m in moves), reverse=True)
    assert {m["node"] for m in moves} <= {"P1", "P2", "U1", "D1"}
    assert all(m["sd"] == pytest.approx(m["mm"] / 0.05) for m in moves)
    assert x.message.startswith("the declared crease shapes: they cost chi2 +")
    # the readings it is against, unsigned and in sd: no amount to type (review P2-1; the signed mm are in the fields)
    assert (f"D|U declared ridge: readings-only -1.0 +- {c['free_sd_deg']:.1f} deg, cost +{c['dchi2']:.1f}, against "
            f"the readings of " + ", ".join(f"{m['node']} ({abs(m['sd']):.1f} sd)" for m in moves)) in x.message
    low = x.message.lower()
    assert not any(w in low for w in ("fix", "should", "instead", "try ", "do not", "redeclare", "consider", "move",
                                      " mm"))
    # one model (v3p2): the cost is the declared solution's own chi2 increase (it was the X cost dchi2_X beside a
    # positions-free dchi2; dchi2_X is gone)
    pb = FT._Problem(crease_pair(0.05))
    free, _ = FT._free_fit(pb)
    one = FT._declared_fit(pb, [(("D", "U"), "ridge")], free)
    assert c["dchi2"] == pytest.approx(one.chi2 - free.chi2, rel=1e-12) and "dchi2_X" not in c


def test_a_declaration_the_hard_values_forbid_is_refused_without_a_cost():
    """D and U as fixed coef planes (a refit of their fit declared a valley: held at the -2.5 deg minimum) declared a
    ridge: no fit can build it."""
    fitted = FT.fit_spec(crease_pair(0.3, shape="valley")).spec
    fitted["creases"] = [{"facets": ["D", "U"], "shape": "ridge"}]
    with pytest.raises(TempleError) as e:
        FT.fit_spec(fitted)
    x = e.value
    assert x.code == "E_FIT_SHAPE" and x.fields["dchi2"] is None
    (c,) = x.fields["creases"]
    assert c["dchi2"] is None and c["moves"] == [] and c["free_deg"] == pytest.approx(-FT.MIN_CREASE_DEG, abs=0.01)
    assert ("D|U declared ridge: readings-only -2.5 +- 0.0 deg, it cannot be built with the hard values (base nodes, "
            "coef planes) and t_min") in x.message


def test_a_declaration_that_already_holds_costs_nothing():
    res = FT.fit_spec(crease_pair(0.05, valley_deg=6.0, shape="valley"))
    (c,) = [c for c in res.creases if c["name"] == "D|U"]
    assert c["dchi2"] == 0.0 and c["deg"] == pytest.approx(-6.0, abs=1e-9) and c["certain"]
    assert res.chi2 == res.chi2_free


# --------------------------------------------------------------------------------------------- equalities
def tee(warp: bool) -> dict:
    """F (P, Q, R[, R2]) above the line y = -6; G (P, S, M) and H (M, S2, Q) below it; M on F's edge P-Q (not F's
    corner), read 1 mm off it."""
    nodes = {"P": [20.0, -6.0, read(64.0, 0.2)], "Q": [60.0, -6.0, read(64.8, 0.2)], "R": [20.0, 6.0, read(65.0, 0.2)],
             "M": [40.0, -6.0, read(65.4, 0.2)], "S": [30.0, -9.5, read(64.0, 0.3)],
             "S2": [50.0, -9.5, read(64.0, 0.3)]}
    facets = {"G": ["P", "S", "M"], "H": ["M", "S2", "Q"]}
    if warp:
        nodes["R2"] = [60.0, 6.0, read(64.1, 0.2)]
        facets["F"] = {"poly": ["P", "Q", "R2", "R"], "plane": "warp"}
    else:
        facets["F"] = ["P", "Q", "R"]
    return FX.straight(nodes=nodes, facets=facets)


@pytest.mark.parametrize("warp", [False, True])
def test_t_junction_lies_on_the_other_facets_surface(warp):
    res = FT.fit_spec(tee(warp))
    X = {n: r["X"] for n, r in res.nodes.items()}
    assert X["M"] == pytest.approx(0.5 * (X["P"] + X["Q"]), abs=1e-9)        # on F's edge P-Q, linear along it
    if not warp:
        a, b, c = res.spec["facets"]["F"]["plane"]["coef"]
        assert abs(a + b * 40.0 + c * -6.0 - X["M"]) <= 1e-9
    assert res.nodes["M"]["residual_mm"] < -0.3                              # the reading's 1 mm is spread, not kept
    for lab in ("G", "H"):
        a, b, c = res.spec["facets"][lab]["plane"]["coef"]
        for n in res.spec["facets"][lab]["poly"]:
            d, y = res.spec["nodes"][n][:2]
            assert abs(a + b * d + c * y - X[n]) <= 1e-9


def test_legacy_crease_plane_keeps_its_rise_and_reproduces_the_builders_planes():
    """prada_default (F1 by crease A-B + rise -0.38, typed A and C, base B, E, H, solved corners): nothing is
    over-determined, so the fit returns the builder's exact planes and the typed values unchanged."""
    spec = FX.prada_default()
    res = FT.fit_spec(spec)
    sol = build_temple(spec).sol
    for lab in ("F1", "F2", "F3", "F4"):
        assert res.spec["facets"][lab]["plane"]["coef"] == pytest.approx(list(sol.planes[lab].coef), abs=1e-9)
    assert res.chi2 <= 1e-15
    for n in ("A", "C"):
        assert res.nodes[n]["source"] == "typed" and res.nodes[n]["t"] == pytest.approx(spec["nodes"][n][2], abs=1e-9)
    from glasses3d.g3d.temple.planes import crease_frame
    from glasses3d.g3d.temple.spec import parse
    ps = parse(spec)
    _, n_in, _ = crease_frame(ps.nodes["A"], ps.nodes["B"], True)
    b, c = res.spec["facets"]["F1"]["plane"]["coef"][1:]
    assert b * n_in[0] + c * n_in[1] == pytest.approx(-0.38, abs=1e-12)
    assert res.nodes["K"]["source"] == "solve" and not res.nodes["K"]["prior"]     # set by F1's plane


def test_thickness_bound_holds_and_is_reported():
    nodes = {"P": [20.0, -6.0, read(64.0, 0.2)], "Q": [60.0, -6.0, read(64.0, 0.2)],
             "R": [60.0, 6.0, read(60.2, 0.2)], "S": [20.0, 6.0, read(60.2, 0.2)]}     # R, S read 0.2 mm thick
    res = FT.fit_spec(FX.straight(nodes=nodes, facets={"T": ["P", "Q", "R", "S"]}))
    for n in ("R", "S"):
        assert res.nodes[n]["t"] >= 1.0 and res.nodes[n]["t"] == pytest.approx(1.0, abs=1e-5)
        assert res.nodes[n]["at_bound"] and res.nodes[n]["sd_X"] <= 1e-9
    assert not res.nodes["P"]["at_bound"] and res.chi2 > 20.0
    a, b, c = res.spec["facets"]["T"]["plane"]["coef"]
    for n, (d, y, _) in res.spec["nodes"].items():
        assert abs(a + b * d + c * y - res.nodes[n]["X"]) <= 1e-9


def test_hard_values_that_cannot_hold_are_refused_e_fit():
    s = FX.straight(inner=[[0.0, 60.0], [50.0, 60.0], [120.0, 50.0]],
                    nodes={"P1": [10.0, 5.0, "base"], "P2": [50.0, 8.0, "base"], "P3": [90.0, 5.0, "base"],
                           "P4": [50.0, -8.0, "base"]}, facets={"F": ["P1", "P4", "P3", "P2"]})
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    assert e.value.code == "E_FIT" and e.value.fields["reason"] == "equalities" and "facet F corner" in e.value.message
    s = FX.straight(base={"top": [[0.0, 0.5], [120.0, 0.5]], "bottom": [[0.0, 0.5], [120.0, 0.5]]},
                    nodes={"P": [20.0, -6.0, "base"], "Q": [60.0, -6.0, read(64.0, 0.2)],
                           "R": [40.0, 6.0, read(64.0, 0.2)]}, facets={"F": ["P", "Q", "R"]})
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    assert e.value.code == "E_FIT" and e.value.fields["reason"] == "bounds"


@pytest.mark.parametrize("bad, code", [
    (lambda s: s["nodes"]["O"].__setitem__(2, {"read": {"X": 66.0}}), "E_SPEC"),             # no sd
    (lambda s: s["nodes"]["O"].__setitem__(2, {"read": {"X": 66.0, "sd": 0.0}}), "E_SPEC"),
    (lambda s: s["nodes"]["O"].__setitem__(2, {"read": {"X": 66.0, "sd": 0.2, "Xx": 1}}), "E_SPEC"),
    (lambda s: s["nodes"]["O"].__setitem__(2, {"reading": {"X": 66.0, "sd": 0.2}}), "E_SPEC"),
    (lambda s: s["facets"].__setitem__("N", {"poly": ["NW", "O", "NE"], "plane": "warp"}), "E_SPEC"),   # 3 corners
    (lambda s: s["facets"].__setitem__("N", {"poly": ["NW", "O", "NE"], "plane": "curved"}), "E_SPEC"),
    (lambda s: s["facets"].__setitem__("N", {"poly": ["NW", "O", "NE"], "soft": "yes"}), "E_SPEC"),
    (lambda s: s["facets"].__setitem__("N", {"poly": ["NW", "O", "NE"], "colour": 1}), "E_SPEC"),
])
def test_malformed_designs_are_refused(bad, code):
    s = layout1()
    bad(s)
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    assert e.value.code == code


# --------------------------------------------------------------------------------------------- warnings, report
def test_prior_only_node_warns_prior_and_a_face_determined_one_warns_solved():
    """Review P1-1: a null node inside the outline set by a face's plane is a fact line (W_FIT_SOLVED: the face, the
    corners it is set through, the nearest one's distance, X +- sd), never silent; the prior-only one is W_FIT_PRIOR."""
    nodes = {"P": [20.0, -6.0, read(64.0, 0.2)], "Q": [60.0, -6.0, read(64.5, 0.2)],
             "R": [60.0, 6.0, read(65.0, 0.2)], "S": [20.0, 6.0, None]}
    res = FT.fit_spec(FX.straight(nodes=nodes, facets={"T": {"poly": ["P", "Q", "R", "S"], "plane": "warp"}}))
    (w,) = res.warnings
    assert w["code"] == "W_FIT_PRIOR" and w["node"] == "S" and "set by no reading or face" in w["message"]
    assert res.nodes["S"]["source"] == "solve" and res.nodes["S"]["X"] == pytest.approx(64.0, abs=1e-9)
    assert res.nodes["S"]["sd_X"] == pytest.approx(FT.PRIOR_SD_MM, rel=1e-9) and res.nodes["S"]["prior"]
    res = FT.fit_spec(FX.straight(nodes=nodes, facets={"T": ["P", "Q", "R", "S"]}))
    assert res.nodes["S"]["sd_X"] < 0.5 and not res.nodes["S"]["prior"]
    assert res.nodes["S"]["X"] == pytest.approx(64.5, abs=1e-9)     # the plane of P, Q, R: the prior does not pull
    (w,) = res.warnings
    s = res.nodes["S"]
    assert w["code"] == "W_FIT_SOLVED" and w["faces"] == ["T"] and w["through"] == ["P", "Q", "R"]
    assert w["nearest"] == "P" and w["nearest_mm"] == pytest.approx(12.0) and not w["at_bound"]
    assert w["message"] == (f"node S: no reading or typed value; X 64.50 +- {s['sd_X']:.2f} mm (t 4.50 mm) from face "
                            f"T through P, Q, R, 12.0 mm from its nearest observed corner P")     # v3p3: mm said


def test_a_far_virtual_corner_of_a_read_face_is_set_by_the_face_not_the_prior():
    """V, 62 mm off three corners 4 mm apart, extrapolates the face's tilt: sd > PRIOR_FLAG_MM, but a face sets it
    (W_FIT_PRIOR says "set by no reading or face", which would be false)."""
    nodes = {"P": [20.0, -2.0, read(64.0, 0.3)], "Q": [24.0, -2.0, read(64.2, 0.3)], "R": [20.0, 2.0, read(63.9, 0.3)],
             "V": [24.0, 60.0, None]}
    res = FT.fit_spec(FX.straight(nodes=nodes, facets={"T": ["P", "Q", "V", "R"]}))
    v = res.nodes["V"]
    assert v["sd_X"] > FT.PRIOR_FLAG_MM and not v["prior"] and not v["inside"] and not res.warnings
    assert v["X"] == pytest.approx(64.0 + 0.2 + (63.9 - 64.0) * 62.0 / 4.0, abs=1e-9)        # on the plane of P, Q, R


def test_typed_value_off_its_stored_reading_warns_as_a_fact():
    s = FX.straight(nodes={"P": [20.0, -6.0, 4.0], "Q": [60.0, -6.0, 4.5], "E": [40.0, 6.0, 6.5]},
                    facets={"T": ["P", "Q", "E"]})
    res = FT.fit_spec(s, readings={"E": {"X": 65.27, "sd_X": 0.4}, "P": {"X": 64.1, "sd": 0.4}})
    (w,) = res.warnings
    assert w["code"] == "W_FIT_OFF_READING" and w["message"] == "typed E 6.50: its reading 5.27 +- 0.40 (+3.1 sd)"
    assert res.nodes["E"]["source"] == "typed" and res.nodes["E"]["obs_sd"] == FT.TYPED_SD_MM


def test_report_lines_are_compact_ordered_and_keep_warnings():
    """The fit line, then the misfit line (review P2-5: where the fit departs from the readings), face lines, crease
    lines, warnings. The short form keeps the fit lines and the warnings (limit 8: the misfit line is one more fit
    line than before)."""
    res = FT.fit_spec(run7_layout("nodes"))
    lines = res.report_lines(limit=40)
    assert all(len(ln) <= FT.REPORT_CHARS for ln in lines)
    assert lines[0].startswith("fit: chi2 21.5, dof 1, from 7 readings and 0 typed values")
    assert lines[1] == ("misfit beyond 1 sd (fitted - read): E X +1.72 mm (3.9 sd), K X -0.24 mm (1.8 sd), A X +0.52 "
                        "mm (1.7 sd)")
    assert lines[2] == ("face crown flat (A K E H): chi2 21.5 for 1 extra corner, not one plane; its largest misfit "
                        "(fitted - read): E X +1.72 mm (3.9 sd)")
    # v3p3-clean-fit: "flat faces with no extra corner" said what "extra" means nowhere on its own line
    assert lines[3] == ("flat faces with fewer than 4 read, typed or base corners (no flatness test): logo, lower, "
                        "nose, shoulder")
    kinds = ["fit" if ln.startswith(("fit", "misfit", "at the thickness")) else "face"
             if ln.startswith(("face", "flat faces")) else "warn" if ln.startswith("W_")
             else "cont" if ln.startswith("  ") else "crease" for ln in lines]
    order = [k for k in kinds if k != "cont"]
    assert order == sorted(order, key=["fit", "face", "crease", "warn"].index)
    short = res.report_lines(limit=8)
    assert len(short) == 8 and short[:2] == lines[:2]
    # the last line says what was left out, by kind (v3p3-clean-fit: "(+N more lines in the fit report)" named a
    # report no tool shows)
    of = [k if k != "cont" else None for k in kinds]
    for i in range(1, len(of)):
        of[i] = of[i] or of[i - 1]                   # a wrapped line belongs to the line it continues
    left = {}
    for ln, k in zip(lines, of):
        if ln not in short[:-1]:
            left[k] = left.get(k, 0) + 1
    assert set(left) <= {"face", "crease"} and left.get("crease", 0) > 0
    assert short[-1] == "(left out for length: " + ", ".join(
        f"{left[k]} {k} line{'s' if left[k] > 1 else ''}" for k in ("face", "crease") if k in left) + ")"
    assert any(ln.startswith("W_FACE_NOT_FLAT: face crown") for ln in short)
    assert any(ln.startswith("  ways (unranked): warp (one face, no line)") for ln in short)
    assert not any(ln.startswith("crown|logo") for ln in res.report_lines(limit=40, creases=False))


# --------------------------------------------------------------------------------------------- determinism, speed
def test_deterministic():
    s = run7_layout("warp")
    a, b = FT.fit_spec(s), FT.fit_spec(copy.deepcopy(s))
    ja = json.dumps(a.to_dict(), sort_keys=True, allow_nan=False)            # strict JSON: no NaN or inf anywhere
    assert ja == json.dumps(b.to_dict(), sort_keys=True, allow_nan=False)


def strip(n: int = 15, seed: int = 0) -> dict:
    """30 nodes: a strip of 14 flat quads (each over-determined by one corner) rising and falling 1.2 mm every
    7.5 mm, every node read with noise, every quad|quad crease declared."""
    rng = np.random.default_rng(seed)
    nodes = {}
    for k in range(n):
        X = 64.0 + (1.2 if k % 2 else 0.0)
        for row, y in (("T", 6.0), ("B", -6.0)):
            nodes[f"{row}{k:02d}"] = [4.0 + 7.5 * k, y, read(X + rng.normal(0.0, 0.2), 0.3)]
    facets = {f"Q{k:02d}": [f"T{k:02d}", f"B{k:02d}", f"B{k + 1:02d}", f"T{k + 1:02d}"] for k in range(n - 1)}
    creases = [{"facets": [f"Q{k:02d}", f"Q{k + 1:02d}"], "shape": "ridge" if k % 2 == 0 else "valley"}
               for k in range(n - 2)]
    return FX.straight(nodes=nodes, facets=facets, creases=creases)


def test_speed_30_nodes_under_50_ms():
    s = strip()
    res = FT.fit_spec(s)
    assert len(res.nodes) == 30 and len([c for c in res.creases if c["declared"]]) == 13
    best = math.inf
    for _ in range(3):                            # CPU time, so other workers sharing the machine do not count;
        c0 = time.process_time()                  # batches of 10: Windows' process clock ticks at ~15.6 ms
        for _ in range(10):
            FT.fit_spec(s)
        best = min(best, (time.process_time() - c0) / 10)
    assert best < 0.050, best


# --------------------------------------------------------------------------------------------- real data (run 7)
RUN7_HINGE = {"z_mm": -10.600000619888306, "axis_xz_mm": [64.60000574588776, -10.100000537931919], "opening_deg": 0.0}
RUN7_TOP_ARM = [[85.03, 7.66], [90.0, 7.45], [92.0, 7.5013], [94.0, 7.2164], [96.0, 6.8674], [98.0, 6.4296],
                [100.0, 5.8483], [101.0, 5.5], [102.0, 5.0934], [103.0, 4.6596], [104.0, 4.1669], [105.0, 3.6212],
                [107.0, 2.429], [109.0, 1.121], [111.0, -0.3027], [113.0, -1.812], [116.0, -4.1908], [119.0, -6.5345],
                [121.0, -7.9986], [123.0, -9.4234], [125.0, -10.7529], [127.0, -12.0049], [129.0, -13.1691],
                [131.0, -14.2309], [132.0, -14.7164], [133.0, -15.1673], [134.0, -15.5754], [135.0, -15.9284],
                [136.0, -16.2815]]
RUN7_BOT_ARM = [[85.0, 1.6], [87.0, 1.8815], [89.0, 2.0155], [92.0, 2.0637], [94.0, 1.9757], [96.0, 1.7562],
                [98.0, 1.4011], [100.0, 0.9152], [101.0, 0.5623], [102.0, 0.1482], [103.0, -0.3311], [104.0, -0.8921],
                [105.0, -1.5482], [107.0, -3.0445], [109.0, -4.7556], [111.0, -6.6261], [113.0, -8.5544],
                [116.0, -11.3624], [119.0, -13.9595], [121.0, -15.6908], [123.0, -17.419], [125.0, -19.1248],
                [127.0, -20.794], [129.0, -22.3882], [131.0, -23.8729], [132.0, -24.5338], [133.0, -25.1155],
                [134.0, -25.5773], [135.0, -25.8931], [136.0, -26.2089]]
RUN7_INNER = [[0.01, 65.9322], [1.0, 65.1571], [4.0, 64.8786], [6.0, 64.6929], [7.0, 64.6], [11.0, 64.2286],
              [16.0, 63.7643], [17.0, 63.6714], [23.0, 63.1143], [25.0, 62.9286], [26.0, 62.8357], [28.0, 62.6538],
              [29.0, 62.6326], [33.0, 62.5631], [36.0, 62.511], [37.0, 62.4936], [41.0, 62.4241], [43.0, 62.3893],
              [44.0, 62.3719], [46.0, 62.3372], [48.0, 62.3024], [50.0, 62.2677], [54.0, 62.1987], [61.0, 62.1049],
              [66.0, 62.0417], [67.0, 61.9958], [69.0, 61.882], [72.0, 61.6615], [76.0, 61.3674], [79.0, 61.1468],
              [82.0, 60.9263], [85.0, 60.7057], [87.0, 60.5587], [89.0, 60.4117], [92.0, 60.1911], [94.0, 60.0441],
              [96.0, 59.897], [98.0, 59.6905], [100.0, 59.2343], [101.0, 58.9764], [102.0, 58.7185], [103.0, 58.4607],
              [104.0, 58.2028], [105.0, 57.9449], [107.0, 57.4292], [109.0, 56.9135], [111.0, 56.3688],
              [113.0, 55.4919], [116.0, 54.1331], [119.0, 52.7741], [121.0, 51.8483], [123.0, 50.9021], [125.0, 50.007],
              [127.0, 49.4382], [129.0, 48.9206], [131.0, 48.4029], [132.0, 48.1441], [133.0, 47.8853],
              [134.0, 47.6265], [135.0, 47.3677], [136.0, 47.1088]]
RUN7_BASE_TOP = [[0.0, 4.42], [10.0, 5.32], [20.0, 5.53], [30.0, 5.58], [36.55, 5.23], [40.0, 5.26], [50.0, 5.32],
                 [60.0, 5.36], [70.0, 5.56], [75.64, 5.92], [80.0, 6.19], [85.03, 6.5], [90.0, 5.6], [95.0, 4.7],
                 [100.0, 4.45], [101.0, 4.4318], [102.0, 4.4091], [103.0, 4.3864], [104.0, 4.3636], [105.0, 4.3409],
                 [107.0, 4.2955], [109.0, 4.25], [111.0, 4.231], [113.0, 4.1591], [116.0, 4.0909], [119.0, 4.0229],
                 [121.0, 4.0176], [123.0, 4.0529], [125.0, 4.0792], [127.0, 4.1235], [129.0, 4.1588], [131.0, 4.1941],
                 [132.0, 4.2118], [133.0, 4.2294], [134.0, 4.2471], [135.0, 4.2647], [136.0, 4.3885]]
RUN7_BASE_BOT = [[0.0, 4.15], [10.0, 5.77], [20.0, 6.71], [22.5, 6.93], [30.0, 6.94], [40.0, 6.41], [50.0, 5.86],
                 [60.0, 5.29], [70.0, 4.89], [75.64, 4.9], [80.0, 5.4], [85.03, 5.8], [90.0, 5.3], [95.0, 4.7],
                 [100.0, 4.45], [101.0, 4.4318], [102.0, 4.4091], [103.0, 4.3864], [104.0, 4.3636], [105.0, 4.3409],
                 [107.0, 4.2955], [109.0, 4.25], [111.0, 4.2091], [113.0, 4.1591], [116.0, 4.0909], [119.0, 4.0232],
                 [121.0, 4.0176], [123.0, 4.0529], [125.0, 4.1079], [127.0, 4.1235], [129.0, 4.1588], [131.0, 4.1941],
                 [132.0, 4.2118], [133.0, 4.2294], [134.0, 4.2471], [135.0, 4.2647], [136.0, 4.0509]]
# (d, y, X, sd): A H K E crown-geometry §2 bundle (A moved from d 0.43 to the hinge plane: the crown's X changes
# ~0.005 mm per mm of d); C the crease-line corner C' (midblock-settled §2.1); L, D target.md table 2.1 (L's X the
# middle of 70.30-70.98, its pick residuals 3-5 px; D B1+B2 only)
RUN7_READ = {"A": (0.0, 10.13, 68.74, 0.31), "H": (36.35, 15.11, 67.80, 0.24), "K": (46.95, 9.65, 70.07, 0.13),
             "E": (75.52, 9.43, 68.43, 0.44), "C": (22.10, -15.84, 70.69, 0.35), "L": (0.0, -9.55, 70.64, 0.40),
             "D": (76.8, -0.7, 65.50, 0.50)}
RUN7_FACETS = {"crown": ["A", "K", "E", "H"], "logo": ["A", "C", "K"], "lower": ["C", "D", "K"],
               "nose": ["A", "L", "C"], "shoulder": ["K", "D", "E"]}
RUN7_CREASES = [{"facets": ["crown", "logo"], "shape": "ridge"}, {"facets": ["crown", "shoulder"], "shape": "ridge"},
                {"facets": ["logo", "lower"], "shape": "ridge"}, {"facets": ["logo", "nose"], "shape": "ridge"},
                {"facets": ["lower", "shoulder"], "shape": "valley"},
                {"facets": ["shoulder", "base"], "shape": "valley"}]


def run7_layout(crown: str = "nodes", creases=(), rd: dict | None = None) -> dict:
    """Run 7's final layout and tables with the settled readings (module docstring; `rd` another readings table of the
    same corners); crown "nodes" or "warp". The outline is spliced through A, H, E (top) and L, C, D (bottom)."""
    rd = RUN7_READ if rd is None else rd
    top = [list(rd[n][:2]) for n in ("A", "H", "E")] + RUN7_TOP_ARM
    bottom = [list(rd[n][:2]) for n in ("L", "C", "D")] + RUN7_BOT_ARM
    nodes = {n: [d, y, {"read": {"id": f"{n}-settled", "X": X, "sd": sd, "d": d, "y": y}}]
             for n, (d, y, X, sd) in rd.items()}
    facets = {k: {"poly": list(v), "plane": crown if k == "crown" else "nodes"} for k, v in RUN7_FACETS.items()}
    return {"schema": "g3d.temple/2", "hinge": copy.deepcopy(RUN7_HINGE),
            "outline": {"top": top, "bottom": bottom, "tip_round_mm": 0.65}, "inner": copy.deepcopy(RUN7_INNER),
            "base": {"top": copy.deepcopy(RUN7_BASE_TOP), "bottom": copy.deepcopy(RUN7_BASE_BOT)}, "nodes": nodes,
            "facets": facets, "creases": copy.deepcopy(list(creases)), "chamfers": [], "roll_mm": 0.25,
            "options": {"t_min_mm": 1.0, "station_step_mm": 2.0, "planar_tol_mm": 1e-6, "kink_sharp_deg": 25.0,
                        "border_band_mm": 2.0, "snap_mm": 0.05}}


def test_real_run7_flat_crown_is_not_flat():
    """Tool test on mixed inputs (module docstring). One extra corner: the four leave-one-out deviations share one
    sigma, so the warning lists them all (K +1.52 from the plane of A, E, H: the settled +1.5 +- 0.35, crown-geometry
    §3) and says where the flat fit puts the misfit (E +1.72 mm, 3.9 sd), never one corner as the culprit (review
    P1-2: the old tie-break named K, the best-read corner, whatever the readings said)."""
    res = FT.fit_spec(run7_layout("nodes"))
    (w,) = [w for w in res.warnings if w["code"] == "W_FACE_NOT_FLAT"]
    assert w["face"] == "crown" and w["extra"] == 1 and "node" not in w
    dev = {x["node"]: x for x in w["deviations"]}
    assert 1.3 <= dev["K"]["mm"] <= 1.8 and dev["H"]["in_line"]
    assert dev["A"]["sigma"] == pytest.approx(dev["K"]["sigma"], rel=1e-9) == pytest.approx(dev["E"]["sigma"])
    assert dev["K"]["sigma"] == pytest.approx(math.sqrt(w["chi2"]), rel=1e-9) and dev["K"]["sigma"] > 3.0
    assert w["message"].startswith("face crown: its corners do not fit one flat face (chi2 21.5 for 1 extra corner): "
                                   "each corner 4.6 sd from the plane of the other three (with one extra corner the "
                                   "readings cannot say which corner is off): A -3.96 +- 0.85 mm, K +1.52 +- 0.33, E "
                                   "-2.42 +- 0.52, H (others nearly in line); the flat fit (fitted - read): E X +1.72 "
                                   "mm (3.9 sd), K X -0.24 mm (1.8 sd), A X +0.52 mm (1.7 sd), H X -0.01 mm (0.0 sd); ")
    assert w["message"].endswith(FT.WAYS)
    assert [x["node"] for x in w["fit"]] == ["E", "K", "A", "H"]
    assert res.faces["crown"]["not_flat"] and res.dof == 1
    assert [x["code"] for x in res.warnings] == ["W_FACE_NOT_FLAT"]
    assert res.nodes["E"]["residual_sd"] > 3.0                       # the flat crown pulls E 1.7 mm out


def test_real_run7_warp_crown_fits_within_its_readings():
    res = FT.fit_spec(run7_layout("warp"))
    assert max(abs(r["residual_sd"]) for r in res.nodes.values()) < 1e-6 and res.chi2 < 1e-9
    assert not res.warnings and res.faces["crown"]["kind"] == "warp"
    assert 0.5 < res.faces["crown"]["warp_mm"] < 1.5
    for r in (res, FT.fit_spec(run7_layout("nodes"))):
        cr = {c["name"]: c for c in r.creases}
        assert 15.0 <= cr["crown|logo"]["deg"] <= 25.0 and cr["crown|logo"]["certain"]   # photos 15-25 (target §2.3)
        assert 7.7 <= cr["logo|lower"]["deg"] <= 12.3 and cr["logo|lower"]["certain"]   # photos 10.0 (7.7-12.3)
        assert not cr["logo|nose"]["certain"]                                            # within error of flat


def test_real_run7_declarations_are_refused_where_the_readings_disagree():
    """Tool test on mixed inputs (module docstring): the refusals describe these inputs, not the temple."""
    with pytest.raises(TempleError) as e:
        FT.fit_spec(run7_layout("nodes", RUN7_CREASES))
    assert e.value.code == "E_FIT_SHAPE"
    assert [c["pair"] for c in e.value.fields["creases"]] == [["logo", "nose"], ["shoulder", "base"]]
    with pytest.raises(TempleError) as e:
        FT.fit_spec(run7_layout("warp", RUN7_CREASES))
    assert [c["pair"] for c in e.value.fields["creases"]] == [["logo", "nose"]]
    # declaring what the readings say builds
    free = FT.fit_spec(run7_layout("warp"))
    decl = [{"facets": c["pair"], "shape": c["shape"]} for c in free.creases if c["certain"]]
    res = FT.fit_spec(run7_layout("warp", decl))
    assert res.chi2 - res.chi2_free <= 1e-9 and len(decl) >= 4


# -------------------------------------------------------------------------------------------- review fixes (v3-fit-fix)
# Each test below is the regression test of one finding of review-fit-correctness.md (F1-F7) or review-fit-design.md
# (P1-x, P2-x, P3-x), on the probe that showed it (v3/probes/fit-*). Their builds are in tests/test_temple_fit_build.py.
def dense_min(res, label: str, step: float = 0.1) -> float:
    """The thinnest point of a fitted facet inside the outline, independent of the fit's bound points: its surface
    (the coef plane, or planes.mvc over the fitted corners) minus x_in, the chamfer and max(roll_mm, roll depth), on a
    `step` grid and 400 points per outline edge inside the facet."""
    s = res.spec
    ps = parse(s)
    poly = ccw_poly(s["facets"][label]["poly"], ps.nodes)
    P = np.array([ps.nodes[n][:2] for n in poly], np.float64)
    B = ps.boundary()
    lo, hi = P.min(axis=0), P.max(axis=0)
    g = np.stack(np.meshgrid(np.arange(lo[0], hi[0] + step, step), np.arange(lo[1], hi[1] + step, step)),
                 axis=-1).reshape(-1, 2)
    g = g[points_in_poly(g, B)]
    ob = np.vstack([np.linspace(B[i], B[(i + 1) % len(B)], 400) for i in range(len(B))])
    pts = np.vstack([g, ob])
    pts = pts[points_in_poly(pts, P)]
    d, y = pts[:, 0], pts[:, 1]
    plane = s["facets"][label]["plane"]
    if plane == "warp":
        F = np.array([float(ps.inner(ps.nodes[n][0])) + ps.nodes[n][2] for n in poly])
        X = mvc(P, F, d, y)
    else:
        a, b, c = plane["coef"]
        X = a + b * d + c * y
    T = X - ps.inner(d) - chamfer_depth(ps, d, y) - np.maximum(ps.roll_mm, roll_depth(ps, d, y))
    return float(T.min())


def pyramid_slab(apex_X: float, roll: float) -> dict:
    """Probe p1: the straight slab's four-facet pyramid (x_in 60), its apex O read apex_X, roll_mm roll."""
    return FX.straight(nodes={"NW": [0.0, 10.0, read(64.0, 0.2)], "NE": [40.0, 10.0, read(64.0, 0.2)],
                              "SE": [40.0, -10.0, read(64.0, 0.2)], "SW": [0.0, -10.0, read(64.0, 0.2)],
                              "O": [20.0, 0.0, read(apex_X, 0.2)]},
                       facets={"N": ["NW", "O", "NE"], "E": ["NE", "O", "SE"], "S": ["SE", "O", "SW"],
                               "W": ["SW", "O", "NW"]}, roll_mm=roll, options={"t_min_mm": 1.0})


def outline_corner_design(off: float) -> dict:
    """Probe p2: a flat facet with virtual corners W, U covering the slab's hinge-end bottom corner (0, -10), its plane
    t = off there (rising 0.1 per mm in d and y); every node it lists reads that plane."""
    pl = lambda d, y: 60.0 + off + 0.1 * d + 0.1 * (y + 10.0)                 # noqa: E731
    return FX.straight(nodes={"W": [-5.0, 5.0, None], "U": [-5.0, -15.0, None],
                              "S": [30.0, -15.0, read(pl(30, -15), 0.2)], "R": [30.0, 5.0, read(pl(30, 5), 0.2)],
                              "P": [10.0, 6.0, read(pl(10, 6), 0.2)]},
                       facets={"T": ["W", "U", "S", "R", "P"]}, options={"t_min_mm": 1.0})


def chamfer_knot_design() -> dict:
    """A flat facet across the slab's top edge over a concave inner knot (d 50: x_in 60 -> 61 -> 59.6) and a tapered top
    chamfer (d 20-80, 2 mm x 0.8 mm, taper 5), read 0.2 mm thick at the knot: its thinnest points lie on the outline at
    the knot and the chamfer's breaks, never at a node (module docstring of fit.py)."""
    return FX.straight(nodes={"P": [10.0, 2.0, read(61.2, 0.2)], "Q": [95.0, 2.0, read(61.2, 0.2)],
                              "R": [95.0, 14.0, read(61.2, 0.2)], "S": [10.0, 14.0, read(61.2, 0.2)]},
                       facets={"T": ["P", "Q", "R", "S"]}, inner=[[0.0, 60.0], [50.0, 61.0], [120.0, 59.6]],
                       chamfers=[{"edge": "top", "d": [20.0, 80.0], "width_mm": 2.0, "depth_mm": 0.8, "taper_mm": 5.0}],
                       options={"t_min_mm": 1.0})


def shared_edge_design() -> dict:
    """Probe p3: F1 and F2 share the edge N1 (20, 0) -> V (60, 30), V null above the top edge (y 10); their readings
    put the planes 1 mm apart where that edge crosses the outline, (33.33, 10)."""
    f1 = lambda d, y: 64.0 + 0.05 * d                                          # noqa: E731
    f2 = lambda d, y: 64.0 + 0.05 * d + 0.1 * y                                # noqa: E731
    return FX.straight(nodes={"N1": [20.0, 0.0, read(f1(20, 0), 0.2)], "V": [60.0, 30.0, None],
                              "Ta": [0.0, 30.0, read(f1(0, 30), 0.2)], "Tb": [0.0, 0.0, read(f1(0, 0), 0.2)],
                              "Rb": [60.0, -5.0, read(f2(60, -5), 0.2)], "Rc": [65.0, 8.0, read(f2(65, 8), 0.2)]},
                       facets={"F1": ["N1", "V", "Ta", "Tb"], "F2": ["N1", "Rb", "Rc", "V"]})


L_POS = {"a": (2.0, -9.0), "b": (60.0, -9.0), "c": (60.0, -2.0), "r": (12.0, -2.0), "e": (12.0, 9.0), "f": (2.0, 9.0)}


def l_warp_design(low: float, high: float) -> dict:
    """Probe p6: an L-shaped (non-convex) warp on the slab, its corners read `low` but the hidden corner e `high`
    (x_in 60): mean value coordinates weigh e negatively behind the reflex corner r, so the interior dips."""
    return FX.straight(nodes={n: [*p, read(high if n == "e" else low, 0.1)] for n, p in L_POS.items()},
                       facets={"L": {"poly": list(L_POS), "plane": "warp"}}, options={"t_min_mm": 1.0})


def test_no_facets_fits_to_the_design_itself():
    """F1: every lab package's revision 0 has no facets (p5: all 108 saved r0000.json raised ValueError); the fit
    returns the design, a reading on a node in no facet becomes its t."""
    for s in (FX.straight(), FX.slab()):
        res = FT.fit_spec(s)
        assert res.spec == s and res.chi2 == 0.0 and res.n_obs == 0 and res.dof == 0
        assert not res.creases and not res.faces and not res.warnings and res.nodes == {}
    res = FT.fit_spec(FX.straight(nodes={"Z": [10.0, 0.0, read(64.5, 0.2)]}))
    assert res.spec["nodes"]["Z"] == [10.0, 0.0, pytest.approx(4.5, abs=1e-12)] and res.chi2 == 0.0


@pytest.mark.parametrize("apex_X, roll, t_O", [(60.5, 0.0, 1.0), (61.1, 0.0, 1.1), (60.5, 0.25, 1.25),
                                               (61.1, 0.25, 1.25)])
def test_thickness_bound_carries_the_pre_roll_limit(apex_X, roll, t_O):
    """F2 (p1): with a roll the builder needs the pre-roll section >= t_min + roll_mm at every vertex (E_ROLL), not
    t_min + the roll depth (0 off the outline): an interior node read 0.5 or 1.1 mm thick is held at 1.25."""
    res = FT.fit_spec(pyramid_slab(apex_X, roll))
    o = res.nodes["O"]
    assert o["t"] >= t_O and o["t"] == pytest.approx(t_O, abs=2e-6)
    assert o["at_bound"] == (t_O > apex_X - 60.0 + 1e-9)
    assert dense_min(res, "N") >= 1.0


def test_outline_vertex_inside_a_facet_is_bounded():
    """F3 (p2): a facet covering an outline vertex (the hinge-end bottom corner) is bounded there; before, its plane
    was t -0.5 there with every bounded point >= 1."""
    res = FT.fit_spec(outline_corner_design(-0.5))
    a, b, c = res.spec["facets"]["T"]["plane"]["coef"]
    assert a - 10.0 * c - 60.0 >= 1.0 and a - 10.0 * c - 60.0 == pytest.approx(1.0, abs=2e-6)
    assert res.bounds == [{"what": "outline", "facet": "T", "at": [0.0, -10.0]}]
    assert ("outline", "T", [0.0, -10.0]) in FT._Problem(outline_corner_design(-0.5)).bound_at
    assert dense_min(res, "T") >= 1.0
    res = FT.fit_spec(outline_corner_design(1.5))                          # thick enough: every reading kept
    assert res.chi2 < 1e-20 and not res.bounds


def test_chamfer_and_concave_knot_points_on_the_outline_are_bounded():
    """F3: the outline's crossings with the event lines (the concave inner knot, the chamfer's breaks) inside a facet
    are bound points; a dense grid of the fitted facet finds nothing thinner."""
    s = chamfer_knot_design()
    pb = FT._Problem(s)
    assert list(pb.events) == [20.0, 25.0, 50.0, 75.0, 80.0]
    pts = {tuple(round(v, 9) for v in b[2]) for b in pb.bound_at if b[0] == "outline"}
    assert {(20.0, 10.0), (25.0, 10.0), (50.0, 10.0), (75.0, 10.0), (80.0, 10.0)} <= pts
    res = FT.fit_spec(s)
    assert dense_min(res, "T") >= 1.0
    assert any(b["what"] == "outline" for b in res.bounds) and res.chi2 > 1.0


def test_a_virtual_node_is_one_value_for_facets_its_shared_edge_binds():
    """F4 (p3): F1 and F2 share an edge from the null virtual node V that reaches inside the outline, so the builder
    binds them at its outline crossing; V is one unknown there (it was split, the planes 1 mm apart: E_RELIEF_STEP).
    Prada's A_l and C_lo, whose shared edges lie wholly outside, stay one value per facet."""
    s = shared_edge_design()
    assert FT._Problem(s).split == {}
    res = FT.fit_spec(s)
    q = (20.0 + 40.0 / 3.0, 10.0)
    X1, X2 = (a + b * q[0] + c * q[1] for a, b, c in (res.spec["facets"][l]["plane"]["coef"] for l in ("F1", "F2")))
    assert X1 == pytest.approx(X2, abs=1e-9) and isinstance(res.spec["nodes"]["V"][2], float)
    assert res.chi2 > 1.0                                                  # the 1 mm the readings disagree by
    pb = FT._Problem(FX.prada_default())
    assert sorted(pb.split) == ["A_l", "C_lo"] and all(len(v) == 2 for v in pb.split.values())


@pytest.mark.parametrize("low, high, cut", [(61.1, 75.0, True), (60.9, 70.0, True), (61.2, 70.0, False)])
def test_non_convex_warp_interior_is_held_at_t_min(low, high, cut):
    """F5 (p6): a non-convex warp's interior dips below every corner and edge (the hidden corner weighs negatively);
    the cutting planes hold it (corners 1.1 thick, e 15: the interior would be 0.95)."""
    res = FT.fit_spec(l_warp_design(low, high))
    assert dense_min(res, "L") >= 1.0
    assert any(b["what"] == "inside" for b in res.bounds) == cut
    if not cut:
        assert res.chi2 < 1e-12


def test_a_fade_is_refused():
    """F6 (p8): the fit describes the unfaded surface, so a fade made its crease report and E_FIT_SHAPE contradict the
    build (tooltest_top_triangle r0026); fades were removed from the core on 2026-10-13 and a design that still
    carries one is refused E_SPEC."""
    s = FX.analytic_prism()
    s["facets"]["N"] = {"poly": s["facets"]["N"], "fade": {"d0": 30.0, "d1": 60.0}}
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    assert e.value.code == "E_SPEC" and e.value.fields["facet"] == "N" and "fade must be null" in e.value.message


def test_flat_face_misfit_names_no_single_corner():
    """P1-2 (p01): K put on the plane of A, H, E and E's reading moved -2 mm. The deviations still share one sigma
    (nothing in the data singles a corner out), and the flat fit's largest misfit follows the readings (E); the old
    warning named K here. With two extra corners the deviations differ: the three largest are listed."""
    s, _ = crown_design(0.0)
    s["nodes"]["E"][2]["read"]["X"] -= 2.0
    res = FT.fit_spec(s)
    (w,) = res.warnings
    sig = [x["sigma"] for x in w["deviations"] if not x["in_line"]]
    assert len(sig) == 3 and max(sig) - min(sig) < 1e-9 and w["fit"][0]["node"] == "E"
    assert ("each corner 3.8 sd from the plane of the other three (with one extra corner the readings cannot say which "
            "corner is off): A ") in w["message"]
    pl = lambda d, y: 64.0 + 0.02 * d - 0.05 * y                               # noqa: E731
    P = {"P1": (20.0, -6.0), "P2": (40.0, -7.0), "P3": (60.0, -5.0), "P4": (55.0, 6.0), "P5": (25.0, 7.0)}
    nodes = {n: [*p, read(pl(*p) + (1.5 if n == "P3" else 0.0), 0.2)] for n, p in P.items()}
    res = FT.fit_spec(FX.straight(nodes=nodes, facets={"T": list(P)}))
    (w,) = res.warnings
    assert w["extra"] == 2 and w["message"].startswith(
        f"face T: its corners do not fit one flat face (chi2 {w['chi2']:.1f} for 2 extra corners): largest from the "
        f"plane of the others: P3 +1.50 +- ")


def test_solved_corner_inside_the_outline_is_a_fact():
    """P1-1 (p02): run 7's flat crown with E null: the crown and shoulder planes put E +2.42 mm proud of its reading
    (the run-6/7 wing) at sd 0.28, where W_FIT_PRIOR (sd > 2) never fired: W_FIT_SOLVED states it, and with the stored
    reading W_FIT_OFF_READING compares them. H null: the thickness bound holds it, said so, and no crease leaning on it
    is "certain" (P2-5). E "base" with a stored reading: compared too. Tool tests on mixed inputs."""
    stored = {n: {"X": X, "sd": sd, "d": d, "y": y} for n, (d, y, X, sd) in RUN7_READ.items()}
    s = run7_layout("nodes")
    s["nodes"]["E"][2] = None
    res = FT.fit_spec(s, readings=stored)
    e = res.nodes["E"]
    assert e["X"] - RUN7_READ["E"][2] == pytest.approx(2.42, abs=0.01) and e["sd_X"] < 0.5 and not e["prior"]
    w = {x["code"]: x for x in res.warnings}
    assert set(w) == {"W_FIT_SOLVED", "W_FIT_OFF_READING"}
    sv = w["W_FIT_SOLVED"]
    assert sv["node"] == "E" and sv["faces"] == ["crown", "shoulder"] and sv["through"] == ["A", "D", "H", "K"]
    assert sv["message"] == (f"node E: no reading or typed value; X {e['X']:.2f} +- {e['sd_X']:.2f} mm (t {e['t']:.2f} "
                             f"mm) from faces crown, shoulder through A, D, H, K, {sv['nearest_mm']:.1f} mm from its "
                             f"nearest observed corner D")
    t_read = RUN7_READ["E"][2] - (e["X"] - e["t"])
    assert w["W_FIT_OFF_READING"]["message"] == (f"solved E {e['t']:.2f}: its reading {t_read:.2f} +- 0.44 "
                                                 f"({(e['t'] - t_read) / 0.44:+.1f} sd)")
    s = run7_layout("nodes")
    s["nodes"]["H"][2] = None
    res = FT.fit_spec(s)
    h = res.nodes["H"]
    assert h["at_bound"] and h["t"] == pytest.approx(1.25, abs=2e-6)          # t_min 1 + roll_mm 0.25 (F2)
    (sv,) = [x for x in res.warnings if x["code"] == "W_FIT_SOLVED"]
    assert sv["at_bound"] and sv["message"].startswith("node H: no reading or typed value; the thickness bound holds "
                                                       "it at t 1.25 mm (from face crown through A, E, K, ")
    lines = res.report_lines(limit=60)
    assert "at the thickness bound: H (solved)" in lines and res.dof == 1    # P3-3: the held bound counts
    cr = {c["name"]: c for c in res.creases}
    assert cr["crown|logo"]["bound"] == ["node H"] and not cr["crown|logo"]["certain"]
    assert any(ln.startswith("crown|logo ridge") and "the thickness bound holds node H (the sd is without it)" in ln
               for ln in lines)
    s = run7_layout("warp")
    s["nodes"]["E"][2] = "base"
    res = FT.fit_spec(s, readings=stored)
    (o,) = [x for x in res.warnings if x["code"] == "W_FIT_OFF_READING"]
    assert o["kind"] == "base" and o["message"].startswith(f"base E {res.nodes['E']['t']:.2f}: its reading 7.03 +- ")


def test_a_reading_used_away_from_where_it_was_read_warns():
    """P1-3 (p03): a reading's own (d, y) is checked against its node (the tip 8-20 mm back in every v2 run): beyond
    max(OFF_POSITION_MM, OFF_POSITION_SD x its sd_d / sd_y) a fact line; the same for a stored reading of a typed
    node."""
    def design(dq, sd_d=None):
        r = {"id": "Q-1", "X": 64.5, "sd": 0.2, "d": 60.0, "y": -6.0}
        if sd_d is not None:
            r["sd_d"] = sd_d
        return FX.straight(nodes={"P": [20.0, -6.0, read(64.0, 0.2)], "Q": [60.0 + dq, -6.0, {"read": r}],
                                  "R": [60.0, 6.0, read(65.0, 0.2)]}, facets={"T": ["P", "Q", "R"]})
    res = FT.fit_spec(design(10.0, 0.7))
    (w,) = res.warnings
    assert w["code"] == "W_FIT_OFF_READING" and w["kind"] == "position" and w["off_mm"] == pytest.approx(10.0)
    assert w["message"] == "node Q at d 70.00, y -6.00: its reading at d 60.00 +- 0.70, y -6.00 (10.00 mm away)"
    for dq, sd_d, warns in ((0.4, None, False), (0.6, None, True), (1.0, 0.7, False), (1.6, 0.7, True)):
        res = FT.fit_spec(design(dq, sd_d))
        assert [x["code"] for x in res.warnings] == (["W_FIT_OFF_READING"] if warns else []), (dq, sd_d)
    s = FX.straight(nodes={"P": [20.0, -6.0, 4.0], "Q": [60.0, -6.0, 4.5], "E": [40.0, 6.0, 5.27]},
                    facets={"T": ["P", "Q", "E"]})
    res = FT.fit_spec(s, readings={"E": {"X": 65.27, "sd_X": 0.4, "d": 30.0, "y": 6.0, "sd_d": 0.5}})
    (w,) = res.warnings
    assert w["message"] == "node E at d 40.00, y 6.00: its reading at d 30.00 +- 0.50, y 6.00 (10.00 mm away)"


def tilted_pair(cov=None, moved=None, sd_x: float = 0.1, rise: float = 0.0) -> dict:
    """Facets D (below the edge P1-P2) and U (above) on the slab, 3 mm of X along their 40 mm shared edge: a reading's
    position error turns the edge and with it the across-edge slopes (review P2-2's mechanism, run 7's shoulder D-E).
    `cov` {node: 3 x 3 (d, y, X)} gives the readings' error; `moved` {node: (dd, dy, dX)} moves them; `rise` lifts U1
    (U's borders with the base then rise along themselves, ~0.4 per mm at rise 10: the slope difference along a
    facet|base border that turns the fold when the border turns, review 2026-10-12 phase 2 P1/P2)."""
    true = {"P1": (20.0, 0.0, 66.0), "P2": (60.0, 0.0, 69.0), "U1": (46.0, 8.0, 68.6 + rise), "D1": (34.0, -8.0, 66.8)}
    # (U1, D1 off the middle: each facet's longer border with the base stays the reported one in every draw)
    nodes = {}
    for n, (d, y, X) in true.items():
        dd, dy, dX = (moved or {}).get(n, (0.0, 0.0, 0.0))
        r = {"id": n, "X": X + dX, "sd": sd_x, "d": d + dd, "y": y + dy}
        if cov:
            r["cov"] = np.asarray(cov[n]).tolist()
        nodes[n] = [d + dd, y + dy, {"read": r}]
    return FX.straight(nodes=nodes, facets={"U": ["P1", "P2", "U1"], "D": ["P1", "D1", "P2"]})


def test_position_error_is_carried_into_crease_sd_certainty_and_costs():
    """P2-2: the readings' (d, y) error, with a -0.7 d-X correlation (the tip E's, readings.py), joins the angles' sd:
    a Monte Carlo over the readings in all three coordinates (each node placed at its moved reading, as the lab does)
    matches it, on tilted_pair and on its steep variant (U's borders with the base rising ~0.4 per mm along
    themselves: the border turns with its ends and its fold with it); without position error nothing changes.
    Changed by the review of 2026-10-12 (phase 2, P1 / P2), the design now one joint model (fit.py docstring: the
    read corners' positions are unknowns, observed with X jointly): v3p2-fit2 had changed this test to pin its
    effective-variance numbers, run 7's shoulder|base +2.8 +- 0.7 certain and the valley refused at 61, which its own
    report measured 4.7x over-confident (the border's turn). Restored: phase 1's +- 3.1, open, the valley accepted
    (~3), now by a solution that moves D and E within their readings (the cost its chi2 pays)."""
    sd_d, sd_y, sd_x, rho = 1.0, 0.4, 0.1, -0.7
    M = np.array([[sd_d ** 2, 0.0, rho * sd_d * sd_x], [0.0, sd_y ** 2, 0.0], [rho * sd_d * sd_x, 0.0, sd_x ** 2]])
    cov = {n: M for n in ("P1", "P2", "U1", "D1")}
    L = np.linalg.cholesky(M)
    for rise in (0.0, 10.0):
        res = FT.fit_spec(tilted_pair(cov, rise=rise))
        plain0 = FT.fit_spec(tilted_pair(rise=rise))
        rng = np.random.default_rng(17)
        names = [c["name"] for c in res.creases]
        draws = []
        for _ in range(300):
            r = FT.fit_spec(tilted_pair(moved={n: tuple(L @ rng.normal(size=3)) for n in cov}, rise=rise))
            draws.append([{c["name"]: c for c in r.creases}[k]["deg"] for k in names])
        sd_mc = np.array(draws).std(axis=0, ddof=1)
        for k, c in enumerate(res.creases):
            p = {x["name"]: x for x in plain0.creases}[c["name"]]
            assert c["free_deg"] == pytest.approx(p["free_deg"], abs=1e-6), (rise, c["name"])
            assert abs(sd_mc[k] / c["sd_deg"] - 1.0) < 0.15, (rise, c["name"], sd_mc[k], c["sd_deg"])
        du = res.creases[0]
        assert du["name"] == "D|U" and du["sd_deg"] > 1.5 * du["sd_deg_X"]   # the position part dominates here
        if rise:                                         # the border turn: U|base's sd several times its X part
            ub = {c["name"]: c for c in res.creases}["U|base"]
            assert ub["sd_deg"] > 2.0 * ub["sd_deg_X"], ub
        else:
            M0 = M.copy()
            M0[0, 2] = M0[2, 0] = 0.0                                       # the correlation matters too
            du0 = FT.fit_spec(tilted_pair({n: M0 for n in cov})).creases[0]
            assert abs(sd_mc[0] / du0["sd_deg"] - 1.0) > 0.2
    s = run7_layout("nodes")
    for n, v in s["nodes"].items():
        v[2]["read"].update(sd_d=1.0 if n in ("D", "E") else 0.3, sd_y=0.3)
    res = FT.fit_spec(s)
    sb = {c["name"]: c for c in res.creases}["shoulder|base"]
    assert sb["sd_deg_X"] == pytest.approx(0.60, abs=0.01) and 2.5 < sb["sd_deg"] < 4.5 and sb["state"] == "open"
    for n, r in res.nodes.items():                     # a reading's chi2: its X row and its position rows
        assert r["chi2"] == pytest.approx(r["residual_sd"] ** 2 + r["pos_sd"] ** 2, rel=1e-9), n
    assert sum(r["chi2"] for r in res.nodes.values()) == pytest.approx(res.chi2, rel=1e-9)
    s["creases"] = [{"facets": ["shoulder", "base"], "shape": "valley"}]
    dec = FT.fit_spec(s)
    cost = dec.chi2 - dec.chi2_free
    assert 1.0 < cost <= FT.ACCEPT_DCHI2
    c = {x["name"]: x for x in dec.creases}["shoulder|base"]
    assert c["dchi2"] == pytest.approx(cost, abs=1e-9) and -c["deg"] >= FT.MIN_CREASE_DEG - 1e-9
    assert sum(r["chi2"] for r in dec.nodes.values()) == pytest.approx(dec.chi2, rel=1e-9)
    for n in ("D", "E"):                               # the corners it moved, within their readings
        assert math.hypot(*dec.nodes[n]["move_mm"]) > 0.5 and dec.nodes[n]["pos_sd"] < 2.0, n
    pb = FT._Problem(s)
    free, _ = FT._free_fit(pb)
    one = FT._declared_fit(pb, [(("shoulder", "base"), "valley")], free)
    assert cost == pytest.approx(one.chi2 - free.chi2, rel=1e-9)          # the cost its own solution pays
    with pytest.raises(TempleError) as e:                                   # the X-only readings refuse it at 69
        FT.fit_spec(run7_layout("nodes", s["creases"]))
    assert e.value.fields["dchi2"] == pytest.approx(69.0, abs=0.1)


@pytest.mark.parametrize("bad", [
    {"cov": [[0.36, 0.0, 0.0], [0.0, 0.16, 0.0], [0.0, 0.0, 0.25]]},              # X variance != sd^2 (0.09)
    {"cov": [[0.36, 0.0, 0.0], [0.0, 0.16, 0.0], [0.0, 0.0, 0.09]], "sd_d": 0.6},  # both
    {"cov": [[0.36, 0.5, 0.0], [0.0, 0.16, 0.0], [0.0, 0.0, 0.09]]},               # not symmetric
    {"cov": [[0.36, 0.0, 0.5], [0.0, 0.16, 0.0], [0.5, 0.0, 0.09]]},               # not positive semidefinite
    {"sd_d": -0.1}])
def test_malformed_position_error_is_refused(bad):
    s = tilted_pair()
    s["nodes"]["P1"][2]["read"].update(bad)
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    assert e.value.code == "E_SPEC"


def test_facet_base_line_labels_the_fold_and_the_dihedral():
    """P2-3 (p07): one crease, two numbers, both labelled: the fold across the border (+2.9 at mid, the fit's angle,
    sd and certainty, +2.7..+3.3 along it) and the dihedral the builder's table holds (2.6: the normals' angle with the
    slope along the border shared, build._base_border_design; the raw normals' 19.2 is no fold)."""
    res = FT.fit_spec(run7_layout("nodes"))
    assert ("shoulder|base ridge: fold +2.9 +- 0.6 deg across the border at mid, +2.7..+3.3 along it, certain; "
            "dihedral 2.6 deg") in res.report_lines(limit=60)
    c = {x["name"]: x for x in res.creases}["shoulder|base"]
    assert c["normal_deg"] == pytest.approx(c["deg"] / math.sqrt(1.0 + 0.46 ** 2), abs=0.15)   # s ~ 0.46 along it


def warp_beside_flat(shape: str, twist: float = 1.5, sd: float = 1.0) -> dict:
    """A warp W (a 2 x twist mm twist) beside a flat R along d = 60, the readings a 1 deg valley: shape declared."""
    g = math.tan(math.radians(1.0))
    nodes = {"A": [20.0, -6.0, read(64.0 + twist, sd)], "B": [60.0, -6.0, read(64.0, sd)],
             "C": [60.0, 6.0, read(64.0, sd)], "D": [20.0, 6.0, read(64.0 - twist, sd)],
             "E": [90.0, -6.0, read(64.0 + 30 * g, sd)], "F": [90.0, 6.0, read(64.0 + 30 * g, sd)]}
    return FX.straight(nodes=nodes, facets={"W": {"poly": ["A", "B", "C", "D"], "plane": "warp"},
                                            "R": ["B", "E", "F", "C"]},
                       creases=[{"facets": ["R", "W"], "shape": shape}])


def r_w_angle(res, y: float) -> float:
    """R|W at (60, y), independent of the fit's forms: R's plane slope and the fitted warp's one-sided MVC difference
    (n from R into W: -d)."""
    sp = res.spec
    P = np.array([sp["nodes"][n][:2] for n in ("A", "B", "C", "D")], np.float64)
    F = 60.0 + np.array([sp["nodes"][n][2] for n in ("A", "B", "C", "D")])
    X = lambda d: float(mvc(P, F, d, y))                                       # noqa: E731
    h = 1e-3
    g2 = (4.0 * X(60.0 - h) - X(60.0 - 2 * h) - 3.0 * X(60.0)) / (2.0 * h)
    g1 = -sp["facets"]["R"]["plane"]["coef"][1]
    return math.degrees(math.atan(g1) - math.atan(g2))


def test_crease_beside_a_warp_reports_its_range_and_holds_its_ends():
    """P2-4 (p08): beside a warp the angle varies along the edge (run 7's warp crown|logo rises into K): the range is
    reported, and a declaration holds what the builder judges over the whole edge, at EDGE_AT 1/16 .. 15/16, not only
    1/4, 1/2, 3/4: the fold >= MIN_CREASE_DEG at the midpoint and the EDGE_W mean of the signed dihedral >=
    FLAT_CREASE_DEG + MEAN_MARGIN_DEG (here no slope along the edge, so the dihedral is the fold; with the twist the
    fold changes linearly along it, its mean is the midpoint's, which holds; an end at -1.9 still votes valley)."""
    res = FT.fit_spec(run7_layout("warp"))
    c = {x["name"]: x for x in res.creases}["crown|logo"]
    lo, hi = c["range_deg"]
    assert lo < c["deg"] < hi and hi - lo > 3.0 and c["segments"] == len(FT.EDGE_AT) and c["borders"] == 1
    assert any(ln.startswith(f"crown|logo ridge {c['deg']:+.1f} +- {c['sd_deg']:.1f} deg at mid, {lo:+.1f}..{hi:+.1f} "
                             f"along the edge, certain") for ln in res.report_lines(limit=60))
    res = FT.fit_spec(warp_beside_flat("valley", twist=3.0))
    c = {x["name"]: x for x in res.creases}["R|W"]
    ang = [r_w_angle(res, -6.0 + 12.0 * f) for f in FT.EDGE_AT]
    lo, hi = c["range_deg"]
    assert any(ln.startswith(f"R|W valley {c['deg']:+.1f} deg at mid, {lo:+.1f}..{hi:+.1f} along the edge (held at the "
                             f"declared minimum)") for ln in res.report_lines(limit=40))
    assert r_w_angle(res, 0.0) == pytest.approx(-FT.MIN_CREASE_DEG, abs=0.01) and c["held"]
    assert sum(w * a for w, a in zip(FT.EDGE_W, ang)) <= -(FT.FLAT_CREASE_DEG + FT.MEAN_MARGIN_DEG) + 1e-5
    assert c["range_deg"] == pytest.approx([min(ang), max(ang)], abs=1e-4)
    assert max(ang) < -FT.FLAT_CREASE_DEG + 0.1 < 0.0 and min(ang) < max(ang) - 1.0     # the twist: the ends differ


def test_a_declared_pair_with_no_shared_edge_is_refused():
    """P2-6 (p11): crown|lower meet only at K. The fit accepted it and the builder refused with "fix the node values, do
    not redeclare"; the fit now refuses it first, a fact, no cost."""
    with pytest.raises(TempleError) as e:
        FT.fit_spec(run7_layout("nodes", [{"facets": ["crown", "lower"], "shape": "ridge"}]))
    x = e.value
    assert x.code == "E_FIT_SHAPE" and x.fields["dchi2"] is None
    assert x.message == ("the declared crease shapes: crown|lower declared ridge: crown and lower share no edge inside "
                         "the outline")
    assert x.fields["creases"] == [{"pair": ["crown", "lower"], "declared": "ridge", "free_deg": None,
                                    "free_sd_deg": None, "dchi2": None, "moves": [], "reason": "no shared edge"}]


RUN7_ONE_SOURCE = dict(RUN7_READ, D=(75.45, -1.00, 66.20, 0.50), L=(0.0, -9.82, 70.54, 0.40))
# P2-7: every corner from crown-geometry §2: A H K E its bundle, C D L its B1+B2 readings (L moved to the hinge plane)


def test_real_run7_one_source():
    """P2-7 (p06, p10): a tool test with every corner from one source. The crown is still not flat (A, H, K, E are
    the same), the warp crown keeps every reading, and the logo|nose ridge's refusal turns on the shared hinge node A
    alone: at the crown vertex's X 68.74 it is refused, at the logo corner A2's 69.49 (B1+B2) it builds."""
    flat = FT.fit_spec(run7_layout("nodes", rd=RUN7_ONE_SOURCE))
    assert flat.faces["crown"]["not_flat"] and [w["code"] for w in flat.warnings] == ["W_FACE_NOT_FLAT"]
    warp = FT.fit_spec(run7_layout("warp", rd=RUN7_ONE_SOURCE))
    assert warp.chi2 < 1e-9 and not warp.warnings
    ridge = [{"facets": ["logo", "nose"], "shape": "ridge"}]
    with pytest.raises(TempleError) as e:
        FT.fit_spec(run7_layout("warp", ridge, rd=RUN7_ONE_SOURCE))
    assert e.value.code == "E_FIT_SHAPE" and [c["pair"] for c in e.value.fields["creases"]] == [["logo", "nose"]]
    res = FT.fit_spec(run7_layout("warp", ridge, rd=dict(RUN7_ONE_SOURCE, A=(0.0, 10.13, 69.49, 0.31))))
    c = {x["name"]: x for x in res.creases}["logo|nose"]
    assert c["deg"] >= FT.MIN_CREASE_DEG - 1e-9 and c["dchi2"] <= FT.ACCEPT_DCHI2


def test_held_declaration_bounds_and_dof_are_reported_plainly():
    """P3-1: a declaration held at the minimum prints so, never "+- 0.0"; P3-3: E_FIT "bounds" names the thinnest
    points of the fit without the bound; dof counts the held bounds."""
    res = FT.fit_spec(crease_pair(0.3))
    c = {x["name"]: x for x in res.creases}["D|U"]
    (ln,) = [x for x in res.report_lines(limit=40) if x.startswith("D|U")]
    assert c["held"] and ln.startswith("D|U ridge +2.5 deg (held at the declared minimum), open, declared ridge") \
        and "+- 0.0" not in ln
    s = FX.straight(base={"top": [[0.0, 0.5], [120.0, 0.5]], "bottom": [[0.0, 0.5], [120.0, 0.5]]},
                    nodes={"P": [20.0, -6.0, "base"], "Q": [60.0, -6.0, read(64.0, 0.2)],
                           "R": [40.0, 6.0, read(64.0, 0.2)]}, facets={"F": ["P", "Q", "R"]})
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    x = e.value
    assert x.fields["reason"] == "bounds" and x.fields["points"][0] == {"at": ["node", "P"], "t_mm": pytest.approx(0.5)}
    assert x.message.endswith("; without the bound the thinnest would be node P t 0.50 mm")


def test_declared_angle_is_checked_exactly_after_the_passes(monkeypatch):
    """P3-5: after the linearised passes every declared piece is checked exactly; a pass that builds less than
    MIN_CREASE_DEG (here rows linearised at 2.0 deg) is E_FIT "angles", never a silent shortfall."""
    rows = FT._shape_rows

    def weak(pb, pairs, z):
        monkeypatch.setattr(FT, "MIN_CREASE_DEG", 2.0)
        try:
            return rows(pb, pairs, z)
        finally:
            monkeypatch.setattr(FT, "MIN_CREASE_DEG", 2.5)
    monkeypatch.setattr(FT, "_shape_rows", weak)
    with pytest.raises(TempleError) as e:
        FT.fit_spec(crease_pair(0.3))
    # v3p2: the refusal states its facts (the passes, that they settled, each short piece's fold and mean dihedral
    # against the minimums); it carried the reason and the pair only
    f = e.value.fields
    assert e.value.code == "E_FIT" and f["reason"] == "angles" and f["creases"] == ["D|U"] and f["settled"]
    assert 1 <= f["passes"] < FT.ANGLE_PASSES
    (s,) = f["short"]                                     # the mean row (2.25) binds: the fold is 2.25, short of 2.5
    assert s["pair"] == "D|U" and s["fold_deg"] == pytest.approx(2.25, abs=1e-6)
    assert s["mean_dihedral_deg"] == pytest.approx(2.25, abs=1e-6)
    assert e.value.message == (f"the declared fit: the declared minimum angle settled after {f['passes']} passes "
                               "short of it at D|U (internal: the linearised rows and the exact check disagree): D|U "
                               "declared ridge: fold +2.250 deg at mid (0.25 short), mean dihedral +2.250 deg (a "
                               "declared shape needs a fold of at least 2.5 deg and a mean dihedral of at least 2.25 "
                               "deg, both its way; + is a ridge)")


@pytest.mark.parametrize("design", ["run7_nodes", "run7_warp", "tee_warp", "prada", "l_warp"])
def test_moved_problem_equals_a_full_evaluation(design):
    """The position Jacobian's moved() recomputes only the rows a node's position touches; every matrix equals
    numbers() at the moved positions (observations, equalities, bounds and cutting planes, the slope forms)."""
    s = {"run7_nodes": lambda: run7_layout("nodes"), "run7_warp": lambda: run7_layout("warp"),
         "tee_warp": lambda: tee(True), "prada": FX.prada_default,
         "l_warp": lambda: l_warp_design(61.1, 75.0)}[design]()
    pb = FT._Problem(s)
    if design == "l_warp":
        FT._solve(pb)                                                       # adds its cutting planes
        assert any(d[0] == "in" for d in pb.bound_def)
    for n in pb.names:
        d, y = pb.nodes[n]
        for p in ((d + 0.37, y), (d, y - 0.29)):
            m = pb.moved(n, p)
            full = pb.numbers(dict(pb.nodes, **{n: p}))
            for k in ("A", "b", "C", "e", "G", "h"):
                assert np.allclose(m[k], full[k], rtol=0.0, atol=1e-12), (n, k)
            for a, b in zip(m["forms"], pb.slope_forms(full["segs"])):
                assert np.allclose(a, b, rtol=0.0, atol=1e-12), n


def steep_pair(slope: float = 1.0) -> dict:
    """crease_pair with its shared edge P1-P2 rising `slope` mm per mm (X 66 -> 106 over 40 mm), the readings a 1 deg
    valley across it, a ridge declared: along a steep edge the builder's dihedral is ~ fold / sqrt(1 + slope^2)."""
    s = crease_pair(0.3)
    for n, v in s["nodes"].items():
        v[2]["read"]["X"] += slope * (v[0] - 20.0)
    return s


def test_steep_edge_declaration_holds_the_builders_dihedral():
    """Review P2-4 / the builder's flat rule: a ridge held at a 2.5 deg fold along an edge rising 1 mm per mm is a
    1.77 deg dihedral, which the builder calls flat (E_CREASE_SHAPE after a fit that accepted it). The piece's mean
    dihedral row binds instead (>= FLAT_CREASE_DEG + MEAN_MARGIN_DEG, the fold ~3.2), and the line says the
    declaration holds it."""
    res = FT.fit_spec(steep_pair())
    c = {x["name"]: x for x in res.creases}["D|U"]
    assert c["held"] and c["deg"] > FT.MIN_CREASE_DEG + 0.5
    assert c["normal_deg"] == pytest.approx(FT.FLAT_CREASE_DEG + FT.MEAN_MARGIN_DEG, abs=0.01)
    (ln,) = [x for x in res.report_lines(limit=40) if x.startswith("D|U")]
    assert ln.startswith(f"D|U ridge {c['deg']:+.1f} deg (held by its declaration), open (dihedral "
                         f"{c['normal_deg']:.1f} deg), declared ridge (readings-only -1.0 +- ")


# ------------------------------------------------------------------------------------------- v3p2-fit2 (phase 2)
# read_payload (WP6_V3 §3 / I2), camera turns as shared unknowns, the angle passes' settling, one model for the
# solution, its cost and its uncertainty. Reports: data/wp6v2/review_20261012/v3/v3p2-fit2.md.
THETA_RAD = math.radians(0.3)        # readings.THETA_SD_DEG: a camera fit's theta sd (one unit of a camera turn)
HINGE_D, HINGE_X = -0.5, 64.6        # the hinge axis in the chart (readings.py: chart d = hinge z - axis z)


def turn(p) -> np.ndarray:
    """The move (dd, dy, dX) of chart point p when the temple turns +1 sd about the hinge axis (a vertical axis through
    d -0.5, X 64.6: X changes -theta (d + 0.5), d changes theta (X - 64.6); readings' camera_shift of a fit is a share
    of it: probe1's K reads 05 -0.024 + 02 -0.224 = -0.248 mm of X at d 46.9, this gives -0.248)."""
    d, y, X = p
    return THETA_RAD * np.array([X - HINGE_X, 0.0, -(d - HINGE_D)])


CAM_POS = {"A": (10.0, -6.0), "B": (40.0, -8.0), "C": (75.0, -5.0), "D": (70.0, 7.0), "E": (20.0, 7.0)}
CAM_W02 = {"A": 0.95, "B": 0.05, "C": 0.9, "D": 0.1, "E": 0.85}
# fit 02's share of each reading's turn (05 the rest): readings from different photo sets (probe1's B1+B2+05+02
# readings all take ~0.9 from 02; a 05+03 reading takes none from 02)
CAM_COV = np.diag([0.16 ** 2, 0.12 ** 2, 0.18 ** 2])        # probe1's crisp corners: sd_d 0.16-0.22, X 0.18-0.25


def cam_plane(d, y) -> float:
    return 64.5 + 0.02 * (d - 40.0) - 0.06 * y


def cam_reading(name: str, p, u: dict, mode: str = "camera", noise=None, w02=None) -> dict:
    """A reading of the true chart point p whose camera fits 05 and 02 are off by u {fit: sd} (the read point moves
    by sum_f u_f s_f, s_f = the fit's share of turn(p)), plus `noise` (dd, dy, dX). mode "camera": the fit sees the
    camera terms (read_payload's form); "ignored": no camera terms, the pick cov only; "independent": the camera part
    added to each reading's own cov (probe1's obs_of)."""
    w = CAM_W02[name] if w02 is None else w02
    shift = {"05": (1.0 - w) * turn(p), "02": w * turn(p)}
    q = np.asarray(p, float) + sum(u.get(f, 0.0) * s for f, s in shift.items()) + (0.0 if noise is None else noise)
    C = CAM_COV.copy()
    if mode == "independent":
        C = C + sum(np.outer(s, s) for s in shift.values())
    rec = {"id": name, "X": float(q[2]), "sd": math.sqrt(C[2, 2]), "d": float(q[0]), "y": float(q[1]),
           "cov": C.tolist()}
    if mode == "camera":
        rec["camera"] = {f: s.tolist() for f, s in shift.items()}
    return {"read": rec}


def cam_face(u: dict, mode: str = "camera", lift: dict | None = None, rng=None, w02: float | None = None) -> dict:
    """One flat face T through CAM_POS on cam_plane (plus `lift` {corner: mm}), read under camera turns u; w02 one
    share of fit 02 for every corner (one photo set for all), else CAM_W02."""
    nodes = {}
    for n, (d, y) in CAM_POS.items():
        p = (d, y, cam_plane(d, y) + (lift or {}).get(n, 0.0))
        noise = None if rng is None else np.linalg.cholesky(CAM_COV) @ rng.normal(size=3)
        r = cam_reading(n, p, u, mode, noise, w02=w02)
        nodes[n] = [r["read"]["d"], r["read"]["y"], r]
    return FX.straight(nodes=nodes, facets={"T": list(CAM_POS)})


CAM_PAIR = {"P1": (20.0, 0.0), "P2": (75.0, 0.0), "U1": (30.0, 8.0), "U2": (65.0, 8.0), "D1": (30.0, -8.0),
            "D2": (65.0, -8.0)}
CAM_PAIR_W02 = {"P1": 0.95, "P2": 0.05, "U1": 0.9, "U2": 0.1, "D1": 0.1, "D2": 0.9}


def cam_pair(u: dict, mode: str = "camera", ridge_deg: float = 4.0, rng=None) -> dict:
    """Quads U (above the edge P1-P2) and D (below) on the slab, one extra corner each, a true ridge of ridge_deg
    across the edge (each face falling away at tan(ridge / 2)), the edge rising 0.03 per mm; read under camera turns
    u, each corner from its own photo set (CAM_PAIR_W02)."""
    g = math.tan(math.radians(ridge_deg / 2.0))
    nodes = {}
    for n, (d, y) in CAM_PAIR.items():
        p = (d, y, 65.0 + 0.03 * (d - 20.0) - g * abs(y))
        noise = None if rng is None else np.linalg.cholesky(CAM_COV) @ rng.normal(size=3)
        r = cam_reading(n, p, u, mode, noise, w02=CAM_PAIR_W02[n])
        nodes[n] = [r["read"]["d"], r["read"]["y"], r]
    return FX.straight(nodes=nodes, facets={"U": ["P1", "P2", "U2", "U1"], "D": ["P1", "D1", "D2", "P2"]})


def test_a_shared_camera_turn_never_reads_as_a_warp_or_moves_a_crease():
    """WP6_V3 §3 / readings.py: a reading's camera part is one rigid turn of the temple per camera fit. Fitted as one
    unknown per fit with its unit prior: a turn of 3 sd shared by both fits (0.9 deg, rigid) leaves a flat face flat
    and a 3 deg ridge at 3.00; a 2.5 sd turn of fit 02 alone, which moves the 02-dominated readings and not the others
    (A, C, E vs B, D: photo sets with and without 02's photos), costs the flat face at most its 6.25 and stays under
    the flatness line (8 for 2 extra corners), and the ridge stays a ridge within 0.5 sd. With the camera terms left
    out (the pick cov only) the same readings read as a warped face (chi2 10.3) and turn the ridge flat (+0.7 deg).
    Real warps: a twist (A, C up 0.75 mm, B, D down) is caught with the turns fitted, under any of these turns; so is a
    1.5 mm lift at C when every corner is read from one photo set (as probe1's crown corners were: the turn is then a
    plane, chi2 26.8 with or without it). From these alternating photo sets a lift at C, the far 02-dominated corner,
    is what a turn of fit 02 does there, and the fit explains it by one (chi2 6.25, u_02 -1.55): the data cannot tell
    them apart (fit.py docstring; the v3p2-fit2 report)."""
    common = {"05": 3.0, "02": 3.0}
    res = FT.fit_spec(cam_face(common))
    assert res.faces["T"]["flat_chi2"] < 1e-9 and not res.warnings
    c = {x["name"]: x for x in FT.fit_spec(cam_pair(common, ridge_deg=3.0)).creases}["D|U"]
    assert c["deg"] == pytest.approx(3.0, abs=0.05)
    for u02 in (2.5, -2.5):
        u = {"02": u02}
        res = FT.fit_spec(cam_face(u))
        f = res.faces["T"]
        assert f["flat_chi2"] <= u02 ** 2 + 1e-9 and not f["not_flat"] and not res.warnings, u02
        # one face: the joint fit is its own test, the face's test linearised in the corners' positions where the
        # fit moves them (Gauss-Newton; v3p2-fit2-fix: the corners are unknowns, so 1e-3 not 1e-6)
        assert res.chi2 == pytest.approx(f["flat_chi2"], rel=1e-3)
        assert sum(r["chi2"] for r in res.nodes.values()) + sum(
            v["u"] ** 2 for v in res.camera.values()) == pytest.approx(res.chi2, rel=1e-9)
        ign = FT.fit_spec(cam_face(u, "ignored"))
        assert ign.faces["T"]["not_flat"] and [w["code"] for w in ign.warnings] == ["W_FACE_NOT_FLAT"], u02
        pr = FT.fit_spec(cam_pair(u, ridge_deg=3.0))
        c = {x["name"]: x for x in pr.creases}["D|U"]
        assert c["shape"] == "ridge" and abs(c["deg"] - 3.0) <= 0.5 * c["sd_deg"] and not c["certain"], u02
        assert not pr.warnings
        twist = {"A": 0.75, "C": 0.75, "B": -0.75, "D": -0.75}
        for lift_u in ({}, u):
            w = FT.fit_spec(cam_face(lift_u, lift=twist))
            assert [x["code"] for x in w.warnings] == ["W_FACE_NOT_FLAT"], (u02, lift_u)
            one = FT.fit_spec(cam_face(lift_u, lift={"C": 1.5}, w02=0.9))
            plain = FT.fit_spec(cam_face(lift_u, "ignored", lift={"C": 1.5}, w02=0.9))
            assert one.faces["T"]["flat_chi2"] == pytest.approx(plain.faces["T"]["flat_chi2"], abs=0.05)
            assert one.faces["T"]["flat_chi2"] > 25.0
            assert [x["code"] for x in one.warnings] == ["W_FACE_NOT_FLAT"]
    alt = FT.fit_spec(cam_face({}, lift={"C": 1.5}))
    assert alt.faces["T"]["flat_chi2"] == pytest.approx(6.25, abs=0.05) and not alt.warnings
    assert alt.camera["02"]["u"] == pytest.approx(-1.55, abs=0.05)
    assert FT.fit_spec(cam_face({}, "ignored", lift={"C": 1.5})).faces["T"]["flat_chi2"] > 25.0
    ign = {x["name"]: x for x in FT.fit_spec(cam_pair({"02": -2.5}, "ignored", ridge_deg=3.0)).creases}["D|U"]
    assert ign["shape"] == "flat" and ign["deg"] < 1.0                      # the turn read as a 2.3 deg change
    # the report (v3p3-clean-fit): a turn the data moved, in words and unsigned; one the data left at its prior's 0
    # (here ~1e-3 sd: one photo set, the turn is a plane) prints nothing, and the misfits are then not "after" one
    lines = FT.fit_spec(cam_face({"02": 2.5})).report_lines(limit=40)
    assert lines[1] == ("camera turn of fit 02: 1.0 sd, of fit 05: 1.0 sd (in sd of that camera fit's theta error; "
                        "one turn for all of its readings)")
    one_set = FT.fit_spec(cam_face({}, lift={"C": 1.5}, w02=0.9))
    assert all(abs(v["u"]) < 0.01 for v in one_set.camera.values())
    lines = one_set.report_lines(limit=40)
    assert not any(ln.startswith("camera") for ln in lines)
    assert lines[1].startswith("misfit beyond 1 sd (fitted - read): C (d, y) 0.03 mm (0.2 sd) and X -0.58 mm (3.2 sd)")


def test_camera_turns_and_effective_sds_are_calibrated_monte_carlo():
    """Monte Carlo over the camera turns (u ~ N(0, 1) per fit) and the readings' own (d, y, X) error, each node placed
    at its reading (as the lab does): the crease angles' spread matches the fit's sd within 15 % and the mean chi2 its
    dof; the flat face's own test exceeds its line (8 for 2 extra corners) about as often as chi2(2) does (1.8 %). The
    two other treatments miss: the camera terms left out is over-confident (sd low by > 15 %), the camera part added
    to each reading's own sd (probe1's payload) under-confident (high by > 15 %)."""
    rng = np.random.default_rng(5)
    ratios = {}
    for mode in ("camera", "ignored", "independent"):
        first = FT.fit_spec(cam_pair({}, mode))
        names = [c["name"] for c in first.creases]
        ang, chi = [], []
        for _ in range(200):
            r = FT.fit_spec(cam_pair({"05": rng.normal(), "02": rng.normal()}, mode, rng=rng))
            ang.append([{c["name"]: c for c in r.creases}[k]["deg"] for k in names])
            chi.append(r.chi2)
        sd_mc = np.array(ang).std(axis=0, ddof=1)
        ratios[mode] = {k: float(sd_mc[i] / first.creases[i]["sd_deg"]) for i, k in enumerate(names)}
        if mode == "camera":
            assert first.dof == 2 and abs(float(np.mean(chi)) - 2.0) < 0.5, np.mean(chi)
    assert all(abs(v - 1.0) < 0.15 for v in ratios["camera"].values()), ratios
    assert ratios["ignored"]["D|U"] > 1.15 and ratios["independent"]["D|U"] < 0.85, ratios
    flat = np.array([FT.fit_spec(cam_face({"05": rng.normal(), "02": rng.normal()}, rng=rng)).faces["T"]["flat_chi2"]
                     for _ in range(300)])
    assert abs(float(flat.mean()) - 2.0) < 0.4 and float(np.mean(flat > 8.0)) < 0.05


# probe1 (v3/probe1.md, TOOL TEST on a copy of package_007): readings.py records of run 7's corners (crown-geometry
# picks, the tip E a crossing of the crown-forensics traces): name -> (d, y, X, cov_pick, camera_shift)
PROBE1_READ = {
    "A": (0.397, 10.126, 68.72, [[0.024683, -0.004274, -0.01202], [-0.004274, 0.013618, 0.006317],
                                 [-0.01202, 0.006317, 0.031879]],
          {"05": [0.02, -0.004, -0.013], "02": [0.001, 0.004, 0.008]}),
    "H": (36.241, 15.163, 67.911, [[0.026503, -0.004333, -0.013337], [-0.004333, 0.014274, 0.006462],
                                   [-0.013337, 0.006462, 0.034789]],
          {"05": [0.025, 0.015, -0.018], "02": [-0.008, -0.015, -0.174]}),
    "K": (46.917, 9.649, 70.044, [[0.026971, -0.004655, -0.013652], [-0.004655, 0.014719, 0.006918],
                                  [-0.013652, 0.006918, 0.035418]],
          {"05": [0.034, 0.02, -0.024], "02": [-0.006, -0.02, -0.224]}),
    "E": (76.747, 9.158, 67.612, [[1.519182, -0.297074, -0.77679], [-0.297074, 0.093735, 0.236357],
                                  [-0.77679, 0.236357, 0.726148]],
          {"05": [-0.056, 0.064, 0.041], "02": [0.072, -0.064, -0.445]}),
    "C": (22.096, -15.851, 70.691, [[0.04177, -0.009066, -0.020715], [-0.009066, 0.024453, 0.013027],
                                    [-0.020715, 0.013027, 0.053323]],
          {"05": [0.037, 0.005, -0.025], "02": [-0.005, -0.005, -0.094]}),
    "L": (1.407, -9.844, 70.545, [[0.039887, -0.008347, -0.019484], [-0.008347, 0.0231, 0.011942],
                                  [-0.019484, 0.011942, 0.050591]],
          {"05": [0.029, -0.006, -0.018], "02": [0.002, 0.006, 0.008]}),
    "D": (75.45, -1.019, 66.202, [[0.046697, -0.009013, -0.02513], [-0.009013, 0.025561, 0.013113],
                                  [-0.02513, 0.013113, 0.061649]],
          {"05": [-0.002, 0.047, -0.009], "02": [0.011, -0.047, -0.389]}),
}
PROBE1_PHOTOS = {n: ["B1", "B2", "05", "02"] if n in "AHKE" else ["B1", "B2"] for n in PROBE1_READ}


def probe1_record(n: str) -> dict:
    """The readings.py record fields read_payload uses (the probe's records carry more)."""
    d, y, X, C, cam = PROBE1_READ[n]
    return {"name": n, "kind": "crossing" if n == "E" else "corner", "d": d, "y": y, "X": X, "cov_pick": C,
            "camera_shift": cam, "photos": PROBE1_PHOTOS[n], "fits": ["05", "02"] * (len(PROBE1_PHOTOS[n]) // 2),
            "sd_X": math.sqrt(C[2][2] + sum(v[2] ** 2 for v in cam.values())), "sd_X_pick": math.sqrt(C[2][2])}


def probe1_design(crown: str, creases=(), payload: str = "fit") -> dict:
    """probe1's design: run 7's layout with the nodes at the readings (A and L at the hinge plane d 0, as the probe
    put them), each node's reading by read_payload ("fit") or as probe1 passed it ("obs_of": the camera shifts added to
    each reading's own cov, no camera terms)."""
    tab = {n: (0.0 if n in "AL" else v[0], v[1], v[2], 0.3) for n, v in PROBE1_READ.items()}
    s = run7_layout(crown, creases, rd=tab)
    for n in s["nodes"]:
        rec = probe1_record(n)
        if payload == "fit":
            s["nodes"][n][2] = FT.read_payload(rec)
        else:
            C = np.asarray(rec["cov_pick"], float) + sum(np.outer(v, v) for v in rec["camera_shift"].values())
            s["nodes"][n][2] = {"read": {"id": n, "X": rec["X"], "sd": math.sqrt(C[2, 2]), "d": rec["d"],
                                         "y": rec["y"], "cov": C.tolist()}}
    return s


def test_read_payload_carries_the_pick_covariance_and_the_camera_turns():
    """I2: read_payload(record) is the fit's node t value: X, d, y, photos, the pick covariance cov_pick (its X variance
    the sd: the pick part, never sd_X, which holds the camera part in quadrature) and camera_shift as the camera terms.
    probe1's tip E keeps its -0.74 d-X correlation. Without cov_pick it takes the pick sds, without camera_shift sd_X;
    a bad record is a ValueError; a cov_pick whose 1e-6 rounding left a negative eigenvalue is clipped and fits."""
    rec = probe1_record("E")
    p = FT.read_payload(rec)
    assert set(p) == {"read"} and json.loads(json.dumps(p, allow_nan=False)) == p
    r = p["read"]
    assert set(r) == {"id", "X", "d", "y", "photos", "sd", "cov", "camera"} and r["id"] == "E"
    assert (r["X"], r["d"], r["y"], r["photos"]) == (67.612, 76.747, 9.158, ["B1", "B2", "05", "02"])
    assert r["cov"] == PROBE1_READ["E"][3] and r["sd"] == pytest.approx(math.sqrt(0.726148), rel=1e-12)
    assert r["sd"] < rec["sd_X"] and r["camera"] == PROBE1_READ["E"][4]
    C = np.array(r["cov"])
    assert C[0, 2] / math.sqrt(C[0, 0] * C[2, 2]) == pytest.approx(-0.74, abs=0.01)
    no_cov = dict(rec, cov_pick=None, sd_X_pick=0.85, sd_d_pick=1.23, sd_y_pick=0.31, sd_d=1.24, sd_y=0.32)
    assert FT.read_payload(no_cov)["read"] == {"id": "E", "X": 67.612, "d": 76.747, "y": 9.158,
                                               "photos": ["B1", "B2", "05", "02"], "sd": 0.85, "sd_d": 1.23,
                                               "sd_y": 0.31, "camera": PROBE1_READ["E"][4]}
    bare = {k: v for k, v in no_cov.items() if k != "camera_shift"}
    assert FT.read_payload(bare)["read"] == {"id": "E", "X": 67.612, "d": 76.747, "y": 9.158,
                                             "photos": ["B1", "B2", "05", "02"], "sd": rec["sd_X"], "sd_d": 1.24,
                                             "sd_y": 0.32}
    for bad in ({"X": None}, {"d": float("nan")}, {"cov_pick": [[1.0, 0.0], [0.0, 1.0]]},
                {"cov_pick": [[0.1, 0, 0], [0, 0.1, 0], [0, 0, 0.0]]}, {"camera_shift": {"02": [0.1, 0.2]}},
                {"cov_pick": None, "sd_X_pick": 0.0}):
        with pytest.raises(ValueError):
            FT.read_payload(dict(rec, **bad))
    near = dict(rec, cov_pick=[[0.04, 0.02, 0.0], [0.02, 0.01, 0.0], [0.0, 0.0, 0.0625]])   # singular d-y block
    near["cov_pick"][1][1] = 0.0099999                                          # rounded below it: eigenvalue < 0
    r = FT.read_payload(near)["read"]
    assert float(np.linalg.eigvalsh(np.array(r["cov"])).min()) >= -1e-15
    s = probe1_design("warp")
    s["nodes"]["E"][2] = {"read": r}
    FT.fit_spec(s)                                                              # accepted (E_SPEC otherwise)
    res = FT.fit_spec(probe1_design("warp"))
    assert set(res.camera) == {"02", "05"} and res.camera["02"]["readings"] == sorted(PROBE1_READ)
    assert res.nodes["E"]["read_sd"] == pytest.approx(math.sqrt(0.726148)) and res.nodes["E"]["reading"]["camera"]


def test_read_payload_end_to_end_on_the_readings_rig():
    """readings.read_point on the readings track's synthetic rig (perspective cameras, the lab's projection; its fits
    05, 02, 03), corners of a flat face from different photo sets, then read_payload and the fit: exact picks give the
    plane to the records' rounding; read with fit 02's theta 3 sd off (picks from the true cameras), the face stays
    flat (the turn is a rigid move of 02's readings: the plane and the turn take it)."""
    from glasses3d.agent.temple_lab import readings as RD
    from glasses3d.g3d.temple import build_temple
    from glasses3d.tests.agent.test_temple_readings import SynthBasis, _pose, corner_picks
    tb = build_temple(FX.straight())
    combos = {"A": ["05", "02"], "B": ["05", "03"], "C": ["02", "03"], "D": ["05", "02", "03"], "E": ["02", "03"]}
    true = SynthBasis()
    wrong = SynthBasis()
    wrong.POSES["reference-02"] = _pose(6.0 + 3.0 * RD.THETA_SD_DEG, 2.0 + 3.0 * RD.THETA_SD_DEG)
    for basis, k in ((true, 0.0), (wrong, 3.0)):
        nodes = {}
        for n, (d, y) in CAM_POS.items():
            rec = RD.read_point(tb, basis, n, corner_picks(true, np.array([d, y, cam_plane(d, y)]), combos[n]),
                                kind="corner")
            p = FT.read_payload(rec)
            assert set(p["read"]["camera"]) == set(rec["fits"]) and p["read"]["cov"] == rec["cov_pick"]
            nodes[n] = [rec["d"], rec["y"], p]
        res = FT.fit_spec(FX.straight(nodes=nodes, facets={"T": list(CAM_POS)}))
        assert not res.warnings and set(res.camera) == {"02", "03", "05"}, k
        if k == 0.0:
            a, b, c = res.spec["facets"]["T"]["plane"]["coef"]
            assert (a + 40.0 * b, b, c) == pytest.approx((64.5, 0.02, -0.06), abs=2e-3) and res.chi2 < 1e-3
        else:
            assert max(abs(nodes[n][2]["read"]["X"] - cam_plane(*CAM_POS[n])) for n in CAM_POS) > 0.3


def test_angle_passes_settle_beyond_five_and_refuse_only_without_settling(monkeypatch):
    """Settle limit (probe1/probe_settle2.py): probe1's flat crown with shoulder|base declared a valley, its readings
    as probe1 passed them (the camera part in each reading's cov) at their X alone, the corners held where they were
    read (v3p2-fit2-fix: with the readings' position error the corners are unknowns and this declaration costs 9.2,
    the joint passes doing the work; here the passes in X are what is tested), holds its minimum only after pass 5
    and settles at 9; the cap of 5 refused it E_FIT "did not settle", a solver artefact. Now it settles, and the
    readings refuse it E_FIT_SHAPE on its cost. A cap that ends before a piece holds is E_FIT "angles" with the facts
    (passes, the last change, the piece's fold and mean dihedral); a cap that ends with every piece holding returns
    the fit: with the corners free, 3 corner passes and 3 held ones end feasible, refused on that upper bound."""
    decl = [{"facets": ["shoulder", "base"], "shape": "valley"}]
    s = probe1_design("nodes", decl, payload="obs_of")
    for v in s["nodes"].values():
        v[2]["read"].pop("cov")
    pb = FT._Problem(s)
    assert pb.M == 0
    free, _ = FT._free_fit(pb)
    one = FT._declared_fit(pb, [(("shoulder", "base"), "valley")], free)
    assert one.passes > 5
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    assert e.value.code == "E_FIT_SHAPE" and e.value.fields["dchi2"] == pytest.approx(one.chi2 - free.chi2)
    monkeypatch.setattr(FT, "ANGLE_PASSES", 3)
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    f = e.value.fields
    assert e.value.code == "E_FIT" and f["reason"] == "angles" and not f["settled"] and f["passes"] == 3
    (sh,) = f["short"]                                   # a valley: its fold and mean dihedral print negative
    assert sh["pair"] == "shoulder|base" and f["step"] > FT.SETTLE_HOLD_TOL
    assert -sh["fold_deg"] < FT.MIN_CREASE_DEG or -sh["mean_dihedral_deg"] < FT.FLAT_CREASE_DEG + FT.MEAN_MARGIN_DEG
    assert e.value.message.startswith("the declared fit: the declared minimum angle did not settle in 3 passes at "
                                      "shoulder|base (the last pass still changed a slope by ")
    gap = FT.FLAT_CREASE_DEG + FT.MEAN_MARGIN_DEG + sh["mean_dihedral_deg"]     # the mean is the short one here
    assert -sh["fold_deg"] >= FT.MIN_CREASE_DEG and gap > 0.0
    assert (f"shoulder|base declared valley: fold {sh['fold_deg']:+.3f} deg at mid, mean dihedral "
            f"{sh['mean_dihedral_deg']:+.3f} deg ({gap:.2g} short) (a declared shape needs") in e.value.message
    monkeypatch.setattr(FT, "ANGLE_PASSES", 60)
    s = probe1_design("nodes", decl, payload="obs_of")             # the corners free: they settle, held, feasible
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    full = e.value.fields["dchi2"]
    monkeypatch.setattr(FT, "POS_PASSES", 3)
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    assert e.value.code == "E_FIT_SHAPE" and e.value.fields["dchi2"] >= full - 1e-9
    monkeypatch.setattr(FT, "POS_PASSES", 30)
    monkeypatch.setattr(FT, "ANGLE_PASSES", 60)
    rows, calls = FT._shape_rows, []

    def jitter(pb, pairs, z):                            # rows exact, the settle key never still: the cap is reached
        out = rows(pb, pairs, z)
        calls.append(1)
        return [(t, r, lo, k + 1e-6 * (-1) ** len(calls)) for t, r, lo, k in out]
    monkeypatch.setattr(FT, "_shape_rows", jitter)
    res = FT.fit_spec(crease_pair(0.3))
    c = {x["name"]: x for x in res.creases}["D|U"]
    assert res.model["angle_passes"] == FT.ANGLE_PASSES and c["deg"] >= FT.MIN_CREASE_DEG - 1e-9


def test_an_accepted_declaration_never_leaves_a_reading_beyond_its_cost():
    """Cost and solution are one model (probe1/probe_settle3.py): probe1's flat crown with shoulder|base declared a
    valley was accepted at +1.7 (positions free, phase 1) while its solution left D 7.0 sd off its X reading. In one
    model the solution moves what the cost assumed moved: refused at the chi2 its solution pays, 15.6 with the corners
    unknowns (v3p2-fit2-fix; 49 in v3p2-fit2, its corners where they were read, against "the camera turn of fit 02"),
    against the tip E (3.5 sd: its d read to 1.2 mm; review 2026-10-12 P1 found E the lever no refusal named).
    An accepted declaration's chi2 is its readings' chi2 (X row and position rows) plus the turns' squares: so none is
    off by more than the root of chi2_free + the cost."""
    decl = [{"facets": ["shoulder", "base"], "shape": "valley"}]
    s = probe1_design("nodes", decl)
    pb = FT._Problem(s)
    free, _ = FT._free_fit(pb)
    one = FT._declared_fit(pb, [(("shoulder", "base"), "valley")], free)
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    x = e.value
    assert x.code == "E_FIT_SHAPE" and x.fields["dchi2"] == pytest.approx(one.chi2 - free.chi2, rel=1e-9)
    assert 10.0 < x.fields["dchi2"] < 25.0
    (c,) = x.fields["creases"]
    assert c["moves"][0]["source"] == "read" and c["moves"][0]["node"] == "E" and c["moves"][0]["sd"] > 3.0
    assert ", against the readings of E (" in x.message and "camera" not in x.message
    # accepted declarations that cost: probe1's warp crown with lower|shoulder declared a ridge (readings -4.3 +- 4.4,
    # the camera terms in), and tilted_pair (sd_d 1.0) with D|base declared a valley (readings -0.4 +- 1.1)
    s = tilted_pair({n: np.diag([1.0, 0.16, 0.01]) for n in ("P1", "P2", "U1", "D1")})
    s["creases"] = [{"facets": ["D", "base"], "shape": "valley"}]
    for res, pair in ((FT.fit_spec(probe1_design("warp", [{"facets": ["lower", "shoulder"], "shape": "ridge"}])),
                       "lower|shoulder"), (FT.fit_spec(s), "D|base")):
        c = {x["name"]: x for x in res.creases}[pair]
        assert 1.0 < c["dchi2"] <= FT.ACCEPT_DCHI2 and res.chi2 - res.chi2_free == pytest.approx(c["dchi2"], abs=1e-12)
        terms = sum(r["chi2"] if r["source"] == "read" else r["residual_sd"] ** 2 for r in res.nodes.values()
                    if r["source"] in ("read", "typed"))
        assert terms + sum(v["u"] ** 2 for v in res.camera.values()) == pytest.approx(res.chi2, rel=1e-9, abs=1e-12)
        worst = max(math.sqrt(r["chi2"]) for r in res.nodes.values() if r["source"] == "read")
        assert 1.0 < worst <= math.sqrt(res.chi2_free + c["dchi2"]) + 1e-9, pair


def test_a_reading_observes_its_corner_jointly_and_a_pinned_direction_is_carried():
    """v3p2-fit2-fix (review 2026-10-12 P1, the design changed: this replaces the effective-variance weights test). A
    free corner's position is an unknown observed with X jointly: its chi2 is exactly the Mahalanobis distance of its
    whole residual (fitted - read in d, y and X) under its pick cov, its X row's sd the conditional one
    sqrt(C_XX - C_Xp C_pp^-1 C_pX). A corner on the hinge-end cap slides along it (its d pinned at the hinge plane,
    as probe1's A and L): its reading's X is carried along the face from where it was read to the node, X - g_d dd
    (g the face's gradient), with the variance v' C v, v = (-g_d, 0, 1). A warp's gradient at its corner (the mean of
    its two edges' MVC gradients CORNER_NEAR_MM along them) is the plane's on a planar warp, and stays bounded at a
    nearly straight corner (probe1's warp crown K, its lower edge bent ~4 deg: the plane through the two edges gave a
    cross slope of 17 there)."""
    C = np.array([[0.36, 0.05, -0.04], [0.05, 0.09, 0.0], [-0.04, 0.0, 0.0225]])
    rng = np.random.default_rng(3)
    nodes = {}
    for n, (d, y) in CAM_POS.items():                # one flat face read 5 times: 2 extra corners to misfit
        e = np.linalg.cholesky(C) @ rng.normal(size=3)
        r = {"id": n, "X": cam_plane(d, y) + float(e[2]), "sd": 0.15, "d": d + float(e[0]), "y": y + float(e[1]),
             "cov": C.tolist()}
        nodes[n] = [r["d"], r["y"], {"read": r}]
    res = FT.fit_spec(FX.straight(nodes=nodes, facets={"T": list(CAM_POS)}))
    Ci = np.linalg.inv(C)
    sc = math.sqrt(C[2, 2] - C[2, :2] @ np.linalg.solve(C[:2, :2], C[:2, 2]))
    for n, r in res.nodes.items():
        rd = r["reading"]
        e = np.array([r["d"] - rd["d"], r["y"] - rd["y"], r["X"] - rd["X"]])
        assert r["chi2"] == pytest.approx(float(e @ Ci @ e), rel=1e-9) and r["obs_sd"] == pytest.approx(sc), n
    assert res.chi2 > 0.1 and sum(r["chi2"] for r in res.nodes.values()) == pytest.approx(res.chi2, rel=1e-9)
    assert res.model["positions_settled"] and res.model["position_unknowns"] == 10
    Cc = np.diag([0.04, 0.09, 0.0625])
    nodes = {"P": [0.0, -6.0, {"read": {"id": "P", "X": 64.6, "sd": 0.25, "d": 0.4, "y": -6.0, "cov": Cc.tolist()}}],
             "Q": [60.0, -6.0, read(65.0, 0.2)], "R": [60.0, 6.0, read(64.4, 0.2)]}
    s = FX.straight(nodes=nodes, facets={"T": ["P", "Q", "R"]})
    res = FT.fit_spec(s)
    pb = FT._Problem(s)
    assert pb.Bn["P"].ravel().tolist() in ([0.0, 1.0], [0.0, -1.0]) and pb.M == 1        # it slides along the cap
    gd = res.spec["facets"]["T"]["plane"]["coef"][1]
    P = res.nodes["P"]
    assert P["d"] == 0.0 and P["obs_X"] == pytest.approx(64.6 - 0.4 * gd, abs=1e-9)
    assert P["obs_sd"] == pytest.approx(math.sqrt(0.0625 + gd * gd * 0.04), rel=1e-9)
    assert res.model["weights_settled"] and res.chi2 < 1e-9
    nodes = {n: [*POS[n], read(64.0 + 0.03 * POS[n][0] - 0.05 * POS[n][1], 0.2)] for n in ("NE", "SE", "Q2", "Q1")}
    pb = FT._Problem(FX.straight(nodes=nodes, facets={"Q": {"poly": ["NE", "SE", "Q2", "Q1"], "plane": "warp"}}))
    free, _ = FT._free_fit(pb)
    for n in ("NE", "SE", "Q2", "Q1"):
        assert pb.region_grad_form(n, "Q") @ free.z[:pb.N] == pytest.approx([0.03, -0.05], abs=1e-9), n
    pb = FT._Problem(probe1_design("warp"))
    free, _ = FT._free_fit(pb)
    assert float(np.abs(pb.region_grad_form("K", "crown") @ free.z[:pb.N]).max()) < 1.0


# ------------------------------------------------------------------------------------- v3p2-fit2-fix (phase 2 review)
# The findings of review 2026-10-12 phase 2 on v3p2-fit2 (data/wp6v2/review_20261012/v3/v3p2-fit2-fix.md): P1 the
# facet|base border turns with its ends (the corners' positions now unknowns), P2 a reading used off where it was read,
# P3 the flatness test's own face, the damped passes, the three crease states, the refusal's camera wording,
# read_payload's camera part; and the builder's snapping of a moved corner.
P1_NAMES = ("A", "H", "K", "E", "C", "L", "D")


def probe1_reading_design(crown: str, rd: dict, creases=()) -> dict:
    """probe1_design with the readings at rd {name: (d, y, X)} (their pick cov and camera shifts probe1's), the nodes
    and the outline where they say (A, L held at the hinge plane): the lab's process, as probe1 spliced it."""
    tab = {n: (0.0 if n in "AL" else rd[n][0], rd[n][1], rd[n][2], 0.3) for n in P1_NAMES}
    s = run7_layout(crown, creases, rd=tab)
    for n in P1_NAMES:
        rec = probe1_record(n)
        rec["d"], rec["y"], rec["X"] = rd[n]
        s["nodes"][n][2] = FT.read_payload(rec)
    return s


def probe1_truth(crown: str, flat_border: bool = False) -> dict:
    """A consistent truth {name: (d, y, X)} from probe1's readings: the readings-only fit's corners and faces; with
    flat_border the tip E at its read (d, y) on the fitted crown plane (shoulder|base then folds -1.1 at mid, +0.0 as
    a mean, its midpoint 0.46 mm from the base station at d 75.64: the review's case)."""
    rd0 = {n: PROBE1_READ[n][:3] for n in P1_NAMES}
    res = FT.fit_spec(probe1_reading_design(crown, rd0))
    tp = {n: (res.nodes[n]["d"], res.nodes[n]["y"], res.nodes[n]["X"]) for n in P1_NAMES}
    if flat_border:
        a, b, c = res.spec["facets"]["crown"]["plane"]["coef"]
        d, y = rd0["E"][:2]
        tp["E"] = (d, y, a + b * d + c * y)
    return tp


def probe1_draw(tp: dict, rng) -> dict:
    """One exact-process reading of every corner: the truth, one theta turn per camera fit (shared by its readings)
    and the pick noise of its cov."""
    u = {f: rng.normal() for f in ("02", "05")}
    rd = {}
    for n in P1_NAMES:
        _, _, _, C, cam = PROBE1_READ[n]
        L = np.linalg.cholesky(np.asarray(C, float) + 1e-12 * np.eye(3))
        q = np.asarray(tp[n], float) + sum(u[f] * np.asarray(s_, float) for f, s_ in cam.items()) + L @ rng.normal(
            size=3)
        rd[n] = tuple(float(v) for v in q)
    return rd


def test_exact_process_monte_carlo_on_probe1_calibrates_every_crease_and_the_border():
    """P1 (review 2026-10-12 phase 2, probe_mc.py): on probe1's real layout and readings, each corner read again by the
    exact process (truth + a shared camera turn per fit + pick noise; the node and the outline where the reading
    says), the fitted angles' spread matches the fit's sd for every crease, the facet|base border included: v3p2-fit2's
    shoulder|base had sd 0.40 against a spread of 3.14 (7.9x) on the flat-border truth, certain with the wrong shape in
    55 % of draws, and refused the near-true valley / ridge in 73 % / 79 % (median costs 35 / 98) where moving the tip
    E 1 mm (0.8 of its d sd) builds either at about 2.5. Here (120 draws; scratch Monte Carlo of 200: every crease
    1.00-1.07, shoulder|base 1.03, wrong-shape certain 15.5 %, both declarations refused 21.5 % at median cost
    1.3-1.5): a declaration about 1 sd from the truth is refused about as often as a 2-sd rule refuses one (~24 %),
    and the truth's own shape (the warp crown's valley) almost never; the flat crown's flatness test false-alarms near
    its nominal rate."""
    rng = np.random.default_rng(20261012)
    tp = probe1_truth("nodes", flat_border=True)
    truth = {c["name"]: c for c in FT.fit_spec(probe1_reading_design("nodes", tp)).creases}
    assert truth["shoulder|base"]["deg"] == pytest.approx(-1.06, abs=0.05) and truth["shoulder|base"]["state"] == "open"
    ang, sds, means, msds, wrong, alarms = {}, {}, {}, {}, 0, 0
    dec = {"valley": [], "ridge": []}
    for k in range(120):
        rd = probe1_draw(tp, rng)
        res = FT.fit_spec(probe1_reading_design("nodes", rd))
        for c in res.creases:
            ang.setdefault(c["name"], []).append(c["free_deg"])
            sds.setdefault(c["name"], []).append(c["sd_deg"])
            means.setdefault(c["name"], []).append(c["free_mean_deg"])
            msds.setdefault(c["name"], []).append(c["free_mean_sd_deg"])
        sb = {c["name"]: c for c in res.creases}["shoulder|base"]
        wrong += sb["state"] == "certain"                # the truth is flat: any certain shape is wrong
        alarms += res.faces["crown"]["not_flat"]
        if k < 50:
            for shape in dec:
                try:
                    r = FT.fit_spec(probe1_reading_design("nodes", rd, [{"facets": ["shoulder", "base"],
                                                                         "shape": shape}]))
                    dec[shape].append(r.chi2 - r.chi2_free)
                except TempleError as e:
                    assert e.code == "E_FIT_SHAPE", e.message
                    dec[shape].append(e.fields["dchi2"])
    for name in ang:
        ratio = float(np.std(ang[name], ddof=1) / np.median(sds[name]))
        assert abs(ratio - 1.0) < 0.2, (name, ratio)
    m_ratio = float(np.std(means["shoulder|base"], ddof=1) / np.median(msds["shoulder|base"]))
    assert abs(m_ratio - 1.0) < 0.25, m_ratio                         # the builder's vote, the mean dihedral
    assert wrong / 120 < 0.25 and alarms / 120 < 0.10, (wrong, alarms)
    for shape, costs in dec.items():
        c = np.array(costs, float)
        assert np.mean(c > FT.ACCEPT_DCHI2) < 0.40 and np.median(c) < 3.0, (shape, np.mean(c > FT.ACCEPT_DCHI2),
                                                                             np.median(c))
    tw = probe1_truth("warp")
    refused = 0
    for _ in range(30):
        rd = probe1_draw(tw, rng)
        try:
            FT.fit_spec(probe1_reading_design("warp", rd, [{"facets": ["shoulder", "base"], "shape": "valley"}]))
        except TempleError as e:
            assert e.code == "E_FIT_SHAPE", e.message
            refused += 1
    assert refused <= 3, refused                                      # its true shape (Monte Carlo of 200: 0.5 %)


def test_a_reading_used_off_where_it_was_read_returns_there_or_is_carried():
    """P2 (probe_offset.py): the lab lets a node stand up to 0.5 mm from where its reading was taken; the fit used the
    reading's X at the node unchanged and said nothing (probe1's warp crown, K 0.5 mm up: the crown's X there 0.29 mm
    = 1.5 sd off, crown|shoulder +3.4 deg). With its position error (every readings.py record) the corner is an
    unknown and returns to its reading: the creases do not move. A direction the corner cannot move in (off the
    outline: H 0.45 mm above its knot; a reading without position error) carries its X along the faces to the node,
    and a face through it changing by more than OFF_CARRY_SD of its sd there is a fact line with the faces' spread."""
    base = FT.fit_spec(probe1_design("warp"))
    a0 = {c["name"]: c["deg"] for c in base.creases}
    s = probe1_design("warp")
    s["nodes"]["K"][1] += 0.5
    res = FT.fit_spec(s)
    K = res.nodes["K"]
    assert (K["d"], K["y"]) == pytest.approx((K["reading"]["d"], K["reading"]["y"]), abs=1e-6)
    assert K["move_mm"] == pytest.approx([0.0, -0.5], abs=1e-6) and K["pos_sd"] < 1e-6
    assert all(abs(c["deg"] - a0[c["name"]]) < 0.01 for c in res.creases)
    (ln,) = [x for x in res.report_lines(limit=40) if x.startswith("corners the fit placed off the design")]
    assert " K +0.00 -0.50 (0.0 sd)" in ln
    s = probe1_design("warp")
    s["nodes"]["H"][1] += 0.45                         # above its outline knot: outside, pinned
    res = FT.fit_spec(s)
    (w,) = [x for x in res.warnings if x.get("node") == "H"]
    assert w["kind"] == "position" and max(abs(c) for c in w["faces_mm"]) > FT.OFF_CARRY_SD * res.nodes["H"]["obs_sd"]
    assert w["message"].startswith("node H at d 36.24, y 15.61: its reading at d 36.24 +- 0.16, y 15.16 +- 0.12 "
                                   "(0.45 mm away; its X carried ")
    x_only = probe1_design("warp")
    for v in x_only["nodes"].values():
        v[2]["read"].pop("cov")
    x_only["nodes"]["K"][1] += 0.5
    res = FT.fit_spec(x_only)
    (w,) = [x for x in res.warnings if x.get("node") == "K"]
    pb = FT._Problem(x_only)
    FT._free_fit(pb)
    assert w["carried_mm"] == pytest.approx(float(pb.gpin["K"] @ np.array([0.0, 0.5])), abs=1e-9)
    assert res.nodes["K"]["obs_X"] == pytest.approx(70.044 + w["carried_mm"], abs=1e-9)
    assert min(w["faces_mm"]) < -0.25 and "(the faces through it " in w["message"]   # the crown's -0.32


def test_flatness_test_weighs_each_corner_by_this_faces_own_gradient():
    """P3 (probe_flat_rate.py): the flat test took each corner's sd from the mean over every face through it (K: four
    faces, y slopes -0.31..+0.42; the crown's own sd 0.204 against the mean 0.190) and over-fired 1.3x. Now each face's
    test weighs a reading by v' C v with its own gradient, at the point where it was read, with its own camera
    coefficient dX - g . (dd, dy)."""
    pb = FT._Problem(probe1_design("nodes"))
    free, _ = FT._free_fit(pb)
    g = np.array(free.z[pb.col["crown"] + 1:pb.col["crown"] + 3])
    known = pb.poly["crown"]
    P_, X_, sd_, Acam = FT._flat_inputs(pb, "crown", known, g)
    v = np.array([-g[0], -g[1], 1.0])
    for k, n in enumerate(known):
        C = np.array(PROBE1_READ[n][3])
        assert sd_[k] == pytest.approx(math.sqrt(v @ C @ v), rel=1e-12), n
        assert tuple(P_[k]) == (PROBE1_READ[n][0], PROBE1_READ[n][1]) and X_[k] == PROBE1_READ[n][2]
        for j, f in enumerate(sorted({"02", "05"})):
            s_ = np.array(PROBE1_READ[n][4][f])
            assert Acam[k, j] == pytest.approx(s_[2] - g @ s_[:2], abs=1e-12)
    assert sd_[known.index("K")] > 1.05 * math.sqrt(np.mean([v_ @ np.array(PROBE1_READ["K"][3]) @ v_ for v_ in (
        np.array([-gf[0], -gf[1], 1.0]) for gf in pb.face_grads("K", free.z))]))


def test_oscillating_passes_are_damped_and_an_unsettled_one_is_said(monkeypatch):
    """P3 (probe_weights_case.py): the gradient passes could cycle (a corner's sd 0.135 <-> 0.496 every pass) and pass
    30 kept whichever half it landed on, said only in FitResult.model. A step that does not contract to 0.9 of the last
    now halves (WEIGHT_DAMP_MIN; the corner passes likewise, POS_DAMP_MIN): a map with slope -1, which cycles
    undamped, settles; undamped, the report says it did not settle."""
    nodes = {"P": [0.0, -6.0, {"read": {"id": "P", "X": 64.6, "sd": 0.25, "d": 0.4, "y": -6.0,
                                        "cov": np.diag([0.04, 0.09, 0.0625]).tolist()}}],
             "Q": [60.0, -6.0, read(65.0, 0.2)], "R": [60.0, 6.0, read(64.4, 0.2)]}
    s = FX.straight(nodes=nodes, facets={"T": ["P", "Q", "R"]})
    real = FT._Problem.pinned_grads

    def cycle(self, z):                                  # the target mirrors the current gradient: g -> c - g
        return {n: np.array([0.05, -0.02]) - self.gpin[n] for n in real(self, z)}
    monkeypatch.setattr(FT._Problem, "pinned_grads", cycle)
    res = FT.fit_spec(s)
    assert res.model["weights_settled"] and not any("did not settle" in ln for ln in res.report_lines(limit=40))
    monkeypatch.setattr(FT, "WEIGHT_DAMP_MIN", 1.0)
    res = FT.fit_spec(s)
    assert not res.model["weights_settled"] and res.model["weight_passes"] == FT.WEIGHT_PASSES
    assert res.model["weight_change"] == pytest.approx(0.12, abs=1e-9)            # the cycle's half-swing, material
    # (v3p3-clean-fit wording: "the pinned-direction gradients ... moved an X row" named no thing Astra knows)
    assert (f"the readings-only fit did not settle in {FT.WEIGHT_PASSES} passes: its last pass still moved a reading's "
            f"X, carried to its corner, by 0.12 of its sd") in res.report_lines(limit=40)


def test_crease_states_and_the_refusals_camera_wording():
    """P3 (probe_report.py): "open" also labelled a fold held flat with a small sd, whose every declaration is refused;
    a refusal named "the camera turn of fit 02" among the readings the prompt sends to pick sheets. Three states now:
    certain, open (a declaration can set it), flat within 2 sd (|angle| + 2 sd < MIN_CREASE_DEG: neither builds within
    the readings); the camera turns a declaration needs are a host fact apart from the readings."""
    s = crease_pair(0.05, valley_deg=0.3)
    s["creases"] = []
    res = FT.fit_spec(s)
    c = {x["name"]: x for x in res.creases}["D|U"]
    assert c["state"] == "flat" and not c["certain"] and abs(c["deg"]) + 2 * c["sd_deg"] < FT.MIN_CREASE_DEG
    assert any(ln.startswith("D|U flat ") and ", flat within 2 sd" in ln for ln in res.report_lines(limit=40))
    assert {x["state"] for x in FT.fit_spec(crease_pair(0.3)).creases} <= {"certain", "open", "flat"}
    moves = [{"node": "A", "source": "read", "sd": 2.9}, {"node": "D", "source": "read", "sd": -2.8},
             {"node": "02", "source": "camera", "sd": -5.6}]
    assert FT._against(moves) == ("against the readings of A (2.9 sd), D (2.8 sd); it turns the camera of fit 02 by "
                                  "5.6 sd of its theta error (a host camera fit)")
    assert FT._against(moves[2:]) == "it turns the camera of fit 02 by 5.6 sd of its theta error (a host camera fit)"


def test_read_payload_takes_the_camera_part_out_of_a_total_sd():
    """P3: a record with camera_shift but no pick sd fell back to the total sd_X / sd_d / sd_y (pick and camera in
    quadrature) and the camera part was counted twice. Now the camera part is taken out in quadrature; an X total with
    no pick part left is a ValueError."""
    rec = probe1_record("E")
    cam = rec["camera_shift"]
    tot = {k: math.sqrt(p ** 2 + sum(v[i] ** 2 for v in cam.values())) for i, (k, p) in
           enumerate((("d", 1.23), ("y", 0.31), ("X", 0.85)))}
    r = FT.read_payload(dict(rec, cov_pick=None, sd_X_pick=None, sd_X=tot["X"], sd_d=tot["d"], sd_y=tot["y"]))["read"]
    assert (r["sd"], r["sd_d"], r["sd_y"]) == pytest.approx((0.85, 1.23, 0.31), rel=1e-12) and r["camera"] == cam
    with pytest.raises(ValueError):
        FT.read_payload(dict(rec, cov_pick=None, sd_X_pick=None, sd_X=0.4, sd_d=1.3, sd_y=0.4))   # 0.4 < 0.45 camera


def test_a_moved_corner_is_where_the_builder_puts_it():
    """The fitted corners are a fixed point of the builder's snapping (_Problem.builder_point): cam_pair's D2 moved to
    d 65.009 beside U2's 65.0 was snapped by the builder (E_FACET_NOT_PLANAR 3.7e-5 mm). Every free corner is written
    where the fit put it, and parse leaves every node where the fitted spec says."""
    res = FT.fit_spec(cam_pair({"02": 2.5}))
    ps = parse(res.spec)
    for n, r in res.nodes.items():
        assert (ps.nodes[n][0], ps.nodes[n][1]) == pytest.approx((r["d"], r["y"]), abs=1e-12), n
    d = sorted(r["d"] for r in res.nodes.values())       # no two corners within snap_mm and apart
    assert all(b - a < 1e-9 or b - a > 0.05 for a, b in zip(d, d[1:])), d
    res = FT.fit_spec(probe1_design("nodes"))
    ps = parse(res.spec)
    for n, r in res.nodes.items():
        assert (ps.nodes[n][0], ps.nodes[n][1]) == pytest.approx((r["d"], r["y"]), abs=1e-12), n
    top = {tuple(p) for p in res.spec["outline"]["top"]}
    assert (res.nodes["E"]["d"], res.nodes["E"]["y"]) in top and res.nodes["E"]["move_mm"][0] < -2.0


def test_a_corner_beside_a_station_is_held_where_the_declaration_needs_it():
    """The fitted corners are snapped or pushed to where the builder reproduces them (_Problem.builder_point): a draw
    of probe1's flat-border truth (the Monte Carlo's 24th) with shoulder|base declared a ridge puts the tip E just
    past the base station at d 75.64 (the fold's midpoint on the ridge side), 0.005 mm from the inner knot at 76.0;
    taking that knot's d put the midpoint back across the station (cost 26.4). The declared fit holds both points the
    builder reproduces, snapped and pushed POS_PUSH_MM beyond snap_mm, and keeps the cheaper: E at 75.949, cost 0.14."""
    tp = probe1_truth("nodes", flat_border=True)
    rng = np.random.default_rng(20261012)
    for _ in range(24):
        rd = probe1_draw(tp, rng)
    res = FT.fit_spec(probe1_reading_design("nodes", rd, [{"facets": ["shoulder", "base"], "shape": "ridge"}]))
    E = res.nodes["E"]
    assert res.chi2 - res.chi2_free < 1.0
    assert E["d"] == pytest.approx(76.0 - 0.05 - FT.POS_PUSH_MM, abs=1e-9)
    ps = parse(res.spec)
    assert (ps.nodes["E"][0], ps.nodes["E"][1]) == pytest.approx((E["d"], E["y"]), abs=1e-12)
    c = {x["name"]: x for x in res.creases}["shoulder|base"]
    assert c["deg"] >= FT.MIN_CREASE_DEG and c["mean_dihedral_deg"] >= FT.FLAT_CREASE_DEG + FT.MEAN_MARGIN_DEG - 1e-6


# --------------------------------------------------------------------------------------------- report text (v3p3)
# v3-integrate-2 / session2.md (the v3 lab driven through its tools on a copy of package_007): the fit report said
# "did not settle" at 4.7e-08 sd, printed the camera prior "+0.00 +- 1.00" as a result on every dof-0 fit, and "-0.00".
# The records below carry session2's stored values (v3/session2/pkg/work/temple_lab/revisions/rNNNN.fit.json, which
# the lab reads back as FitResult(**record["fit"])).
S2_R0004_NODES = {      # session2 r0004 (the flat-crown variant arm): (move_mm, pos_sd, residual_mm, residual_sd)
    "A": ([0.0, 0.014091178306431473], 0.17478698557787287, 0.05568294315144949, 0.3262215012772458),
    "C": ([0.007253836953367454, 0.0018822455031877183], 2.4e-13, -1.1342105369356485e-14, -5.7e-14),
    "D": ([-0.003986430011607922, 0.02809310772992768], 4.1e-13, -2.4186007047958867e-14, -1.1e-13),
    "E": ([-2.9012278373548526, 0.8977652633434374], 2.8232431104926183, 0.40825384133104803, 1.1374023548409298),
    "H": ([0.0168080879535637, 0.0067497947371606415], 0.05272110115686065, -0.011815178262662467, -0.0723),
    "K": ([0.08356885436734274, -0.042645576221412185], 0.5664414897925276, -0.12528104415846197, -0.7622625651646331),
    "L": ([0.0039570810161215775, -0.007458694603807103], 1.4e-13, -1.1106571237171265e-14, -5.7e-14)}
S2_SETTLED = {"weight_passes": 5, "weights_settled": True, "weight_change": 1.968230870770605e-11,
              "position_passes": 5, "positions_settled": True, "position_step": 4.569308067025054e-15,
              "snap_reverted": [], "angle_passes": 0, "position_unknowns": 13}


def stored_fit(camera: dict, model: dict, nodes: dict | None = None, faces: dict | None = None, creases=(),
               chi2: float = 10.478822932706858, dof: int = 1) -> FT.FitResult:
    """A FitResult as the lab rebuilds a stored record (FitResult(**rec["fit"])): read corners {name: (move_mm, pos_sd,
    residual_mm, residual_sd)}, camera {fit: u} (sd 1, as on a dof-0 fit), the model's settle fields."""
    nodes = S2_R0004_NODES if nodes is None else nodes
    rec = {n: {"source": "read", "move_mm": mv, "pos_sd": ps, "residual_mm": rm, "residual_sd": rs, "at_bound": False,
               "at_cap": False} for n, (mv, ps, rm, rs) in nodes.items()}
    return FT.FitResult(spec={}, nodes=rec, faces=faces or {}, creases=list(creases), chi2=chi2, chi2_free=chi2,
                        n_obs=len(rec), dof=dof, counts={"read": len(rec), "typed": 0, "base": 0, "solve": 0},
                        camera={f: {"u": u, "sd": 1.0, "free_u": u, "readings": sorted(rec)}
                                for f, u in camera.items()},
                        model=dict(model))


def test_the_report_says_no_unsettled_pass_below_a_printed_digit(monkeypatch):
    """session2 step 12 (r0004, the flat crown): "the pinned-direction gradients did not settle in 30 passes (the last
    moved an X row by 4.7e-08 of its sd)": settled for every printed number (WEIGHT_TOL is 1e-9 sd), read as a fit
    failure, and it pushed two face lines out of the 12-line receipt. A pass is said only where it still moves a
    reading's carried X WEIGHT_SHOWN_SD (1e-3) of its sd or a corner POS_SHOWN_MM (1e-3 mm): under the damping's 0.9
    contraction what remains is < 0.01 sd / 0.01 mm. FitResult.model keeps the solver's own flags. The stored receipt
    is now session2's facts only, the camera turns in words."""
    s2 = dict(S2_SETTLED, weight_passes=30, weights_settled=False, weight_change=4.6807804188864956e-08,
              position_passes=30, position_step=1.1629763818632455e-10)
    res = stored_fit({"02": 0.31483686781539166, "05": -0.26161223281999946}, s2)
    assert res.model["weights_settled"] is False
    assert res.report_lines(limit=12, creases=False) == [
        "fit: chi2 10.5, dof 1, from 7 readings and 0 typed values; readings-only chi2 10.5",
        "camera turn of fit 02: 0.3 sd, of fit 05: 0.3 sd (in sd of that camera fit's theta error; one turn for all of "
        "its readings)",
        "corners the fit placed off the design (fitted - design, d y mm; fitted - read in its sd): E -2.90 +0.90 (2.8 "
        "sd), K +0.08 -0.04 (0.6 sd)",
        "misfit beyond 1 sd (fitted - read, after the camera turns): E (d, y) 2.8 sd and X +0.41 mm (1.1 sd)"]
    for key, step, said in (("weight", FT.WEIGHT_SHOWN_SD, "moved a reading's X, carried to its corner, by 0.001 of "
                                                           "its sd"),
                            ("position", FT.POS_SHOWN_MM, "moved a corner 0.001 mm")):
        field = "weight_change" if key == "weight" else "position_step"
        flag = "weights_settled" if key == "weight" else "positions_settled"
        below = stored_fit({}, dict(S2_SETTLED, **{flag: False, field: 0.999 * step, f"{key}_passes": 30}))
        assert not any("did not settle" in ln for ln in below.report_lines(limit=40)), key
        at = stored_fit({}, dict(S2_SETTLED, **{flag: False, field: step, f"{key}_passes": 30}))
        assert (f"the readings-only fit did not settle in 30 passes: its last pass still {said}"
                in at.report_lines(limit=40)), key
        settled = stored_fit({}, dict(S2_SETTLED, **{field: 0.5}))         # settled by the solver: never said
        assert not any("did not settle" in ln for ln in settled.report_lines(limit=40)), key
    # a real fit held to a tolerance it cannot meet: both flags False at changes ~1e-13; the report is the settled one's
    plain = FT.fit_spec(probe1_design("nodes")).report_lines(limit=40)
    monkeypatch.setattr(FT, "WEIGHT_TOL", 1e-300)
    monkeypatch.setattr(FT, "POS_TOL_MM", 1e-300)
    res = FT.fit_spec(probe1_design("nodes"))
    assert not res.model["weights_settled"] and not res.model["positions_settled"]
    assert res.model["weight_change"] < 1e-9 and res.model["position_step"] < 1e-9
    assert res.report_lines(limit=40) == plain and not any("did not settle" in ln for ln in plain)


def test_the_report_prints_a_camera_turn_only_where_the_data_moved_it():
    """session2 printed "camera turn per fit (...): 02 +0.00 +- 1.00, 05 +0.00 +- 1.00" on every dof-0 fit (r0001
    stored u 1.9e-13 / 7.7e-14) and "02 -0.00" (r0005, -1.6e-13): the prior read as a result. A turn is printed where
    the data moved it at least CAMERA_SHOWN_SD (it prints to 0.1 sd), unsigned and in words as E_FIT_SHAPE says it,
    and the misfit line says "after the camera turn(s)" only beside a printed one. probe1's warp crown (dof 0) prints
    none; its flat crown (dof 1) turns both fits 0.3 sd."""
    for cam in ({"02": 1.9051419864002737e-13, "05": 7.727144048322786e-14},
                {"02": -1.5987212411065974e-13, "05": 1.2079266186459173e-13}):
        lines = stored_fit(cam, S2_SETTLED, nodes={}, chi2=0.0, dof=0).report_lines(limit=40)
        assert lines == ["fit: chi2 0.0, dof 0, from 0 readings and 0 typed values; readings-only chi2 0.0"], cam
    one = stored_fit({"02": -0.4, "05": 0.049}, S2_SETTLED).report_lines(limit=40)
    assert one[1] == ("camera turn of fit 02: 0.4 sd (in sd of that camera fit's theta error; one turn for all of its "
                      "readings)")
    assert "misfit beyond 1 sd (fitted - read, after the camera turn): E (d, y) 2.8 sd and X +0.41 mm (1.1 sd)" in one
    none = stored_fit({"02": 0.049, "05": -0.049}, S2_SETTLED).report_lines(limit=40)
    assert not any(ln.startswith("camera") for ln in none)
    assert "misfit beyond 1 sd (fitted - read): E (d, y) 2.8 sd and X +0.41 mm (1.1 sd)" in none
    warp = FT.fit_spec(probe1_design("warp"))
    assert warp.dof == 0 and all(abs(v["u"]) < 1e-9 and v["sd"] == pytest.approx(1.0) for v in warp.camera.values())
    assert not any(ln.startswith("camera") for ln in warp.report_lines(limit=40))
    flat = FT.fit_spec(probe1_design("nodes")).report_lines(limit=40)
    assert flat[1] == ("camera turn of fit 02: 0.3 sd, of fit 05: 0.3 sd (in sd of that camera fit's theta error; one "
                       "turn for all of its readings)")
    assert ("misfit beyond 1 sd (fitted - read, after the camera turns): E (d, y) 2.99 mm (2.8 sd) and X +0.41 mm (1.1 "
            "sd)") in flat


def test_no_report_text_prints_a_signed_zero():
    """"-0.00" (session2's camera line) is never printed: _signed gives a value that rounds to zero a "+", _fixed no
    sign. A synthetic record with every signed field just below zero (the misfit of a reading read to 0.003 mm, a
    node at the thickness bound, a flat crease and its range, a face's flat misfit, a corner's move), and the real
    fits of this module's fixtures, print no "-0.0"."""
    assert FT._signed(-0.004) == "+0.00" and FT._signed(-1e-13, 1) == "+0.0" and FT._signed(0.0) == "+0.00"
    assert FT._signed(-0.006) == "-0.01" and FT._signed(0.004, 3) == "+0.004" and FT._signed(-2.9) == "-2.90"
    assert FT._fixed(-0.001) == "0.00" and FT._fixed(-0.01) == "-0.01" and FT._fixed(4.6349) == "4.63"
    nodes = {"E": ([-0.004, -0.001], 0.2, -0.004, -1.3), "K": ([0.06, -0.002], 0.3, -0.001, -0.2)}
    face = {"kind": "flat", "corners": ["E", "K", "P", "Q"], "known": ["E", "K", "P", "Q"], "extra": 1,
            "flat_chi2": 1.7, "not_flat": False, "misfit_at": "E", "flat_misfit_mm": -0.004, "misfit_sd": 1.3}
    crease = {"pair": ["T", "U"], "name": "T|U", "deg": -0.04, "sd_deg": 1.2, "held": False, "range_deg": [-0.04, 0.9],
              "shape": "flat", "state": "open", "certain": False, "normal_deg": 0.04, "bound": [], "declared": "ridge",
              "dchi2": 0.4, "free_deg": -0.02, "free_sd_deg": 1.2}
    res = stored_fit({"02": -0.3}, S2_SETTLED, nodes=nodes, faces={"T": face}, creases=[crease])
    res.nodes["K"]["at_bound"] = True
    lines = res.report_lines(limit=40)
    assert "misfit beyond 1 sd (fitted - read, after the camera turn): E (d, y) 0.2 sd and X +0.00 mm (1.3 sd)" in lines
    assert "at the thickness bound: K (+0.00 mm from its reading)" in lines
    assert ("face T flat (E K P Q): chi2 1.7 for 1 extra corner; its largest misfit (fitted - read): E (d, y) 0.2 sd "
            "and X +0.00 mm (1.3 sd)" in lines)
    assert ("T|U flat +0.0 +- 1.2 deg at mid, +0.0..+0.9 along the edge, open, declared ridge (readings-only +0.0 +- "
            "1.2 deg, cost +0.4)") in lines
    assert ("corners the fit placed off the design (fitted - design, d y mm; fitted - read in its sd): K +0.06 +0.00 "
            "(0.3 sd)") in lines
    real = [FT.fit_spec(probe1_design(c)) for c in ("nodes", "warp")] + [
        FT.fit_spec(cam_face({"02": 2.5})), FT.fit_spec(cam_face({}, lift={"C": 1.5}, w02=0.9)),
        FT.fit_spec(run7_layout("nodes")), FT.fit_spec(run7_layout("warp"))]
    for r in real + [res]:
        for ln in r.report_lines(limit=99):
            assert not re.search(r"-0\.0+(?![0-9])", ln), ln


def test_the_report_says_what_it_left_out_and_why_a_face_is_untested():
    """The receipt's last line was "(+2 more lines in the fit report)" (session2 step 12): no tool shows a longer
    report. It now names what was left out, by kind and count, singular where one. "flat faces with no extra corner"
    named "extra" nowhere on its own line: a flat face with fewer than 4 read, typed or base corners has no flatness
    test (3 set its plane); one whose 4 or more lie in one line has none either, and says so."""
    faces = {"V": {"kind": "warp", "corners": ["E", "K", "P", "Q"], "warp_mm": 1.34, "corner_chi2": 0.0},
             "T": {"kind": "flat", "corners": ["E", "K", "P"], "known": ["E", "K"], "extra": 0},
             "U": {"kind": "flat", "corners": ["E", "K", "P", "Q"], "known": ["E", "K", "P", "Q"], "extra": 0}}
    res = stored_fit({}, S2_SETTLED, faces=faces)
    lines = res.report_lines(limit=40)
    assert lines[-3:] == ["face V warp (E K P Q): 1.34 mm off one plane; corner residuals chi2 0.0",
                          "flat faces with fewer than 4 read, typed or base corners (no flatness test): T",
                          "flat faces whose read, typed and base corners lie in one line (no flatness test): U"]
    assert res.report_lines(limit=len(lines)) == lines
    assert res.report_lines(limit=len(lines) - 1) == lines[:-3] + ["face V warp (E K P Q): 1.34 mm off one plane; "
                                                                   "corner residuals chi2 0.0",
                                                                   "(left out for length: 2 face lines)"]
    assert res.report_lines(limit=len(lines) - 2) == lines[:-3] + ["(left out for length: 3 face lines)"]
    crease = {"pair": ["T", "U"], "name": "T|U", "deg": 12.0, "sd_deg": 1.2, "held": False, "range_deg": None,
              "shape": "ridge", "state": "certain", "certain": True, "normal_deg": 12.0, "bound": [], "declared": None,
              "dchi2": None, "free_deg": 12.0, "free_sd_deg": 1.2}
    one = stored_fit({}, S2_SETTLED, faces={"T": faces["T"]}, creases=[crease])
    full = one.report_lines(limit=40)
    assert full[-1] == "T|U ridge +12.0 +- 1.2 deg, certain" and len(full) == 5
    assert one.report_lines(limit=4) == full[:3] + ["(left out for length: 1 face line, 1 crease line)"]
    tight = stored_fit({"02": 0.4}, dict(S2_SETTLED, weights_settled=False, weight_change=0.2, weight_passes=30),
                       faces={"T": faces["T"]})
    full = tight.report_lines(limit=40)
    assert len(full) == 6 and tight.report_lines(limit=3) == full[:2] + ["(left out for length: 3 fit lines, 1 face "
                                                                         "line)"]


# --------------------------------------------------------------------------------------------- v3p3-fix-core
def test_chi2_tail_is_the_chi_square_upper_tail():
    """fit.chi2_tail (the misfit and position-warning probabilities): the closed forms for 1, 2 and 3 dof, below and
    above the series / continued-fraction switch (x / 2 = k / 2 + 1), and the two-sided normal tails at 1 and 2 sd."""
    for x in (1e-6, 0.01, 0.5, 1.0, 2.3, 3.0, 4.0, 5.0, 9.26, 30.0, 80.0):
        q1 = math.erfc(math.sqrt(x / 2.0))
        assert FT.chi2_tail(x, 1) == pytest.approx(q1, rel=1e-10, abs=1e-300), x
        assert FT.chi2_tail(x, 2) == pytest.approx(math.exp(-x / 2.0), rel=1e-10, abs=1e-300), x
        q3 = q1 + math.sqrt(2.0 * x / math.pi) * math.exp(-x / 2.0)
        assert FT.chi2_tail(x, 3) == pytest.approx(q3, rel=1e-10, abs=1e-300), x
    assert FT.chi2_tail(0.0, 3) == 1.0
    assert FT.chi2_tail(1.0, 1) == pytest.approx(0.3173105, abs=1e-7)
    assert FT.chi2_tail(4.0, 1) == pytest.approx(0.0455003, abs=1e-7)


def test_a_corner_the_fit_slides_is_reported_by_its_whole_residual():
    """v3p3 P1 (review-astra P1-1, review-owner P1-1; verify-1, verify-6): on probe1's flat crown (session2 r0004,
    every corner read) the fit slides the tip E 3.04 mm along its reading (2.82 sd of its position rows) and its X row
    is 1.14 sd: chi2 9.26 of the fit's 10.48. The misfit line, the face line and W_FACE_NOT_FLAT's flat fit said only
    "E +0.41 mm (1.1 sd)" ("off its reading"), and nothing said the top outline knot moved with E (0.42 mm higher at d
    70). Now: each says E's (d, y) part (mm along its free directions after the camera turns, and sd) and its X part;
    the moved line names the knot; W_FIT_OFF_READING (kind "fitted") says where the fit put E, how far from its reading
    and in sd, and the knot's move. pos_mm is checked against the reading's own (d, y) covariance and camera terms."""
    s = probe1_design("nodes")
    res = FT.fit_spec(s)
    E = res.nodes["E"]
    assert E["free_dims"] == 2 and E["knots"] == ["top"] and not E["at_cap"]
    assert E["pos_sd"] == pytest.approx(2.823, abs=2e-3) and E["residual_sd"] == pytest.approx(1.137, abs=2e-3)
    assert E["chi2"] == pytest.approx(E["pos_sd"] ** 2 + E["residual_sd"] ** 2, rel=1e-9)
    rec = s["nodes"]["E"][2]["read"]
    C = FT._full_cov(rec, float(rec["sd"]))[:2, :2]
    v = np.array([E["d"] - rec["d"], E["y"] - rec["y"]]) + sum(
        res.camera[f]["u"] * np.asarray(c[:2], float) for f, c in rec["camera"].items())
    assert E["pos_mm"] == pytest.approx(float(np.hypot(*v)), rel=1e-6)                 # fitted - read, turns in
    assert E["pos_sd"] == pytest.approx(math.sqrt(float(v @ np.linalg.solve(C, v))), rel=1e-6)
    assert FT.chi2_tail(E["chi2"], 3) == pytest.approx(0.026, abs=1e-3)
    lines = res.report_lines(limit=40)
    assert ("misfit beyond 1 sd (fitted - read, after the camera turns): E (d, y) 2.99 mm (2.8 sd) and X +0.41 mm (1.1 "
            "sd)") in lines
    face = [ln.strip() for k, ln in enumerate(lines) if ln.startswith("face crown")
            or (ln.startswith("  ") and lines[k - 1].startswith("face crown"))]
    assert " ".join(face) == ("face crown flat (A K E H): chi2 10.5 for 1 extra corner, not one plane; its largest "
                              "misfit (fitted - read): E (d, y) 2.99 mm (2.8 sd) and X +0.41 mm (1.1 sd)")
    assert not any("off its reading" in ln for ln in lines)
    assert any("E -2.90 +0.90 (2.8 sd, its top outline knot with it)" in ln for ln in lines)
    nf = next(w for w in res.warnings if w["code"] == "W_FACE_NOT_FLAT")
    assert ("the flat fit (fitted - read): E (d, y) 2.99 mm (2.8 sd) and X +0.41 mm (1.1 sd), K (d, y) 0.09 mm (0.6 "
            "sd) and X -0.13 mm (0.8 sd), A ") in nf["message"]
    assert nf["fit"][0]["node"] == "E" and nf["fit"][0]["chi2"] == pytest.approx(E["chi2"])
    (w,) = [w for w in res.warnings if w["code"] == "W_FIT_OFF_READING" and w["kind"] == "fitted"]
    assert w["node"] == "E" and w["knots"] == ["top"]
    assert w["message"] == ("node E at d 73.85, y 10.06: the fit placed it 3.03 mm from its reading at d 76.75 +- "
                            "1.23, y 9.16 +- 0.31 (2.8 sd along the directions it may move); the top outline knot it "
                            "lies on moved with it (-2.90 +0.90 mm in d y)")
    assert (E["d"], E["y"]) in {tuple(p) for p in res.spec["outline"]["top"]}   # the knot it names moved with E
    # the warp crown leaves E where it was read: no slide, no fitted-position warning
    warp = FT.fit_spec(probe1_design("warp"))
    assert warp.nodes["E"]["pos_sd"] < 0.01
    assert not any(w["code"] == "W_FIT_OFF_READING" and w["kind"] == "fitted" for w in warp.warnings)


def test_a_misfit_is_selected_and_ranked_by_its_whole_residual():
    """v3p3 P1 (verify-6's latent gap): a corner whose X row is under 1 sd dropped out of the misfit line whatever its
    position rows said (X 0.84 sd with position 2.82 sd printed nothing). A reading is a misfit when its X row or its
    whole residual lies beyond 1 sd's probability for its rows, and the list is ranked by that probability: E's three
    rows at chi2 8.66 (p 0.034) before a pinned K's X at 1.5 sd (p 0.134), whose X is the larger. A stored record
    without pos_mm (session2's) prints its position part in sd only; a position part that prints no digit is left
    out."""
    nodes = {"E": ([-2.9, 0.9], 2.82, 0.30, 0.84), "K": ([0.0, 0.0], 0.0, -0.20, -1.5)}
    res = stored_fit({}, S2_SETTLED, nodes=nodes)
    res.nodes["K"]["free_dims"] = 0
    assert res.report_lines(limit=40)[-1] == ("misfit beyond 1 sd (fitted - read): E (d, y) 2.8 sd and X +0.30 mm (0.8 "
                                              "sd), K X -0.20 mm (1.5 sd)")
    res.nodes["E"].update(pos_mm=3.04, free_dims=2)
    assert res.report_lines(limit=40)[-1].startswith("misfit beyond 1 sd (fitted - read): E (d, y) 3.04 mm (2.8 sd) "
                                                     "and X +0.30 mm (0.8 sd), K X")
    res.nodes["E"].update(pos_sd=0.5, chi2=0.5 ** 2 + 0.84 ** 2)          # 0.96 for 3 rows: within 1 sd
    assert res.report_lines(limit=40)[-1] == "misfit beyond 1 sd (fitted - read): K X -0.20 mm (1.5 sd)"
    res.nodes["E"].update(pos_sd=0.04, pos_mm=0.004, residual_sd=1.2, residual_mm=0.43, chi2=0.04 ** 2 + 1.2 ** 2)
    assert "E X +0.43 mm (1.2 sd)" in res.report_lines(limit=40)[-1]


def test_one_extra_corner_says_its_one_sigma_for_every_corner():
    """v3p3-verify-2: with one extra corner every corner's deviation from the plane of the other three has the one
    sigma sqrt(chi2), but the warning printed only mm, and the mm rank the corners by lever arm: a lift at K alone (the
    defect FINDINGS §0.1 puts there) prints A, 31 mm in front of K and H, the largest. The warning now says the sigma
    per corner and that the readings cannot single one out; the head no longer carries it."""
    s, _ = crown_design(2.0)
    w = next(x for x in FT.fit_spec(s).warnings if x["code"] == "W_FACE_NOT_FLAT")
    dev = {x["node"]: x for x in w["deviations"]}
    sig = math.sqrt(w["chi2"])
    assert all(x["sigma"] == pytest.approx(sig, rel=1e-9) for x in dev.values())
    assert abs(dev["A"]["mm"]) > abs(dev["K"]["mm"])                       # the lever arm, not the data
    assert w["message"].startswith(f"face crown: its corners do not fit one flat face (chi2 {w['chi2']:.1f} for 1 "
                                   f"extra corner): each corner {sig:.1f} sd from the plane of the other three (with "
                                   f"one extra corner the readings cannot say which corner is off): A ")


def warp_quad(dX: float) -> dict:
    """An A/A2-like warp W (A2 C K A): A2 0.67 mm from the hinge corner A, behind the hinge plane, dX mm proud of A
    (the split's logo: 0.64)."""
    rd_ = (lambda X: {"read": {"X": float(X), "sd": 0.1}})                 # noqa: E731
    nodes = {"A2": [-0.65, 5.18, rd_(64.0 + dX)], "C": [22.0, -8.0, rd_(65.5)], "K": [47.0, 4.5, rd_(65.2)],
             "A": [0.0, 5.0, rd_(64.0)]}
    return FX.straight(nodes=nodes, facets={"W": {"poly": ["A2", "C", "K", "A"], "plane": "warp"}})


def test_a_warp_reports_how_far_it_turns_and_warns_beyond_the_crowns_scale():
    """v3p3 P2 (review-owner P1-4, verify-9): a warp had no report of its turn; following the prompt, the A/A2 split's
    warped logo turned ~60 deg at the hinge corner and the fit said "0.33 mm off one plane". Each warp now gives its
    largest sampled turn from its area-mean normal and where; W_WARP_TWIST above WARP_TWIST_WARN_DEG. The crowns stay
    under it (probe1's warp crown 9.0 deg near K, run 7's 6.7). Checked independently: the mean normal from a 0.1 mm
    grid of central differences of planes.mvc, the normal at the reported point (an edge point 0.05 mm from A) by
    second-order one-sided differences from inside, and no interior point farther than 0.2 mm from a corner turning
    more than the fit says."""
    from glasses3d.g3d.temple import spec as S
    for design, deg in ((probe1_design("warp"), 8.96), (run7_layout("warp"), 6.69)):
        res = FT.fit_spec(design)
        f = res.faces["crown"]
        assert f["twist_deg"] == pytest.approx(deg, abs=0.05) and f["twist_near"] == "K"
        assert not any(w["code"] == "W_WARP_TWIST" for w in res.warnings)
        assert any(f"its largest sampled turn from its mean plane is {deg:.0f} deg" in ln
                   for ln in res.report_lines(limit=40))
    res = FT.fit_spec(warp_quad(0.64))
    f = res.faces["W"]
    (w,) = [w for w in res.warnings if w["code"] == "W_WARP_TWIST"]
    assert w["face"] == "W" and w["near"] == "A" and f["twist_near_mm"] == pytest.approx(FT.CORNER_NEAR_MM, abs=1e-6)
    assert w["message"] == (f"face W warp (A2 C K A): its largest sampled turn from its mean plane is "
                            f"{f['twist_deg']:.0f} deg, at d 0.05, y 5.00 (0.05 mm from corner A) (over 15 deg)")
    P = np.array([[-0.65, 5.18], [22.0, -8.0], [47.0, 4.5], [0.0, 5.0]])
    F = np.array([res.nodes[n]["X"] for n in ("A2", "C", "K", "A")])
    if (P[1, 0] - P[0, 0]) * (P[2, 1] - P[0, 1]) - (P[1, 1] - P[0, 1]) * (P[2, 0] - P[0, 0]) < 0:
        P, F = P[::-1].copy(), F[::-1].copy()
    g, h = 0.1, 1e-4
    Q = np.stack(np.meshgrid(np.arange(-0.65 + g / 2, 47.0, g), np.arange(-8.0 + g / 2, 5.18, g)), -1).reshape(-1, 2)
    Q = Q[points_in_poly(Q, P) & (Q[:, 0] >= 0.0)]                        # the outline starts at d 0
    gd = (mvc(P, F, Q[:, 0] + h, Q[:, 1]) - mvc(P, F, Q[:, 0] - h, Q[:, 1])) / (2 * h)
    gy = (mvc(P, F, Q[:, 0], Q[:, 1] + h) - mvc(P, F, Q[:, 0], Q[:, 1] - h)) / (2 * h)
    gm = np.array([gd.mean(), gy.mean()])
    nm = np.array([-gm[0], -gm[1], 1.0]) / math.sqrt(1.0 + gm @ gm)

    def turn_of(grad):
        nv = np.array([-grad[0], -grad[1], 1.0]) / math.sqrt(1.0 + grad @ grad)
        return math.degrees(math.acos(min(1.0, float(nv @ nm))))
    m = np.array(f["twist_at"])
    A, K = np.array([0.0, 5.0]), np.array([47.0, 4.5])
    u = (K - A) / np.hypot(*(K - A))
    n_in = np.array([-u[1], u[0]])
    if not points_in_poly((m + 0.01 * n_in)[None], P)[0]:
        n_in = -n_in
    t = 1e-3
    X0 = float(mvc(P, F, m[0], m[1]))
    cross = (4.0 * float(mvc(P, F, *(m + t * n_in))) - float(mvc(P, F, *(m + 2 * t * n_in))) - 3.0 * X0) / (2 * t)
    along = (float(res.nodes["K"]["X"]) - float(res.nodes["A"]["X"])) / float(np.hypot(*(K - A)))
    grad = np.linalg.solve(np.array([u, n_in]), np.array([along, cross]))
    assert f["twist_deg"] == pytest.approx(turn_of(grad), abs=0.5)
    far = np.min(np.hypot(Q[:, None, 0] - P[None, :, 0], Q[:, None, 1] - P[None, :, 1]), axis=1) > 0.2
    assert max(turn_of(np.array([a, b])) for a, b in zip(gd[far], gy[far])) <= f["twist_deg"] + 0.5
    assert FT.fit_spec(warp_quad(0.0)).faces["W"]["twist_deg"] < FT.WARP_TWIST_WARN_DEG
    assert "W_WARP_TWIST" in S.WARNINGS and "W_WARP_TWIST" in FT.FIT_WARNINGS


def test_a_valued_node_no_facet_lists_is_refused_inside_a_facet():
    """v3p3 P2 (review-owner P2-4, verify-13): L dropped from nose [A, L0, C] with L kept (read, on the bottom outline
    knot inside nose's polygon): the fit left L's reading out and accepted, then the builder refused E_RELIEF_STEP at
    nose's fitted plane to 6 digits; a typed node strictly inside a facet was dropped by both, silently. A read, typed
    or base node no facet lists, inside the outline and inside or on a facet's polygon, is refused at the fit, in
    words, with every way out; one on the plain arm (in no facet's polygon) or outside the outline keeps its value as
    its t (every package's revision 0 has no facets)."""
    for node, what, where in (([10.0, 5.0, read(63.5, 0.2)], "a reading", "on the edge of facets N, W (d 10.00, y "
                                                                          "5.00)"),
                              ([85.0, 2.0, 4.0], "a typed t", "inside facet R (d 85.00, y 2.00)"),
                              ([85.0, 2.0, "base"], "t base", "inside facet R (d 85.00, y 2.00)")):
        d = layout1()
        d["nodes"]["Z"] = node
        with pytest.raises(TempleError) as e:
            FT.fit_spec(d)
        assert e.value.code == "E_SPEC" and e.value.fields["node"] == "Z"
        assert e.value.message == (f"node Z has {what} and lies {where}, but no facet lists it among its corners: the "
                                   f"fit takes a node's value only at a facet's corner; ways: list Z among a facet's "
                                   f"corners, give it no value (t solve), or delete node Z")
    for p in ([110.0, 5.0], [10.0, 15.0]):                                  # the plain arm; outside the outline
        d = layout1()
        d["nodes"]["Z"] = [*p, read(63.5, 0.2)]
        assert FT.fit_spec(d).spec["nodes"]["Z"][2] == pytest.approx(3.5, abs=1e-12)
    d = layout1()
    d["nodes"]["Z"] = [10.0, 5.0, None]                                     # no value: nothing to bind
    assert "Z" not in FT.fit_spec(d).nodes


def test_the_builder_names_a_stray_known_value_without_the_plane_equation():
    """v3p3-verify-13: the builder's refusal printed "off its plane X = 69.5513 + -0.00772868 d + -0.0826772 y", the
    fit's own plane to 6 digits (no tool sets it), and never said L is no corner of nose. Now: the facts in the units
    the tools set, and that the node is not the facet's corner; the coefficients stay in the fields. T's corners P, Q
    lie below the outline, so the bottom outline knot at d 40, where the typed node L sits 0.5 mm off T's plane, is a
    vertex of T's built region."""
    from glasses3d.g3d.temple import build_temple
    s = FX.straight(nodes={"P": [20.0, -12.0, 4.0], "Q": [60.0, -12.0, 4.0], "R": [40.0, 6.0, 4.0],
                           "L": [40.0, -10.0, 4.5]},
                    facets={"T": {"poly": ["P", "Q", "R"], "plane": {"coef": [64.0, 0.0, 0.0]}}})
    s["outline"]["bottom"] = [[0.0, -10.0], [40.0, -10.0], [120.0, -10.0]]
    with pytest.raises(TempleError) as e:
        build_temple(s)
    x = e.value
    assert x.code == "E_RELIEF_STEP" and "X = " not in x.message and "+ -" not in x.message
    assert x.message == ("facet T: 1 known value(s) off its plane (worst +0.5 mm at node L, which is not a corner of "
                         "T, (d, y) (40.00, -10.00))")
    assert x.fields["coef"] == pytest.approx([64.0, 0.0, 0.0])


def test_crosses_sees_a_touch_on_a_sloped_outline_edge():
    """v3p3-review-e2e P3-3 (probe 4 seed 1874): builder_point reverts a corner whose move touches an outline edge, but
    the orientation signs saw a point put onto a sloped edge by interpolation on either side at random (153 of 200
    such points on a Prada-like first top edge): the readings-only fit snapped A onto the top outline, the crown's edge
    A-H then lay outside it, and the declared crown|base valley "held" on a piece the builder did not build
    (E_CREASE_SHAPE after the fit accepted). Touching is now a distance: every such point reverts."""
    top = [[0.0, 10.13], [36.24, 15.16]] + [p for p in FX.TOP if p[0] >= 50.0]
    rd_ = {"read": {"X": 65.0, "sd": 0.1, "d": 5.0, "y": 10.0, "sd_d": 0.2, "sd_y": 0.2}}
    s = FX.base_spec(nodes={"A": [5.0, 10.0, rd_], "K": [30.0, 0.0, read(65.0, 0.1)],
                            "P": [3.0, -5.0, read(65.0, 0.1)]},
                     facets={"F": ["A", "P", "K"]}, outline={"top": top, "bottom": FX.BOTTOM, "tip_round_mm": 0.0})
    pb = FT._Problem(s)
    qs = [(d, float(pb.ps.y_top(d))) for d in np.linspace(1.0, 20.0, 200)]
    signs = [bool(np.any(FT._meets(np.array([pb.nodes["A"]]), np.array([q]), None, *pb._others("A")[1:]))) for q in qs]
    assert 0 < sum(signs) < len(qs)                                         # the orientation signs alone: at random
    assert all(pb._crosses("A", q) for q in qs)


def lost_pair_design() -> dict:
    """crown [A K E H] on the straight slab with H 0.3 mm above the top outline and A 0.15 mm below it, read 0.02 mm
    below it: crown|base shares only the inside part of A-H (11.6 mm); logo [A P K] and sh [K Q E] on the plain arm."""
    rd_ = (lambda X, **p: {"read": {"X": float(X), "sd": 0.1, **p}})       # noqa: E731
    cr = (lambda d, y: 65.5 - 0.08 * y)                                     # noqa: E731
    nodes = {"A": [5.2, 9.85, rd_(cr(5.2, 9.98), d=5.2, y=9.98, sd_d=0.2, sd_y=0.05)], "K": [30.0, 0.0, rd_(cr(30, 0))],
             "E": [60.0, 10.0, rd_(cr(60, 10))], "H": [40.0, 10.3, rd_(cr(40, 10.3))], "P": [3.0, -5.0, rd_(65.0)],
             "Q": [60.0, -5.0, rd_(65.0)]}
    return FX.straight(nodes=nodes, facets={"crown": ["A", "K", "E", "H"], "logo": ["A", "P", "K"],
                                            "sh": ["K", "Q", "E"]},
                       creases=[{"facets": ["crown", "base"], "shape": "valley"}])


def test_a_declared_pair_the_fitted_corners_no_longer_share_is_refused(monkeypatch):
    """v3p3-review-e2e P3-3: the crease pieces are fixed at the design, so a corner the fit moves could take a
    declared pair's only shared edge outside the outline unseen; the builder then refused E_CREASE_SHAPE "declared
    valley but built none". The fitted spec is read again and such a pair is refused E_FIT_SHAPE with the fact and the
    moves. Here the fit keeps A at its design (its snap onto the outline touches the edge: reverted), the pair holds
    and builds; with the touch test disabled the fit puts A onto the outline, and the check refuses."""
    from glasses3d.g3d.temple import build_temple
    s = lost_pair_design()
    res = FT.fit_spec(s)
    assert res.model["snap_reverted"] == ["A"] and math.hypot(*res.nodes["A"]["move_mm"]) < 1e-9
    assert any(c["regions"] == ["crown", "base"] and c["shape"] == "valley"
               for c in build_temple(res.spec).tables["creases"] if c["kind"] == "crease")
    monkeypatch.setattr(FT._Problem, "_crosses", lambda self, n, q: False)
    with pytest.raises(TempleError) as e:
        FT.fit_spec(s)
    x = e.value
    assert x.code == "E_FIT_SHAPE" and x.fields["creases"][0]["reason"] == "no shared edge after the fit"
    assert x.message == ("the declared crease shapes: crown|base declared valley: crown and base share no edge inside "
                         "the outline where the fit puts the corners (11.60 mm at the design's); the fit moved (d y "
                         "mm) A +0.00 +0.15")
