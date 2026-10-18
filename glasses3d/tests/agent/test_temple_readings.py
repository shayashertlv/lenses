"""Readings (agent/temple_lab/readings.py, plan/WP6_V3.md §4): a named chart point read from pixel picks (a corner) or
from two traced edges per photo (a crossing) in 2..4 photos, with its pick and camera-theta error, the slide line a
two-direction reading cannot check, and the warnings. No Blender, no network, no model beyond x_in.

Synthetic rig (``SynthBasis``): perspective cameras around a Prada-sized temple pair, 640 x 480 photos; points are
projected with the lab's own model (pose.chart_to_posed + camera.project_mm), never with readings' matrices.
Real cameras (skipped when data/wp6v2/basis is absent): the WP6 investigation's crown picks
(data/wp6v2/review_20261012/w2/crown-geometry/picks.json, copied below and checked against the file when present) read
K's X ~70.07 (bundle 70.07 +- 0.13, 4-photo joint 70.08) and the crown tip E at d 75.5-77.2 from B1 + B2 crossings.
The review of 2026-10-12 (v3/v3-readings-fix.md) added: the mirrored-chart ghost bound, W_RESIDUAL in the reading's own
covariance, the slide line for both kinds, the per-edge offset term, the twin camera map, camera_shift / cov_pick and
refresh; each has a test below that fails on the module before it.
"""
import itertools
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from glasses3d.agent.temple_lab import geometry as G
from glasses3d.agent.temple_lab import readings as RD
from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d_host import compare as GC
from glasses3d.g3d_host import pose as PO
from glasses3d.g3d_host.camera import Camera, NormFrame, project_mm

G3D = Path(__file__).resolve().parents[2]
BASIS_JOB = G3D / "data" / "wp6v2" / "basis" / "check_job"
PICKS_JSON = G3D / "data" / "wp6v2" / "review_20261012" / "w2" / "crown-geometry" / "picks.json"
AX = [64.6, -10.1]


def _pose(a: float, b: float) -> dict:
    return {"+1": {"theta_deg": a, "axis_xz_mm": AX}, "-1": {"theta_deg": b, "axis_xz_mm": [-AX[0], AX[1]]}}


class SynthBasis:
    """05 / 01 square-on side views of the charted / mirrored temple, 02 an angled view (yaw 30, theta 6 / 2), 03 a top
    view, 04 a front view, 06 a side view 0.4 deg from 05 (one direction seen twice), black-side = 05's camera moved by
    an image similarity (scale 1.1, shift: the same lines of sight) whose record names its source as the Prada basis
    does, black-angled = 02's (scale 1.05, its record names the transport's contour residual 0.82 px), black-front's
    source given only by a transport block."""
    frame = NormFrame((0.0, 0.0, -40.0), 200.0)
    CAMS = {"reference-05": Camera(90.0, 12.0, 1.0, 0.25, 600.0, 100.0, 240.0),
            "reference-01": Camera(-90.0, 12.0, -1.0, 0.25, 600.0, 540.0, 240.0),
            "reference-02": Camera(30.0, 12.0, 0.6, 0.26, 600.0, 160.0, 240.0),
            "reference-03": Camera(0.0, 60.0, 0.0, 0.29, 500.0, 320.0, 400.0),
            "reference-04": Camera(0.0, 10.0, 0.0, 0.08, 500.0, 320.0, 200.0),
            "reference-06": Camera(90.4, 12.2, 1.0, 0.25, 600.0, 100.0, 240.0),
            "black-side": Camera(90.0, 12.0, 1.0, 0.25, 660.0, 80.0, 245.0),
            "black-angled": Camera(30.0, 12.0, 0.6, 0.26, 630.0, 150.0, 236.0),
            "black-front": Camera(0.0, 10.0, 0.0, 0.08, 520.0, 320.0, 205.0)}
    POSES = {"reference-02": _pose(6.0, 2.0), "black-angled": _pose(6.0, 2.0), "reference-03": _pose(-2.0, -1.9),
             "reference-04": _pose(0.05, -0.03), "black-front": _pose(0.05, -0.03)}

    def __init__(self):
        self.refs = {r: {"fit_status": "fitted (all_views_validation: iou 0.9, contour 6.0 px)",
                         "theta_observed": {"+1": True, "-1": True},
                         "pose_provenance": {"opening_calibrated": True}}
                     for r in self.CAMS}
        self.refs["black-side"]["fit_status"] = "camera transported from 05"
        self.refs["black-angled"]["fit_status"] = ("camera transported from 02 by image similarity (contour 0.82 px, "
                                                   "IoU 0.992); theta copied from 02")
        self.refs["black-front"] = {"transport": {"source": "reference-04.png"}}
        self.POSES = dict(self.POSES)

    def camera(self, rid):
        return self.CAMS[rid]

    def shape(self, rid):
        return 480, 640

    def pose(self, rid):
        return self.POSES.get(rid, _pose(0.0, 0.0))

    def photo(self, rid):
        y, x = np.mgrid[0:480, 0:640]
        return np.stack([(x * 3) % 256, (y * 2) % 256, np.full_like(x, 90)], -1).astype(np.uint8)


TWINS = {"05": "reference-05", "02": "reference-02", "03": "reference-03", "04": "reference-04", "B1": "black-side",
         "B2": "black-angled", "B3": "black-front"}


@pytest.fixture(scope="module")
def tb():
    return build_temple(FX.prada_default())


@pytest.fixture()
def bs():
    return SynthBasis()


def _rid(pid: str) -> str:
    return TWINS.get(pid, pid if pid.startswith("black-") else f"reference-{pid}")


def px(bs, pid: str, p, side: int = 1) -> list:
    """The lab's projection of chart point p on the side temple in photo pid (opening 0, the photo's pose)."""
    rid = _rid(pid)
    return project_mm(PO.chart_to_posed([p], side, -10.6, bs.pose(rid)), bs.camera(rid), bs.frame)[0].tolist()


def point(tb, d=40.0, y=2.0, t=6.0) -> np.ndarray:
    return np.array([d, y, float(tb.spec.inner(d)) + t])


def corner_picks(bs, p, photos, sides=None) -> list:
    sides = sides or {}
    return [{"photo": c, "uv": px(bs, c, p, sides.get(c, 1)), "edges": None, "sd_px": None} for c in photos]


def edge_picks(bs, p, photos, a, b, sa=(-14.0, -9.0, -4.0), sb=(3.0, 7.0, 11.0)) -> list:
    """Per photo two traced edges: points of the 3D lines p + s a and p + s b (chart mm), the crossing itself never
    traced (it is extrapolated, as a tip is)."""
    a, b = np.asarray(a, float) / np.linalg.norm(a), np.asarray(b, float) / np.linalg.norm(b)
    return [{"photo": c, "uv": None, "sd_px": None,
             "edges": [[px(bs, c, p + s * a) for s in sa], [px(bs, c, p + s * b) for s in sb]]} for c in photos]


def line_of_sight(bs, pid: str, p, side: int = 1) -> np.ndarray:
    """Photo pid's line of sight through chart point p, in chart directions (pose.toward_camera, independent of
    readings): the epipolar plane of two photos is spanned by theirs."""
    rid = _rid(pid)
    w = PO.toward_camera(PO.chart_to_posed([p], side, -10.6, bs.pose(rid)), bs.camera(rid), bs.frame)[0]
    O = PO.chart_to_posed(np.vstack([np.zeros(3), np.eye(3)]), side, -10.6, bs.pose(rid))
    v = np.linalg.solve((O[1:] - O[0]).T, w)
    return v / np.linalg.norm(v)


# a tip: two edges 9 deg apart in 3D, traced short of their crossing with 4 points over 24 mm each (~70 px)
TIP = ((1.0, 0.08, 0.0), (1.0, -0.08, 0.0), (-30.0, -22.0, -14.0, -6.0), (4.0, 12.0, 20.0, 28.0))


def codes(rec) -> list:
    return [w["code"] for w in rec["warnings"]]


def model_noise(bs, picks, refs, rng, edge_sys=True) -> list:
    """The picks with the error model readings states (module doc), drawn independently of readings: per photo a
    camera error (1 px per fitted camera; a twin's = its source's scaled by the similarity's scale (the synthetic twins
    share their source's roll) plus its transport error), per traced point 1 px, per traced edge an offset of
    EDGE_SYS_PX along its normal; a corner pick 1 px."""
    fit_err, out = {}, []
    for pk in picks:
        rid = refs.get(pk["photo"], pk["photo"])
        src = RD.camera_source(bs, rid)
        e = fit_err.setdefault(src, rng.normal(0, RD.CAMERA_PX_SD, 2))
        if src != rid:
            e = bs.camera(rid).scale / bs.camera(src).scale * e + rng.normal(0, RD.transport_px(bs, rid), 2)
        if pk.get("edges") is None:
            out.append({**pk, "uv": (np.asarray(pk["uv"]) + e + rng.normal(0, 1.0, 2)).tolist()})
            continue
        edges = []
        for edge in pk["edges"]:
            Q = np.asarray(edge, float)
            t = (Q[-1] - Q[0]) / np.linalg.norm(Q[-1] - Q[0])
            off = rng.normal(0, RD.EDGE_SYS_PX) * np.array([-t[1], t[0]]) if edge_sys else 0.0
            edges.append((Q + e + off + rng.normal(0, 1.0, Q.shape)).tolist())
        out.append({**pk, "edges": edges})
    return out


# --------------------------------------------------------------------------------------------- the projection
def test_camera_matrix_is_the_labs_projection(bs):
    """camera_matrix (one projective matrix per photo and temple) equals pose.chart_to_posed + camera.project_mm on
    random chart points, every camera, both temples, poses with theta and a hinge opening; its third row is
    project's depth (a point behind the near plane: depth <= NEAR there, and project_mm raises)."""
    rng = np.random.default_rng(7)
    pts = np.column_stack([rng.uniform(-5, 130, 50), rng.uniform(-20, 20, 50), rng.uniform(55, 75, 50)])
    for rid, cam in bs.CAMS.items():
        for side in (1, -1):
            for opening in (None, (3.0, (64.6, -10.1))):
                H = RD.camera_matrix(cam, bs.frame, side, -10.6, bs.pose(rid), opening)
                ref = project_mm(PO.chart_to_posed(pts, side, -10.6, bs.pose(rid), opening), cam, bs.frame)
                h = np.column_stack([pts, np.ones(len(pts))]) @ H.T
                assert np.max(np.abs(h[:, :2] / h[:, 2:] - ref)) < 1e-9, (rid, side, opening)
    cam = Camera(90.0, 0.0, 0.0, 0.9, 600.0, 100.0, 240.0)               # a strong perspective: the camera at x 222
    far = np.array([[40.0, 0.0, 260.0]])                                  # beyond it
    H = RD.camera_matrix(cam, bs.frame, 1, -10.6)
    assert (np.append(far[0], 1.0) @ H[2]) <= RD.NEAR
    with pytest.raises(ValueError, match="near plane"):
        project_mm(PO.chart_to_posed(far, 1, -10.6), cam, bs.frame)


# --------------------------------------------------------------------------------------------- read back exactly
@pytest.mark.parametrize("photos,sides", [(["05", "02"], {}), (["05", "02", "03"], {}),
                                          (["05", "02", "03", "04"], {}), (["02", "01"], {"01": -1})])
def test_a_projected_point_is_read_back_exactly(tb, bs, photos, sides):
    """A chart point projected into 2..4 photos (01 sees the mirrored temple: the same chart point on the -X temple)
    is read back exactly from its pixels with zero residuals, with no start from the model (a linear triangulation per
    temple combination); t = X - x_in(d); the record keeps the photos, their basis references, camera fits, the
    temples and the pixel used; sd parts add in quadrature; cov_pick's diagonal is the pick part."""
    P = point(tb)
    rec = RD.read_point(tb, bs, "K", corner_picks(bs, P, photos, sides), kind="corner")
    assert (rec["d"], rec["y"], rec["X"]) == (pytest.approx(P[0], abs=1e-3), pytest.approx(P[1], abs=1e-3),
                                              pytest.approx(P[2], abs=1e-3))
    assert rec["t"] == pytest.approx(6.0, abs=1e-3) and max(rec["res_px"]) < 1e-3
    assert rec["chi2"] < 1e-6 and max(rec["res_sd"]) < 1e-3 and rec["dof"] == 2 * len(photos) - 3
    assert rec["photos"] == photos and rec["rids"] == [_rid(p) for p in photos] and rec["fits"] == photos
    assert rec["sides"] == [sides.get(p, 1) for p in photos]
    assert rec["uv"] == [pytest.approx(px(bs, p, P, sides.get(p, 1)), abs=1e-3) for p in photos]
    assert rec["kind"] == "corner" and rec["crossing_deg"] is None and rec["edge_epipolar_deg"] is None
    assert rec["warnings"] == [] and 1.0 < rec["cond"] < RD.COND_MAX
    for k in ("X", "d", "y"):
        assert rec[f"sd_{k}"] == pytest.approx(math.hypot(rec[f"sd_{k}_pick"], rec[f"sd_{k}_camera"]), abs=2e-3)
        assert rec[f"sd_{k}_pick"] > 0
    assert np.sqrt(np.diag(rec["cov_pick"])) == pytest.approx([rec["sd_d_pick"], rec["sd_y_pick"], rec["sd_X_pick"]],
                                                              abs=2e-3)
    assert rec["sd_X_camera"] > 0 and rec["sd_d_camera"] > 0
    assert set(rec["camera_shift"]) == set(photos)
    assert rec["sd_X_camera"] == pytest.approx(math.sqrt(sum(v[2] ** 2 for v in rec["camera_shift"].values())),
                                               abs=2e-3)
    Xm, _r = G.surface_at(tb, np.array([[rec["d"], rec["y"]]]))
    assert rec["model_t"] == pytest.approx(float(Xm[0]) - float(tb.spec.inner(rec["d"])), abs=2e-3)
    assert rec["picks"][0] == {"photo": photos[0], "uv": RD._r(px(bs, photos[0], P, sides.get(photos[0], 1)), 2),
                               "edges": None, "sd_px": 1.0}


@pytest.mark.parametrize("photos", [["05", "02"], ["05", "02", "03"], ["02", "03", "04", "05"]])
def test_a_crossing_of_two_projected_lines_is_read_back_exactly(tb, bs, photos):
    """Two 3D lines through a point, traced short of their crossing (3 points each, then 2): each photo's crossing
    pixel is the point's projection, and the point is read back exactly; the record carries the crossing angles, the
    edges' smallest epipolar angles and the crossing's anisotropic pick covariance."""
    P = point(tb)
    for sa, sb in (((-14.0, -9.0, -4.0), (3.0, 7.0, 11.0)), ((-14.0, -4.0), (3.0, 11.0))):
        rec = RD.read_point(tb, bs, "E", edge_picks(bs, P, photos, (1.0, 0.3, -0.05), (0.2, -1.0, 0.1), sa, sb),
                            kind="crossing")
        assert (rec["d"], rec["y"], rec["X"]) == (pytest.approx(P[0], abs=1e-3), pytest.approx(P[1], abs=1e-3),
                                                  pytest.approx(P[2], abs=1e-3))
        assert rec["uv"] == [pytest.approx(px(bs, p, P), abs=1e-3) for p in photos] and max(rec["res_px"]) < 1e-3
        assert rec["kind"] == "crossing" and len(rec["crossing_deg"]) == len(photos)
        assert all(len(e) == 2 and min(e) >= 0 for e in rec["edge_epipolar_deg"])
        assert json.loads(json.dumps(rec)) == rec                       # the lab stores the record as is
        for cov in rec["uv_cov"]:
            assert np.linalg.eigvalsh(np.asarray(cov))[0] > 0


# --------------------------------------------------------------------------------------------- the v2 reference
# v2's lab.corner_reading (photo_compare depths with ``also``), moved here from agent/temple_lab/lab.py at v3-final,
# where nothing reached it any more: an independent least squares (scipy, started from the model's surface, every
# temple combination tried) that readings.read_point (a linear triangulation, then its own weighted fit) must agree
# with. Unchanged but for its names.
REF_COND_MAX = 20.0          # v2's lab.CORNER_COND_MAX: readings.COND_MAX must equal it


def _ref_inner_x(tb, d: float) -> float:
    return float(np.asarray(tb.spec.inner(d), float))


def _ref_opening(spec_hinge) -> tuple | None:
    deg = float(spec_hinge.get("opening_deg") or 0.0)
    if not deg:
        return None
    ax = spec_hinge["axis_xz_mm"]
    return deg, (abs(float(ax[0])), float(ax[1]))


def ref_photo_sides(tb, camera, frame, pose, d: float, y: float, X: float, opening) -> list:
    """The temple(s) to try for a photo: the one whose outer face the camera sees at (d, y, X), else both."""
    h = tb.spec.hinge
    seen = []
    for side in (1, -1):
        P = PO.chart_to_posed([[d, y, X], [d, y, X + 1.0]], side, float(h["z_mm"]), pose, opening)
        seen.append(bool(PO.faces_camera(P[:1], P[1] - P[0], camera, frame)[0]))
    return [1] if seen == [True, False] else ([-1] if seen == [False, True] else [1, -1])


def corner_reading(tb, views: list, d0: float, y0: float) -> dict:
    """A point read from 2..4 photos, views = [(camera, frame, pose, (u, v))], by least squares from (d0, y0) at the
    model's t there: {d_mm, y_mm, X_mm, t_mm, res_px, sd_t_mm, t_at_mm, sd_t_at_mm, model_t_mm, sides}. Raises
    ValueError when no point fits the pixels or the views see it along nearly one line (condition number over
    REF_COND_MAX)."""
    from scipy.optimize import least_squares
    h = tb.spec.hinge
    opening = _ref_opening(h)
    X0, _reg = G.surface_at(tb, np.array([[d0, y0]], float))
    x0 = float(X0[0]) if np.isfinite(X0[0]) else _ref_inner_x(tb, d0) + float(tb.spec.base_t(d0, y0))
    start = np.array([d0, y0, x0], float)
    targets = [np.asarray(v[3], float).reshape(2) for v in views]
    sides = [ref_photo_sides(tb, cam, fr, pose, d0, y0, x0, opening) for cam, fr, pose, _uv in views]

    def residual(p, combo):
        out = []
        for (cam, fr, pose, _uv), side, tgt in zip(views, combo, targets):
            P = PO.chart_to_posed([p], side, float(h["z_mm"]), pose, opening)
            out.append(project_mm(P, cam, fr)[0] - tgt)
        return np.concatenate(out)
    best = None
    for combo in itertools.product(*sides):
        try:
            r = least_squares(residual, start, args=(combo,), x_scale=[10.0, 10.0, 3.0], method="lm")
        except ValueError:
            continue
        if np.all(np.isfinite(r.fun)) and (best is None or r.cost < best[0].cost):
            best = (r, combo)
    if best is None:
        raise ValueError("no point fits these pixels: pick the same corner in each photo")
    r, combo = best
    J = np.asarray(r.jac, float)
    d, y, X = (float(v) for v in r.x)
    g = np.array([-(_ref_inner_x(tb, d + 0.5) - _ref_inner_x(tb, d - 0.5)), 0.0, 1.0])
    try:
        cov = np.linalg.inv(J.T @ J)
        sd = float(np.sqrt(max(g @ cov @ g, 0.0)))
        sd_at = float(np.sqrt(max(cov[2, 2], 0.0)))
    except np.linalg.LinAlgError:
        sd = sd_at = float("inf")
    sv = np.linalg.svd(J, compute_uv=False)
    cond = float(sv[0] / sv[-1]) if sv[-1] > 0 else float("inf")
    if not np.isfinite(cond) or cond > REF_COND_MAX:
        raise ValueError(f"these photos see the point along nearly one line (t would move {max(sd, sd_at):.1f} mm "
                         f"per px of pick error): pick it in a photo from another direction")
    Xm, _reg = G.surface_at(tb, np.array([[d, y]], float))
    res = np.asarray(r.fun, float).reshape(-1, 2)
    return {"d_mm": d, "y_mm": y, "X_mm": X, "t_mm": X - _ref_inner_x(tb, d), "t_at_mm": X - _ref_inner_x(tb, d0),
            "res_px": [float(np.hypot(*e)) for e in res], "sd_t_mm": sd, "sd_t_at_mm": sd_at,
            "model_t_mm": float(Xm[0]) - _ref_inner_x(tb, d) if np.isfinite(Xm[0]) else None, "sides": list(combo)}


def test_matches_lab_corner_reading(tb, bs):
    """With equal pick sd and independent cameras the weighted fit is v2's least squares (corner_reading above, v2's
    lab.corner_reading, which starts from the model): the same point from 2 and 3 photos, read with noisy pixels.
    Pins the constants that must equal the lab's or v2's: COND_MAX, fixed, the temple choice and the node-name rule,
    and the engineering bounds the ghost bound uses."""
    pytest.importorskip("agents")
    from glasses3d.agent.temple_lab import lab as LB
    assert RD.COND_MAX == REF_COND_MAX == 20.0 and not hasattr(LB, "corner_reading")
    assert (G.D_LIMIT_MM, G.Y_LIMIT_MM, G.T_LIMIT_MM) == (400.0, 200.0, 40.0)
    for x in (8.365, 8.36499999, -0.004, 0.0, 70.071, -1.25):
        for nd in (1, 2):
            assert RD.fixed(x, nd) == LB.fixed(x, nd)
    assert RD.NAME_MAX == int(re.search(r"\{0,(\d+)\}", G.NAME_RE.pattern).group(1)) + 1
    rng = np.random.default_rng(3)
    P = point(tb, 63.0, -4.0, 5.0)
    for photos in (["05", "02"], ["05", "02", "03"]):
        picks = corner_picks(bs, P, photos)
        for pk in picks:
            pk["uv"] = (np.asarray(pk["uv"]) + rng.normal(0, 1.5, 2)).tolist()
        rec = RD.read_point(tb, bs, "Q", picks, kind="corner")
        views = [(bs.camera(_rid(pk["photo"])), bs.frame, bs.pose(_rid(pk["photo"])), pk["uv"]) for pk in picks]
        lab = corner_reading(tb, views, 60.0, -3.0)
        assert (rec["d"], rec["y"], rec["X"]) == (pytest.approx(lab["d_mm"], abs=2e-3),
                                                  pytest.approx(lab["y_mm"], abs=2e-3),
                                                  pytest.approx(lab["X_mm"], abs=2e-3))
        assert rec["res_px"] == pytest.approx(lab["res_px"], abs=2e-3) and rec["sides"] == lab["sides"]
        for c, (cam, fr, pose, _uv) in zip(rec["photos"], views):
            assert RD._facing(cam, fr, pose, [rec["d"], rec["y"], rec["X"]], -10.6, None) == \
                ref_photo_sides(tb, cam, fr, pose, rec["d"], rec["y"], rec["X"], None)


# --------------------------------------------------------------------------------------------- the ghost
def _ghosts(tb, b, refs, sets, ds, ys, noises, seeds, hz=-10.6):
    """(readings, ghosts (X > 20 mm off the truth), refused) over every set, point and noise draw."""
    out = [0, 0, 0]
    for photos, d, y, noise, seed in itertools.product(sets, ds, ys, noises, seeds):
        rng = np.random.default_rng(seed)
        p = np.array([d, y, float(tb.spec.inner(d)) + 6.0])
        picks = []
        for pid in photos:
            rid = refs[pid]
            uv = project_mm(PO.chart_to_posed([p], 1, hz, b.pose(rid)), b.camera(rid), b.frame)[0]
            picks.append({"photo": pid, "uv": (uv + rng.normal(0, noise, 2)).tolist()})
        try:
            rec = RD.read_point(tb, b, "P", picks, kind="corner", refs=refs)
        except ValueError:
            out[2] += 1
            continue
        out[0] += 1
        if abs(rec["X"] - p[2]) > 20.0:
            out[1] += 1
        else:
            assert rec["sides"] == [1] * len(photos) and abs(rec["X"] - p[2]) < 3.0, (photos, d, y, rec["X"], p)
    return out


def test_mirrored_chart_ghost_is_rejected(tb, bs, monkeypatch):
    """Photos that all see both temples' outer faces or neither (top 03, front 04 and its twin B3) try both temples'
    charts; the -X temple's chart point of the same world point (X ~ -71, t ~ -136) fits the pixels as well, and with
    1 px of pick noise it won 18 of 189 readings near the hinge (review p06). The lab's thickness bound |t| <= 40
    rejects it: no reading is a ghost, every one is on the charted temple within 3 mm. With the bound lifted the ghost
    returns (the test sees the defect)."""
    sets = [["03", "04"], ["03", "B3"], ["03", "04", "B3"]]
    args = (sets, (1.0, 3.0, 5.0), (-6.0, 2.0, 9.0), (1.0,), range(4))
    read, ghosts, _refused = _ghosts(tb, bs, TWINS, *args)
    assert ghosts == 0 and read >= 60
    monkeypatch.setattr(G, "T_LIMIT_MM", 1e9)
    assert _ghosts(tb, bs, TWINS, *args)[1] > 0


# --------------------------------------------------------------------------------------------- error model
def test_theta_term_is_positive_and_grows_with_d(tb, bs):
    """The camera term (each camera fit's theta moved +-THETA_SD_DEG, the point re-solved) rotates the read point
    about the hinge axis (chart d = hinge z - axis z = -0.5): it is > 0 and grows with d (the lever); the pick term
    barely moves."""
    cams, picks_sd = [], []
    for d in (10.0, 40.0, 80.0, 110.0):
        rec = RD.read_point(tb, bs, "P", corner_picks(bs, point(tb, d, 2.0, 5.0), ["05", "02", "03"]), kind="corner")
        cams.append(rec["sd_X_camera"])
        picks_sd.append(rec["sd_X_pick"])
    assert cams[0] > 0 and all(b > a for a, b in zip(cams, cams[1:])) and cams[-1] > 5 * cams[0], cams
    assert max(picks_sd) < 1.2 * min(picks_sd)
    # at d 110 every fit moved together would rotate the point 110 mm x 0.3 deg = 0.58 mm; three fits in quadrature
    # each move it part of the way
    assert 0.2 < cams[-1] < 0.58


def test_camera_shift_is_one_rotation_per_fit(tb, bs):
    """camera_shift (per camera fit, the point's move for theta +THETA_SD_DEG) is what re-reading with that fit's
    theta moved gives, and for one fit it is the same rotation at every d: X's shift is linear in d through the hinge
    axis (chart d -0.5), so it is one nuisance per fit for a shape fit, not independent noise per reading (a plane
    moved by a + b d stays a plane; review p09: 02's +0.3 deg moved X -0.013 .. -0.533 at d 5 .. 100)."""
    ds = (5.0, 25.0, 47.0, 76.0, 100.0)
    xs = []
    for d in ds:
        picks = corner_picks(bs, point(tb, d, 2.0, 5.0), ["05", "02"])
        rec = RD.read_point(tb, bs, "P", picks, kind="corner")
        xs.append(rec["camera_shift"]["02"][2])
        if d == 76.0:
            moved = SynthBasis()
            moved.POSES["reference-02"] = _pose(6.0 + RD.THETA_SD_DEG, 2.0 + RD.THETA_SD_DEG)
            again = RD.read_point(tb, moved, "P", picks, kind="corner")
            got = np.subtract([again["d"], again["y"], again["X"]], [rec["d"], rec["y"], rec["X"]])
            assert got == pytest.approx(rec["camera_shift"]["02"], abs=0.01)
    slope, icpt = np.polyfit(ds, xs, 1)
    assert np.max(np.abs(np.polyval([slope, icpt], ds) - xs)) < 0.01 and abs(slope) > 1e-3
    assert -icpt / slope == pytest.approx(-0.5, abs=5.0)                  # zero at the hinge axis


def test_crossing_covariance_matches_monte_carlo():
    """crossing_pixel's covariance (each line's offset and angle error at the crossing, extrapolated beyond the traced
    points, plus the trace's shared offset EDGE_SYS_PX) matches the spread of crossings of noisy traced points (sd 1 px
    both ways per point and one EDGE_SYS_PX offset per edge normal to it): eigen-sds within 8 %. The shared offset
    does not shrink with the point count: a crossing at both centroids of 6-point edges keeps sd >= EDGE_SYS_PX normal
    to each (review p5: without it the tip E's d spread was 1.24-1.33x its reported sd)."""
    rng = np.random.default_rng(11)
    c = np.array([300.0, 200.0])
    a, b = np.array([1.0, 0.18]), np.array([0.6, -0.8])
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    na, nb = np.array([-a[1], a[0]]), np.array([-b[1], b[0]])
    ea = [c + s * a for s in (-60.0, -45.0, -30.0)]
    eb = [c + s * b for s in (20.0, 35.0, 50.0, 65.0)]
    x0, cov, ang = RD.crossing_pixel(RD.edge_line(ea, 1.0), RD.edge_line(eb, 1.0))
    assert np.allclose(x0, c) and ang == pytest.approx(math.degrees(math.acos(abs(a @ b))), abs=1e-9)
    xs = []
    for _ in range(4000):
        la = RD.edge_line([q + rng.normal(0, 1.0, 2) for q in np.asarray(ea) + rng.normal(0, RD.EDGE_SYS_PX) * na], 1.0)
        lb = RD.edge_line([q + rng.normal(0, 1.0, 2) for q in np.asarray(eb) + rng.normal(0, RD.EDGE_SYS_PX) * nb], 1.0)
        xs.append(RD.crossing_pixel(la, lb)[0])
    emp = np.cov(np.asarray(xs).T)
    assert np.sqrt(np.linalg.eigvalsh(emp)) == pytest.approx(np.sqrt(np.linalg.eigvalsh(cov)), rel=0.08)
    six_a = [c + s * a for s in np.linspace(-30.0, 30.0, 6)]
    six_b = [c + s * b for s in np.linspace(-30.0, 30.0, 6)]
    _x, cov6, _a = RD.crossing_pixel(RD.edge_line(six_a, 1.0), RD.edge_line(six_b, 1.0))
    for n in (na, nb):
        assert math.sqrt(float(n @ cov6 @ n)) >= RD.EDGE_SYS_PX


def test_pick_sd_matches_monte_carlo(tb, bs):
    """sd_*_pick (each photo's pick sd 1 px plus CAMERA_PX_SD in quadrature, propagated by the least-squares
    covariance) matches the spread of readings of the same point with that pixel noise: within 15 %."""
    rng = np.random.default_rng(5)
    P = point(tb, 55.0, 0.0, 6.0)
    base = corner_picks(bs, P, ["05", "02", "03"])
    rec = RD.read_point(tb, bs, "P", base, kind="corner")
    s = math.hypot(1.0, RD.CAMERA_PX_SD)
    got = []
    for _ in range(250):
        picks = [{**pk, "uv": (np.asarray(pk["uv"]) + rng.normal(0, s, 2)).tolist()} for pk in base]
        r = RD.read_point(tb, bs, "P", picks, kind="corner")
        got.append([r["d"], r["y"], r["X"]])
    emp = np.std(np.asarray(got), axis=0, ddof=1)
    assert emp == pytest.approx([rec["sd_d_pick"], rec["sd_y_pick"], rec["sd_X_pick"]], rel=0.15)


def test_twin_camera_error_is_mapped_and_shared(tb, bs):
    """camera_source reads a transported camera's lineage from its basis record (fit_status "camera transported from
    05", or a transport block's source) and transport_px its own error (the contour residual its record names, else
    TRANSPORT_PX_DEFAULT; 0 for a fitted camera, whose contour is against the model). A twin's camera error is its
    source's mapped by the similarity (B1 = 05 x 1.1 here) plus its transport error: with exactly that noise drawn
    independently of readings, sd_*_pick matches the spread of 250 readings (within 15 %), the residual chi2 averages
    its dof, and W_RESIDUAL is as rare as its 0.3 % (review: a flat 3 px rule and an identity map over-trusted twins).
    No W_ONE_FIT: the sharing is in the covariance and ``fits``; sharing costs d most (the twins are its only
    evidence)."""
    assert RD.camera_source(bs, "black-side") == "reference-05"
    assert RD.camera_source(bs, "black-angled") == "reference-02"
    assert RD.camera_source(bs, "black-front") == "reference-04"
    assert RD.camera_source(bs, "reference-05") == "reference-05"
    assert [RD.transport_px(bs, r) for r in ("black-side", "black-angled", "black-front", "reference-05")] == \
        [RD.TRANSPORT_PX_DEFAULT, 0.82, RD.TRANSPORT_PX_DEFAULT, 0.0]
    bs.refs["reference-06"]["fit_status"] = "camera transported from 99"            # a source the basis lacks
    assert RD.camera_source(bs, "reference-06") == "reference-06" and RD.transport_px(bs, "reference-06") == 0.0
    P = point(tb, 70.0, 3.0, 6.0)
    refs = {"05": "reference-05", "B1": "black-side", "02": "reference-02"}
    picks = [{"photo": "05", "uv": px(bs, "05", P)}, {"photo": "B1", "uv": px(bs, "black-side", P)},
             {"photo": "02", "uv": px(bs, "02", P)}]
    shared = RD.read_point(tb, bs, "P", picks, kind="corner", refs=refs)
    assert shared["warnings"] == [] and shared["fits"] == ["05", "05", "02"]
    assert shared["rids"] == ["reference-05", "black-side", "reference-02"]
    assert set(shared["camera_shift"]) == {"05", "02"}
    assert (shared["d"], shared["X"]) == (pytest.approx(P[0], abs=1e-3), pytest.approx(P[2], abs=1e-3))
    rng = np.random.default_rng(17)
    got, chi, fired = [], [], 0
    for _ in range(250):
        r = RD.read_point(tb, bs, "P", model_noise(bs, picks, refs, rng), kind="corner", refs=refs)
        got.append([r["d"], r["y"], r["X"]])
        chi.append(r["chi2"])
        fired += "W_RESIDUAL" in codes(r)
    emp = np.std(np.asarray(got), axis=0, ddof=1)
    assert emp == pytest.approx([shared["sd_d_pick"], shared["sd_y_pick"], shared["sd_X_pick"]], rel=0.15)
    assert np.mean(chi) == pytest.approx(shared["dof"], rel=0.2) and fired <= 4
    bs.refs["black-side"] = {"fit_status": "fitted", "theta_observed": {"+1": True, "-1": True}}
    alone = RD.read_point(tb, bs, "P", picks, kind="corner", refs=refs)
    assert alone["fits"] == ["05", "B1", "02"] and alone["warnings"] == []
    assert shared["sd_d_pick"] > 1.1 * alone["sd_d_pick"] and shared["sd_y_pick"] > alone["sd_y_pick"]
    assert shared["sd_X_pick"] > alone["sd_X_pick"] and shared["sd_X_camera"] >= alone["sd_X_camera"]


def test_cov_pick_gives_x_where_a_node_puts_the_point(tb, bs):
    """cov_pick (3 x 3, d y X) carries what the marginal sds lose: a shallow crossing's position moves along a line of
    sight, so X correlates with d or y (here y-X 0.94; the real E: d-X -0.71). conditional_x(rec, d, y) (X where the
    point's (d, y) is known) predicts each noisy reading's X from the true (d, y) with errors whose spread is its
    conditional sd (within 20 %), well under the marginal sd_X_pick (review: a node at another d needs the
    conditional, not X unchanged)."""
    P = point(tb, 60.0, 1.0, 6.0)
    refs = {"05": "reference-05", "02": "reference-02"}
    base = edge_picks(bs, P, ["05", "02"], *TIP)
    rec = RD.read_point(tb, bs, "E", base, kind="crossing", refs=refs)
    C = np.asarray(rec["cov_pick"])
    assert np.allclose(C, C.T) and np.linalg.eigvalsh(C)[0] > 0
    s = np.sqrt(np.diag(C))
    assert max(abs(C[0, 2]) / (s[0] * s[2]), abs(C[1, 2]) / (s[1] * s[2])) > 0.5
    X0, sd0 = RD.conditional_x(rec, rec["d"], rec["y"])
    assert X0 == pytest.approx(rec["X"], abs=1e-9) and sd0 < 0.5 * rec["sd_X_pick"]
    rng = np.random.default_rng(23)
    err, marg = [], []
    for _ in range(200):
        r = RD.read_point(tb, bs, "E", model_noise(bs, base, refs, rng), kind="crossing", refs=refs)
        Xc, sdc = RD.conditional_x(r, P[0], P[1])
        err.append((Xc - P[2]) / sdc)
        marg.append(r["X"] - P[2])
    assert np.std(err) == pytest.approx(1.0, rel=0.2) and np.std(marg) > 2.0 * np.std(np.asarray(err) * sd0)


def test_refresh_recomputes_t_from_a_later_build(tb, bs):
    """A stored reading's t and model_t are the build's at read time; refresh(rec, later_build) recomputes them from
    the later inner face and surface (review: K's t was 7.27 on one inner face and 7.76 on run 7's for one X), and
    keeps X, d, y and the errors."""
    spec = FX.prada_default()
    spec["inner"] = [[d, x - 0.5] for d, x in spec["inner"]]
    tb2 = build_temple(spec)
    rec = RD.read_point(tb, bs, "K", corner_picks(bs, point(tb, 47.0, 3.0, 6.0), ["05", "02"]), kind="corner")
    new = RD.refresh(rec, tb2)
    assert new["t"] == pytest.approx(rec["t"] + 0.5, abs=2e-3) and new["X"] == rec["X"] and new["d"] == rec["d"]
    Xm, _r = G.surface_at(tb2, np.array([[rec["d"], rec["y"]]]))
    assert new["model_t"] == pytest.approx(float(Xm[0]) - float(tb2.spec.inner(rec["d"])), abs=2e-3)
    assert {k: v for k, v in new.items() if k not in ("t", "model_t")} == \
        {k: v for k, v in rec.items() if k not in ("t", "model_t")}


def test_model_t_holds_at_the_outline_edge(tb, bs):
    """model_t (v3p3-clean-lab): a reading a few microns outside the build's outline (a corner read ON the outline,
    once the fit moved its node: session2's C 0.006 mm and D 0.029 mm below revision 4's bottom outline printed
    "model out" where revision 3 printed 7.49) takes the surface's t within MODEL_EDGE_MM; farther out it has none,
    and the text says "outside the model's outline"; inside, it is the surface's t at the point itself."""
    d = 40.0
    y_bot = float(tb.spec.y_bot(np.array([d]))[0])
    inner = float(tb.spec.inner(d))
    Xe, _r = G.surface_at(tb, np.array([[d, y_bot + 1e-6]]))
    for off in (0.006, 0.029, 0.9 * RD.MODEL_EDGE_MM):
        assert np.isnan(G.surface_at(tb, np.array([[d, y_bot - off]]))[0][0])        # the point itself: no surface
        assert RD.model_t(tb, d, y_bot - off) == pytest.approx(float(Xe[0]) - inner, abs=0.02), off
    assert RD.model_t(tb, d, y_bot - 1.5 * RD.MODEL_EDGE_MM) is None
    Xm, _r = G.surface_at(tb, np.array([[d, 2.0]]))
    assert RD.model_t(tb, d, 2.0) == pytest.approx(float(Xm[0]) - inner, abs=1e-12)
    rec = RD.read_point(tb, bs, "K", corner_picks(bs, point(tb, 47.0, 3.0, 6.0), ["05", "02"]), kind="corner")
    out = RD.refresh({**rec, "y": y_bot - 0.02, "d": d}, tb)
    assert out["model_t"] == pytest.approx(float(Xe[0]) - inner, abs=0.02)
    out = RD.refresh({**rec, "y": y_bot - 1.0, "d": d}, tb)
    assert out["model_t"] is None and f"({RD.OUTSIDE_MODEL}); residual px" in RD.reading_text(out)


# --------------------------------------------------------------------------------------------- the slide line
def test_a_corner_pick_carries_the_slide_line(tb, bs):
    """A two-direction reading cannot check where its pixels lie along the epipolar line between the directions; the
    record's slide (both kinds) gives each photo's line angle and the point's move per px of slide along it, and the
    text prints it. A corner pick slid 6 px along that line in 05 moves the point by 6 x that with no residual (the run
    1-7 failure: a tip read as a corner slid back with a residual that only shrank, review p2). Twins are one
    direction. With a third direction the same slide shows in the residual, and there is no slide line."""
    P = point(tb, 70.0, 4.0, 6.0)
    for kind, picks in (("corner", corner_picks(bs, P, ["05", "02"])),
                        ("crossing", edge_picks(bs, P, ["05", "02"], (1.0, 0.3, -0.05), (0.2, -1.0, 0.1)))):
        rec = RD.read_point(tb, bs, "E", picks, kind=kind)
        sl = rec["slide"]
        assert sl["groups"] == [["05"], ["02"]] and len(sl["deg"]) == len(sl["mm_per_px"]) == 2
        assert [ln[0]["to"] for ln in rec["lines"]] == [["02"], ["05"]]
        assert "; slide line (+ rises to the right; a slide along it does not show in the residual; per px to the " \
               "right the point moves): 05 " in RD.reading_text(rec)
    a = math.radians(sl["deg"][0])
    for k in (6.0, -6.0):
        slid = corner_picks(bs, P, ["05", "02"])
        slid[0]["uv"] = (np.asarray(slid[0]["uv"]) + k * np.array([math.cos(a), -math.sin(a)])).tolist()
        r = RD.read_point(tb, bs, "E", slid, kind="corner")
        move = np.subtract([r["d"], r["y"], r["X"]], P)
        assert move == pytest.approx(k * np.asarray(sl["mm_per_px"][0]), rel=0.05, abs=0.01)
        assert max(r["res_px"]) < 0.05 and r["warnings"] == [] and np.linalg.norm(move) > 0.3
    tw = RD.read_point(tb, bs, "E", corner_picks(bs, P, ["05", "B1", "02"]), kind="corner", refs=TWINS)
    assert tw["slide"]["groups"] == [["05", "B1"], ["02"]]
    three = corner_picks(bs, P, ["05", "02", "03"])
    assert RD.read_point(tb, bs, "E", three, kind="corner")["slide"] is None
    three[0]["uv"] = (np.asarray(three[0]["uv"]) + 6.0 * np.array([math.cos(a), -math.sin(a)])).tolist()
    r3 = RD.read_point(tb, bs, "E", three, kind="corner")
    assert max(r3["res_px"]) > 1.0 and len(r3["lines"][0]) == 2


# --------------------------------------------------------------------------------------------- warnings
def test_along_line_fires_for_an_edge_in_the_epipolar_plane(tb, bs):
    """An edge in the plane of two photos' lines of sight through the point (pose.toward_camera: independent of
    readings) lies along the slide line in both photos: W_ALONG_LINE names it per direction with its angle and how far
    the crossing moves along the line per px of an edge's offset (review: the warning now sizes the consequence, so a
    crisp corner and a tip read differently), and edge_epipolar_deg is ~0 there; the edge normal to that plane is not
    named. A slide of the other edge moves the reading with no residual. With a third direction (03) the slide shows
    in the residual, so nothing is "along the line" (changed: the pair-only warning was false for the reading)."""
    P = point(tb)
    w5, w2 = line_of_sight(bs, "05", P), line_of_sight(bs, "02", P)
    v1 = (w5 + w2) / np.linalg.norm(w5 + w2)
    v2 = np.cross(w5, w2)
    rec = RD.read_point(tb, bs, "E", edge_picks(bs, P, ["05", "02"], v1, v2), kind="crossing")
    assert rec["X"] == pytest.approx(P[2], abs=1e-3)
    w = [x for x in rec["warnings"] if x["code"] == "W_ALONG_LINE"]
    assert len(w) == 1 and [(i["photo"], i["edges"]) for i in w[0]["items"]] == [("05", [1]), ("02", [1])]
    assert all(i["gain"] >= 1.0 for i in w[0]["items"])
    assert rec["edge_epipolar_deg"][0][0] < 0.05 and rec["edge_epipolar_deg"][1][0] < 0.05
    assert min(rec["edge_epipolar_deg"][0][1], rec["edge_epipolar_deg"][1][1]) > RD.EPIPOLAR_WARN_DEG
    g5, g2 = (RD.fixed(i["gain"], 1) for i in w[0]["items"])
    assert re.fullmatch(rf"edges along the slide line: 05 edge 1 \(0\.0°\), 02 edge 1 \(0\.0°\); along it the "
                        rf"crossing moves 05 {g5}, 02 {g2} px per px of an edge's offset \(the point up to d [\d.]+, y "
                        rf"[\d.]+, X [\d.]+ mm per px, (05|02)\), and the residual cannot check that each edge is the "
                        rf"same feature in both directions", w[0]["message"]), w[0]["message"]
    rec = RD.read_point(tb, bs, "E", edge_picks(bs, P, ["05", "02"], (1.0, 0.3, -0.05), (0.2, -1.0, 0.1)),
                        kind="crossing")
    assert "W_ALONG_LINE" not in codes(rec) and min(map(min, rec["edge_epipolar_deg"])) > RD.EPIPOLAR_WARN_DEG
    # a slide along the epipolar edge in one photo: the reading moves, the residual does not show it
    for photos, seen in ((["05", "02"], False), (["05", "02", "03"], True)):
        picks = edge_picks(bs, P, photos, v1, v2)
        picks[0]["edges"][1] = edge_picks(bs, P + 3.0 * v1, ["05"], v1, v2)[0]["edges"][1]   # edge 2 moved 3 mm
        r = RD.read_point(tb, bs, "E", picks, kind="crossing")
        assert ("W_ALONG_LINE" in codes(r)) is not seen and (r["slide"] is None) is seen
        if seen:
            assert max(r["res_px"]) > 1.0 and r["edge_epipolar_deg"][0][0] < 0.05
        else:
            assert np.linalg.norm(np.array([r["d"], r["y"], r["X"]]) - P) > 1.0 and max(r["res_px"]) < 0.05


def test_shallow_crossing_and_residual_warnings(tb, bs):
    """W_SHALLOW_CROSSING: edges crossing under 15 deg in a photo, with 1 / sin(angle) px of crossing move per px of
    edge offset (changed: the number reads "px per px", not "px"). W_RESIDUAL: a pick 8 px off the others' point in
    the third direction, across its lines to the others, puts the residuals' chi2 above its 99.7 % point for 2n - 3
    dof: the pixels are not one point within their stated error (changed: judged in the reading's whole stated error,
    every photo named, each photo's residual in px and sd in the text; the offset is now vertical: the old horizontal
    8 px lay 7.6 deg from 03's line to 05, nearly a slide only 02 sees, chi2 13.4 against 13.9, while every direction
    15 deg or more off that line gives 15-23). Exact picks of a well-crossed point warn of neither."""
    P = point(tb)
    rec = RD.read_point(tb, bs, "E", edge_picks(bs, P, ["05", "02"], (1.0, 0.05, 0.0), (1.0, -0.05, 0.0)),
                        kind="crossing")
    w = next(x for x in rec["warnings"] if x["code"] == "W_SHALLOW_CROSSING")
    a5, a2 = rec["crossing_deg"]
    assert a5 < RD.SHALLOW_DEG and a2 < RD.SHALLOW_DEG and w["photos"] == ["05", "02"]
    items = re.fullmatch(r"the edges cross at ([\d.]+)° in 05, ([\d.]+)° in 02: along them the crossing moves "
                         r"([\d.]+), ([\d.]+) px per px of edge offset", w["message"])
    assert items, w["message"]
    for (ang, k), a in zip(((items[1], items[3]), (items[2], items[4])), (a5, a2)):
        assert float(ang) == pytest.approx(a, abs=0.051)
        assert float(k) == pytest.approx(1 / math.sin(math.radians(a)), abs=0.051)
    for cov, pk in zip(rec["uv_cov"], rec["picks"]):                # weak along the edges, strong across them
        ev, vec = np.linalg.eigh(np.asarray(cov))
        along = np.subtract(pk["edges"][0][-1], pk["edges"][0][0])
        assert ev[1] / ev[0] > 10 and abs(vec[:, 1] @ along) / np.linalg.norm(along) > math.cos(math.radians(10))
    assert rec["sd_d"] > 3 * RD.read_point(tb, bs, "E", corner_picks(bs, P, ["05", "02"]), kind="corner")["sd_d"]
    picks = corner_picks(bs, P, ["05", "02", "03"])
    picks[2]["uv"] = (np.asarray(picks[2]["uv"]) + [0.0, 8.0]).tolist()
    rec = RD.read_point(tb, bs, "P", picks, kind="corner")
    w = next(x for x in rec["warnings"] if x["code"] == "W_RESIDUAL")
    limit = RD._chi2.ppf(1 - RD.RESIDUAL_P, 3)
    assert rec["chi2"] > limit and w["photos"] == ["05", "02", "03"]
    assert w["message"] == (f"the pixels are not one point within their stated error: the residuals' chi2 is "
                            f"{RD.fixed(rec['chi2'], 1)} for 3 degrees of freedom (its 99.7 % point is "
                            f"{RD.fixed(limit, 1)})")
    assert rec["res_sd"][2] == max(rec["res_sd"]) > 2.0
    assert "residual px (in sd of the photo's stated pixel error) 05 " in RD.reading_text(rec)
    assert RD.read_point(tb, bs, "P", corner_picks(bs, P, ["05", "02", "03"]), kind="corner")["warnings"] == []


def test_residual_is_judged_in_the_readings_own_error(tb, bs):
    """Consistent shallow crossings (a tip: edges 9 deg apart in 3D, 4 points over ~70 px each) in 4 photos of two
    directions (05 + B1, 02 + B2), with exactly the error the reading states drawn independently of it (1 px per traced
    point, EDGE_SYS_PX per edge, a camera error per fit mapped to the twin plus its transport error): W_RESIDUAL fires
    in at most 3 of 150 (0.3 % expected), while the residuals often exceed 3 px (the old flat rule fired on 87 % of the
    real 4-photo E, review p11/p12). An 8 px off-feature pick across the edges still fires."""
    P = point(tb, 60.0, 2.0, 6.0)
    photos = ["05", "B1", "02", "B2"]
    base = edge_picks(bs, P, photos, *TIP)
    rng = np.random.default_rng(29)
    fired, big = 0, 0
    for _ in range(150):
        r = RD.read_point(tb, bs, "E", model_noise(bs, base, TWINS, rng), kind="crossing", refs=TWINS)
        fired += "W_RESIDUAL" in codes(r)
        big += max(r["res_px"]) > 3.0
    assert fired <= 3 and big >= 15, (fired, big)
    off = [dict(pk) for pk in base]
    n = np.array([-0.05, 1.0]) / np.hypot(0.05, 1.0)
    off[2] = {**off[2], "edges": [(np.asarray(e) + 8.0 * n).tolist() for e in off[2]["edges"]]}
    r = RD.read_point(tb, bs, "E", off, kind="crossing", refs=TWINS)
    assert "W_RESIDUAL" in codes(r)


# --------------------------------------------------------------------------------------------- refusals
def test_near_parallel_views_are_refused(tb, bs):
    """The condition refusal (pixel Jacobian over COND_MAX 20): a side view 0.4 deg from another, a twin (one camera
    moved by an image similarity: one direction twice) and the opposite side view of the mirrored temple (one line of
    sight, mirrored), each with exact picks; a third photo from another direction lifts it."""
    P = point(tb)
    for photos, sides in ((["05", "06"], {}), (["05", "black-side"], {}), (["05", "01"], {"01": -1})):
        with pytest.raises(ValueError, match=r"photos .* see this point along nearly one line: the pixel Jacobian's "
                                             r"condition number is (\d+|above 10000) \(limit 20\)") as e:
            RD.read_point(tb, bs, "P", corner_picks(bs, P, photos, sides), kind="corner")
        m = re.search(r"number is (\d+)", str(e.value))
        assert m is None or int(m.group(1)) > RD.COND_MAX
    rec = RD.read_point(tb, bs, "P", corner_picks(bs, P, ["05", "06", "02"]), kind="corner")
    assert rec["X"] == pytest.approx(P[2], abs=1e-3) and rec["slide"]["groups"] == [["05", "06"], ["02"]]


def test_bad_picks_are_refused(tb, bs, monkeypatch):
    """ValueError (the lab's E_ARGS) for every malformed reading, before any solve; and "no point fits" when no
    temple combination gives a point in front of the cameras within the chart's bounds (here: bounds of 10 mm)."""
    P = point(tb)
    good_c = corner_picks(bs, P, ["05", "02"])
    good_x = edge_picks(bs, P, ["05", "02"], (1.0, 0.3, -0.05), (0.2, -1.0, 0.1))

    def with_(picks, i, **kw):
        out = [dict(p) for p in picks]
        out[i].update(kw)
        return out
    cases = [
        ("K", good_c, "edge", "kind must be one of"),
        ("1K", good_c, "corner", "name must be a node name"),
        ("base", good_c, "corner", "name must be a node name"),
        ("K", good_c[:1], "corner", "picks must name 2..4 photos"),
        ("K", corner_picks(bs, P, ["05", "02", "03", "04", "01"]), "corner", "picks must name 2..4 photos"),
        ("K", [good_c[0], good_c[0]], "corner", "repeats photo 05"),
        ("K", with_(good_c, 1, photo="09"), "corner", "photo 09 has no camera"),
        ("K", with_(good_c, 1, uv=[700.0, 10.0]), "corner", "inside the native 640 x 480 photo 02"),
        ("K", with_(good_c, 1, uv=[float("nan"), 10.0]), "corner", r"must be \[u, v\] finite"),
        ("K", with_(good_c, 1, sd_px=0.0), "corner", "sd_px must be null or 0.1..20 px"),
        ("K", with_(good_c, 1, sd_px=True), "corner", "sd_px must be null or"),
        ("K", with_(good_c, 1, edges=good_x[1]["edges"]), "corner", "a corner reading takes uv"),
        ("K", good_c, "crossing", "a crossing reading takes edges"),
        ("K", with_(good_x, 0, uv=[10.0, 10.0]), "crossing", "a crossing reading takes edges"),
        ("K", with_(good_x, 0, edges=good_x[0]["edges"][:1]), "crossing", "a crossing reading takes edges"),
        ("K", with_(good_x, 0, edges=[good_x[0]["edges"][0][:1], good_x[0]["edges"][1]]), "crossing",
         r"edges\[0\] must be 2..6 points"),
        ("K", with_(good_x, 0, edges=[good_x[0]["edges"][0] * 3, good_x[0]["edges"][1]]), "crossing",
         r"edges\[0\] must be 2..6 points"),
        ("K", with_(good_x, 0, edges=[[[100.0, 100.0], [101.0, 101.0]], good_x[0]["edges"][1]]), "crossing",
         "must span at least 4 px"),
        ("K", with_(good_x, 0, edges=[[[100.0, 100.0], [200.0, 100.0]], [[100.0, 120.0], [200.0, 121.0]]]),
         "crossing", "from parallel: they do not cross at one pixel"),
        ("K", with_(good_x, 0, edges=[[[600.0, 100.0], [630.0, 100.0]], [[600.0, 120.0], [630.0, 110.0]]]),
         "crossing", "cross outside the photo"),
    ]
    for name, picks, kind, what in cases:
        with pytest.raises(ValueError, match=what):
            RD.read_point(tb, bs, name, picks, kind=kind)
    with pytest.raises(ValueError, match="photo 05 has no camera"):           # the lab's own mapping
        RD.read_point(tb, bs, "K", good_c, kind="corner", refs={"02": "reference-02"})
    monkeypatch.setattr(G, "D_LIMIT_MM", 10.0)
    with pytest.raises(ValueError, match=r"no chart point in front of the cameras fits these pixels \(photos 05, "
                                         r"02\)"):
        RD.read_point(tb, bs, "K", good_c, kind="corner")


# --------------------------------------------------------------------------------------------- text and sheet
def test_reading_text_prints_the_record():
    """One line, every number from the record through fixed (half up from 0.001): d, y, X with sd in mm, X's pick part
    and its camera part per fit (fits that move X under 0.01 left out, the largest kept), t in mm with the model's t
    there ("outside the model's outline" where it has none: v3p3-clean-lab, session2 printed "model out"), each
    photo's residual in px and in sd of that photo's stated pixel error (it read "residual px (sd)", whose sd unsaid:
    v3p3 astra P3-9), the crossing angles, the condition number with its limit (it was a bare
    "condition 2.0"), the slide line, then the warnings in order."""
    rec = {"name": "E", "kind": "crossing", "d": 75.795, "y": 9.381, "X": 68.293, "t": 7.005, "sd_X": 0.891,
           "sd_X_pick": 0.792, "sd_X_camera": 0.408, "sd_d": 1.081, "sd_y": 0.289, "photos": ["B1", "B2"],
           "camera_shift": {"05": [-0.006, 0.047, -0.002], "02": [0.027, -0.047, -0.408]},
           "res_px": [2.354, 1.605], "res_sd": [1.04, 0.95], "crossing_deg": [8.154, 18.894], "cond": 1.96,
           "model_t": None, "slide": {"groups": [["B1"], ["B2"]], "deg": [-3.581, 9.253],
                                      "mm_per_px": [[0.119, -0.025, -0.06], [-0.002, 0.032, 0.13]]},
           "warnings": [{"code": "W_SHALLOW_CROSSING", "message": "the edges cross at 8.2° in B1: ..."}]}
    assert RD.reading_text(rec) == (
        "E (crossing; B1, B2): d 75.80 +-1.08, y 9.38 +-0.29, X 68.29 +-0.89 mm (pick 0.79; camera 0.41: theta +-0.3° "
        "of fit 02 0.41); t 7.01 mm at that d (outside the model's outline); residual px (in sd of the photo's stated "
        "pixel error) B1 2.4 (1.0), B2 1.6 (1.0); crossing deg B1 8.2, B2 18.9; condition number 2.0 (limit 20); "
        "slide line (+ rises to the right; a slide along it does not show in the residual; per px to the right the "
        "point moves): B1 -3.6°: d +0.12 y -0.03 "
        "X -0.06 mm (B1); B2 +9.3°: d +0.00 y +0.03 X +0.13 mm (B2); W_SHALLOW_CROSSING the edges cross at 8.2° in B1: "
        "...")
    rec.update(kind="corner", crossing_deg=None, warnings=[], model_t=5.0, slide=None,
               camera_shift={"05": [0.0, 0.0, 0.2], "02": [0.0, 0.0, -0.35]})
    assert RD.reading_text(rec).endswith("(pick 0.79; camera 0.41: theta +-0.3° of fit 05 0.20, 02 0.35); t 7.01 mm "
                                         "at that d (model t 5.00); residual px (in sd of the photo's stated pixel "
                                         "error) B1 2.4 (1.0), B2 1.6 (1.0); condition number 2.0 (limit 20)")
    assert f"(limit {RD.COND_MAX:g})" in RD.reading_text(rec) and "model out" not in RD.reading_text(rec)
    assert RD.signed(-0.004, 2) == "+0.00" and RD.signed(-0.006, 2) == "-0.01" and RD.signed(0.12, 2) == "+0.12"


def test_pick_sheet_draws_each_photo(tb, bs, tmp_path):
    """One image per reading, a row per photo: the clean crop (none of the drawing colours) and the same crop drawn:
    the read point's projection in magenta, the traced points in cyan, the pick in green, the lines to the other
    directions in orange (changed: a clean panel beside each drawn one, a context box of at least BOX_MIN_W x
    BOX_MIN_H native px, the lines, and a title with d, y, X and their sd). Its size is fixed by the photo count."""
    P = point(tb)
    rec = RD.read_point(tb, bs, "E", edge_picks(bs, P, ["05", "02", "03"], (1.0, 0.3, -0.05), (0.2, -1.0, 0.1)),
                        kind="crossing")
    out = RD.pick_sheet(bs, rec, tmp_path / "sheets" / "E.png")
    im = np.asarray(Image.open(out).convert("RGB")).astype(int)
    row = RD.PANEL_H + RD.LABEL_PX + RD.GAP_PX
    assert im.shape == (RD.TITLE_PX + 3 * row + RD.GAP_PX, 2 * RD.PANEL_W + 3 * RD.GAP_PX, 3)
    left = im[RD.TITLE_PX:, RD.GAP_PX:RD.GAP_PX + RD.PANEL_W]
    right = im[RD.TITLE_PX:, 2 * RD.GAP_PX + RD.PANEL_W:]
    for rgb in (RD.FIT_RGB, RD.EDGE_RGB, RD.PICK_RGB, RD.LINE_RGB):
        assert (np.abs(right - rgb).sum(axis=2) < 30).sum() > 20, rgb
        assert (np.abs(left - rgb).sum(axis=2) < 30).sum() == 0, rgb
    rec2 = RD.read_point(tb, bs, "K", corner_picks(bs, P, ["05", "02"]), kind="corner")
    im2 = np.asarray(Image.open(RD.pick_sheet(bs, rec2, tmp_path / "K.png")).convert("RGB")).astype(int)
    assert im2.shape[0] == RD.TITLE_PX + 2 * row + RD.GAP_PX
    for rgb in (RD.FIT_RGB, RD.LINE_RGB, RD.PICK_RGB):
        assert (np.abs(im2 - rgb).sum(axis=2) < 30).sum() > 20
    k = min(RD.ZOOM_MAX_K, RD.PANEL_W / RD.BOX_MIN_W, RD.PANEL_H / RD.BOX_MIN_H)
    assert k < 2.5                                                    # a corner's crop shows its context


# --------------------------------------------------------------------------------------------- real cameras
# The crown-geometry picks (picks.json): per photo the traced edges' total-least-squares lines (centre c, direction,
# traced span along it in px) and the corners K (4|1 x 1|3) and E (T|4 back x 4|2) as their crossings.
REAL_EDGES = {
    "B1": {"AK": ([524.63, 130.53], [1.0, 0.00137], [-189.63, 190.37]),
           "KC": ([604.36, 248.44], [0.69513, -0.71889], [-99.73, 105.23]),
           "T4b": ([776.56, 102.79], [0.99207, 0.12572], [-121.57, 123.37]),
           "KE": ([841.0, 128.09], [0.99987, -0.0162], [-99.01, 99.02])},
    "B2": {"AK": ([1424.06, 561.87], [0.95842, -0.28537], [-91.8, 94.06]),
           "KC": ([1480.77, 656.0], [0.37269, -0.92796], [-123.31, 122.3]),
           "T4b": ([1544.0, 492.28], [0.99973, -0.02328], [-66.02, 66.02]),
           "KE": ([1573.63, 512.25], [0.93833, -0.34573], [-40.03, 41.01])},
    "05": {"AK": ([342.4, 302.03], [1.0, 0.00058], [-162.4, 162.6]),
           "KC": ([482.17, 336.06], [0.69229, -0.72162], [-37.56, 36.07]),
           "T4b": ([563.86, 276.71], [0.992, 0.12621], [-109.79, 116.0]),
           "KE": ([612.65, 299.88], [0.99988, -0.01537], [-87.66, 87.36])},
    "02": {"AK": ([1466.69, 607.73], [0.95811, -0.28639], [-90.34, 90.36]),
           "KC": ([1547.34, 622.81], [0.35202, -0.93599], [-47.52, 35.05]),
           "T4b": ([1584.0, 542.41], [0.99962, -0.02742], [-66.01, 66.03]),
           "KE": ([1609.63, 560.93], [0.9371, -0.34905], [-36.76, 35.62])}}
REAL_CORNERS = {"K": {"B1": [718.13, 130.8], "B2": [1531.42, 529.9], "05": [514.73, 302.13], "02": [1563.95, 578.66]},
                "E": {"B1": [960.9, 126.15], "B2": [1633.49, 490.2], "05": [731.57, 298.05],
                      "02": [1665.36, 540.18]}}
REAL_REFS = {"B1": "black-side", "B2": "black-angled", "B3": "black-front", "01": "reference-01",
             "02": "reference-02", "03": "reference-03", "04": "reference-04", "05": "reference-05",
             "06": "reference-06"}


@pytest.fixture(scope="module")
def real_basis():
    if not (BASIS_JOB / "cameras").is_dir():
        pytest.skip("data/wp6v2/basis is not on this machine")
    return GC.load_basis(BASIS_JOB)


def real_edges(pid: str, e: str, n: int = 6) -> list:
    """n points along a measured edge line over its traced span (what a careful trace of it gives)."""
    c, d, span = (np.asarray(v, float) for v in REAL_EDGES[pid][e])
    return [(c + s * d).tolist() for s in np.linspace(span[0], span[1], n)]


def test_real_pick_copy_matches_picks_json():
    if not PICKS_JSON.is_file():
        pytest.skip("picks.json is not on this machine")
    doc = json.loads(PICKS_JSON.read_text(encoding="utf-8"))
    for pid, edges in REAL_EDGES.items():
        for e, (c, d, span) in edges.items():
            rec = doc[pid]["edges"][e]
            assert (rec["c"], rec["d"], rec["span"]) == (c, d, span)
        for cn in ("K", "E"):
            assert doc[pid]["corners"][cn]["uv"] == REAL_CORNERS[cn][pid]


def test_real_cameras_read_the_crown_corner_K(tb, real_basis):
    """K (the logo's back corner) on the Prada basis: as a corner in B1, B2, 05, 02, X 70.09 against the
    investigation's bundle 70.07 +- 0.13 (4-photo joint 70.08; v2's lab.corner_reading); as the crossing of the traced
    4|1 and 1|3 creases in the same photos, the crossing pixels are picks.json's and X ~70.05. The camera lineage is
    the basis's (B1 <- 05 with no contour number, B2 <- 02 at 0.82 px, B3 <- 04 at 1.09 px); two directions, so the
    slide line runs 3.7 deg down to the right in B1 and 9.4 up in B2; the crossing names the near-horizontal 4|1
    crease along it, and its crossing slides only ~1.4 px per px of an edge's offset (d < 0.3 mm: a crisp corner;
    the tip E reads 7.0, below). Changed: no W_ONE_FIT (the design's case is always refused; the sharing is in
    ``fits``)."""
    assert [RD.transport_px(real_basis, r) for r in ("black-side", "black-angled", "black-front", "reference-05")] == \
        [RD.TRANSPORT_PX_DEFAULT, 0.82, 1.09, 0.0]
    photos = ["B1", "B2", "05", "02"]
    corner = RD.read_point(tb, real_basis, "K", [{"photo": p, "uv": REAL_CORNERS["K"][p]} for p in photos],
                           kind="corner", refs=REAL_REFS)
    assert corner["X"] == pytest.approx(70.07, abs=0.3) and corner["d"] == pytest.approx(46.95, abs=0.3)
    assert corner["y"] == pytest.approx(9.65, abs=0.3) and max(corner["res_px"]) < 2.0
    assert corner["warnings"] == [] and corner["fits"] == ["05", "02", "05", "02"]
    assert corner["slide"]["groups"] == [["B1", "05"], ["B2", "02"]]
    assert corner["slide"]["deg"][0] == pytest.approx(-3.7, abs=0.3) and corner["slide"]["deg"][1] == \
        pytest.approx(9.4, abs=0.5)
    cross = RD.read_point(tb, real_basis, "K", [{"photo": p, "edges": [real_edges(p, "AK"), real_edges(p, "KC")]}
                                                for p in photos], kind="crossing", refs=REAL_REFS)
    assert cross["uv"] == [pytest.approx(REAL_CORNERS["K"][p], abs=0.05) for p in photos]
    assert cross["X"] == pytest.approx(70.07, abs=0.3) and cross["d"] == pytest.approx(46.95, abs=0.3)
    assert codes(cross) == ["W_ALONG_LINE"] and min(cross["crossing_deg"]) > 45.0
    items = cross["warnings"][0]["items"]
    assert [(i["photo"], i["edges"]) for i in items] == [(p, [1]) for p in photos]
    assert all(1.2 < i["gain"] < 1.6 for i in items)
    assert float(re.search(r"up to d ([\d.]+)", cross["warnings"][0]["message"]).group(1)) < 0.3
    # The historical side camera is explicitly unobserved: its 10-degree term now contributes.
    assert cross['opening_uncertainty']['05']['sd_deg'] == 10.
    assert cross['opening_uncertainty']['02']['sd_deg'] == .3
    assert cross['sd_X'] == pytest.approx(.435, abs=.002)
    assert cross['sd_X_camera'] == pytest.approx(.376, abs=.002)
    assert abs(cross['camera_shift']['02'][2])/.3 > 10 * abs(cross['camera_shift']['05'][2])/10.
    for pair in (["B1", "05"], ["B2", "02"]):                                  # one direction twice: refused
        with pytest.raises(ValueError, match="along nearly one line"):
            RD.read_point(tb, real_basis, "K", [{"photo": p, "uv": REAL_CORNERS["K"][p]} for p in pair],
                          kind="corner", refs=REAL_REFS)


def test_real_cameras_put_the_crown_tip_E_where_the_photos_end_it(tb, real_basis, tmp_path):
    """The crown tip E as the crossing of its two traced edges (T|4 back x 4|2) in B1 + B2: d ~75.7, inside the
    photos' 75.5-77.2 (every v2 run put it at d 85-96.5). The reading says why it is the weakest point: B1's edges
    cross at 8.2 deg (7.1 px along them per px), both lie 3.6-4.5 deg from the slide line in B1 and edge 1 7.9 in B2,
    and the crossing slides 7.0 px along it per px of an edge's offset (d > 0.6 mm per px, against K's < 0.3); its d is
    good to about 1-1.6 mm. The corner pick of the same pixels is within 0.03 of v2's lab.corner_reading (d 75.59, y
    9.43, X 68.41: tri.log B1+B2; changed from 0.01: the twins' camera errors now differ, B1's is 05's mapped plus its
    transport), claims sd_d 0.22, and carries the same slide line: 0.12 mm of d per px in B1, so its 16 px slide back
    along the top edge (the v2 failure) moves d ~1.9 with a residual that shrinks and no warning."""
    rec = RD.read_point(tb, real_basis, "E", [{"photo": p, "edges": [real_edges(p, "T4b"), real_edges(p, "KE")]}
                                              for p in ("B1", "B2")], kind="crossing", refs=REAL_REFS)
    assert 75.5 <= rec["d"] <= 77.2 and rec["y"] == pytest.approx(9.4, abs=0.3)
    assert rec["uv"] == [pytest.approx(REAL_CORNERS["E"][p], abs=0.05) for p in ("B1", "B2")]
    assert codes(rec) == ["W_ALONG_LINE", "W_SHALLOW_CROSSING"] and json.loads(json.dumps(rec)) == rec
    items = rec["warnings"][0]["items"]
    assert [(i["photo"], i["edges"]) for i in items] == [("B1", [1, 2]), ("B2", [1])]
    assert items[0]["gain"] == pytest.approx(7.0, abs=0.2)
    assert float(re.search(r"up to d ([\d.]+)", rec["warnings"][0]["message"]).group(1)) > 0.6
    assert rec["warnings"][1]["photos"] == ["B1"] and rec["crossing_deg"][0] == pytest.approx(8.15, abs=0.05)
    assert 0.6 < rec["sd_d"] < 1.6 and rec["sd_X"] > 0.5
    corner = RD.read_point(tb, real_basis, "E", [{"photo": p, "uv": REAL_CORNERS["E"][p]} for p in ("B1", "B2")],
                           kind="corner", refs=REAL_REFS)
    assert (corner["d"], corner["y"], corner["X"]) == (pytest.approx(75.59, abs=0.03), pytest.approx(9.43, abs=0.03),
                                                       pytest.approx(68.41, abs=0.03))
    assert corner['sd_d'] == pytest.approx(.322, abs=.002)  # widened unobserved source opening
    assert corner['sd_d'] < rec['sd_d'] and corner['warnings'] == []
    assert corner["slide"]["mm_per_px"][0][0] == pytest.approx(0.12, abs=0.02)
    assert "; slide line (" in RD.reading_text(corner)
    slid = RD.read_point(tb, real_basis, "E", [{"photo": p, "uv": (np.asarray(REAL_CORNERS["E"][p]) + 16.0 * np.asarray(
        REAL_EDGES[p]["T4b"][1])).tolist()} for p in ("B1", "B2")], kind="corner", refs=REAL_REFS)
    assert slid["d"] - corner["d"] == pytest.approx(16.0 * corner["slide"]["mm_per_px"][0][0], rel=0.2)
    assert max(slid["res_px"]) < max(corner["res_px"]) and slid["warnings"] == []
    try:
        out = RD.pick_sheet(real_basis, rec, tmp_path / "E.png")
    except (OSError, ValueError) as exc:                                       # the photos are not on this machine
        pytest.skip(f"real photos unavailable for the pick sheet: {exc}")
    assert Image.open(out).size == (2 * RD.PANEL_W + 3 * RD.GAP_PX,
                                    RD.TITLE_PX + 2 * (RD.PANEL_H + RD.LABEL_PX + RD.GAP_PX) + RD.GAP_PX)


def test_real_cameras_no_ghost_and_honest_residuals(tb, real_basis):
    """On the Prada basis: (1) readings from photos that all see both temples (03, 04, 06, B3) near the hinge with
    1 px of pick noise are never the mirrored-chart ghost (review p06: 5 of 567 were, e.g. 03 + B3 at d 3: X -71.4, t
    -136.4, residuals 0.56 / 0.47 px; the seeds below gave the pre-fix module the ghost in every set, 105 of 640 such
    draws at d 1-3, each with smaller residuals than the true point's); (2) the real 4-photo E and K crossings, their
    edges moved to pass exactly through one point and then given the reading's own error (1 px per traced point,
    EDGE_SYS_PX per edge, a camera error per fit mapped to the twin plus its transport error), warn W_RESIDUAL in at
    most 2 of 60 (review p11: the flat 3 px rule fired on 89 % of the E readings)."""
    refs = {k: REAL_REFS[k] for k in ("03", "04", "06", "B3")}
    read, ghosts, _ref = _ghosts(tb, real_basis, refs, [["03", "B3"], ["06", "B3"], ["03", "04", "B3"]],
                                 (1.0, 3.0), (-6.0, 2.0), (1.0,), (3, 6, 13))
    assert ghosts == 0 and read >= 20
    hz = float(tb.spec.hinge["z_mm"])
    photos = ["B1", "B2", "05", "02"]
    rng = np.random.default_rng(31)
    for e1, e2 in (("T4b", "KE"), ("AK", "KC")):
        rec0 = RD.read_point(tb, real_basis, "E", [{"photo": p, "edges": [real_edges(p, e1), real_edges(p, e2)]}
                                                   for p in photos], kind="crossing", refs=REAL_REFS)
        P = np.array([rec0["d"], rec0["y"], rec0["X"]])
        exact = []
        for p, s in zip(photos, rec0["sides"]):
            rid = REAL_REFS[p]
            q = project_mm(PO.chart_to_posed([P], s, hz, real_basis.pose(rid)), real_basis.camera(rid),
                           real_basis.frame)[0]
            edges = []
            for e in (e1, e2):
                c, d = np.asarray(REAL_EDGES[p][e][0]), np.asarray(REAL_EDGES[p][e][1])
                nrm = np.array([-d[1], d[0]]) / np.linalg.norm(d)
                edges.append((np.asarray(real_edges(p, e)) + ((q - c) @ nrm) * nrm).tolist())
            exact.append({"photo": p, "edges": edges})
        fired = 0
        for _ in range(60):
            r = RD.read_point(tb, real_basis, "E", model_noise(real_basis, exact, REAL_REFS, rng), kind="crossing",
                              refs=REAL_REFS)
            fired += "W_RESIDUAL" in codes(r)
        assert fired <= 2, (e1, e2, fired)
