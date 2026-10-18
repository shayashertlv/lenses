"""A1 (WP5_DESIGN §6.1, §8): the numpy print-UV core ``g3d/printuv`` on q001 and on synthetic parts with known answers:
0 overlaps, 0 flips, anisotropy p95 <= 2, density p1 >= 0.6 x target, mirror partners index-identical; numpy only."""
from glasses3d.tests.fixtures import camera_products

import ast
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d import printuv as PU
from glasses3d.g3d.printuv import charts as CH
from glasses3d.g3d.printuv import check as CK
from glasses3d.g3d.printuv import mirror as MR
from glasses3d.g3d.printuv import pack as PK

PKG = Path(__file__).resolve().parents[2] / "g3d" / "printuv"
REPO = Path(__file__).resolve().parents[3]
PARTS = ("Brow_Left", "Brow_Right", "Temple_Left", "Temple_Right")


@pytest.fixture(scope="module")
def q001(ft1119_fixture):
    from glasses3d.g3d_host import fitmesh
    return fitmesh.load_fit_mesh(ft1119_fixture['model'], skip=camera_products.PROTOTYPE_SKIP)


@pytest.mark.parametrize("tx", [10.0, 16.0])
def test_q001_layout_passes_a1(q001, tx):
    lay = PU.layout(q001.V, q001.F, q001.part, q001.names, PARTS, tx)
    rep = lay.report
    assert rep["pass"], rep["criteria"]
    assert rep["overlap_texels"] == 0 and rep["flipped_faces"] == 0 and rep["zero_area_faces"] == 0
    assert rep["anisotropy_p50_p95_p99"][1] <= 2.0 and rep["density_p1_share"] >= 0.6
    assert lay.width <= 4096 and lay.height <= 4096 and lay.tx_per_mm == tx
    # q001's right parts are its left parts mirrored (a few end faces triangulated differently)
    assert lay.partners == {"Brow_Right": "Brow_Left", "Temple_Right": "Temple_Left"}
    for b, p in rep["partners"].items():
        assert p["pass"] and p["geometry_max_err_mm"] <= 0.01 and p["max_abs_texel"] < 1e-6, (b, p)
        assert p["index_copied"] + p["retriangulated_vertex_copied"] == p["faces"]
        assert p["retriangulated_vertex_copied"] <= 0.01 * p["faces"]
    assert sorted(set(lay.faces.tolist())) == sorted(lay.faces.tolist())        # every face once
    uv01 = lay.uv01()
    assert uv01.min() >= 0 and uv01.max() <= 1


def _tube(n_s=40, n_t=16, length=60.0, r=3.0, x0=10.0, bend=8.0):
    """A closed bent tube along x (a synthetic brow) with end caps, outward counter-clockwise faces."""
    s = np.linspace(0, 1, n_s)
    th = np.linspace(0, 2 * np.pi, n_t, endpoint=False)
    V = []
    for si in s:
        cx, cz = x0 + length * si, -bend * (2 * si - 1) ** 2
        for t in th:
            V.append([cx, r * np.cos(t), cz + r * np.sin(t)])
    V = np.asarray(V)
    F = []
    for i in range(n_s - 1):
        for j in range(n_t):
            a, b = i * n_t + j, i * n_t + (j + 1) % n_t
            c, d = a + n_t, b + n_t
            F += [[a, c, b], [b, c, d]]
    c0 = len(V)
    V = np.vstack([V, [[x0, 0, -bend]], [[x0 + length, 0, -bend]]])
    for j in range(n_t):
        F.append([c0, j, (j + 1) % n_t])
        k = (n_s - 1) * n_t
        F.append([c0 + 1, k + (j + 1) % n_t, k + j])
    F = np.asarray(F)
    # orient outward: the normal must point away from the local axis point
    P = V[F]
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    cen = P.mean(1)
    ax = np.stack([cen[:, 0], np.zeros(len(F)), -bend * (2 * (cen[:, 0] - x0) / length - 1) ** 2], 1)
    flip = (n * (cen - ax)).sum(1) < 0
    F[flip] = F[flip][:, ::-1]
    return V, F


def _pair(perturb=0.0, flip_quad=False):
    V, F = _tube()
    Vm = V * np.array([-1.0, 1.0, 1.0])
    Fs = F.copy()
    if flip_quad:                                   # one quad split along the other diagonal on the mirror
        f0, f1 = list(Fs[0]), list(Fs[1])
        shared = set(f0) & set(f1)
        k = [i for i in range(3) if f0[i] not in shared][0]
        a, x, y = f0[k], f0[(k + 1) % 3], f0[(k + 2) % 3]      # quad cycle a -> x -> d -> y
        d = [v for v in f1 if v not in shared][0]
        Fs[0], Fs[1] = [a, x, d], [a, d, y]
    Fm = Fs[:, ::-1].copy()
    Vm = Vm + perturb
    VV = np.vstack([V, Vm])
    FF = np.vstack([F, Fm + len(V)])
    part = np.r_[np.zeros(len(F), int), np.ones(len(Fm), int)]
    return VV, FF, part


def test_mirror_partner_is_an_index_copy():
    V, F, part = _pair()
    lay = PU.layout(V, F, part, ["A", "B"], ["A", "B"], 12.0)
    assert lay.partners == {"B": "A"}
    p = lay.report["partners"]["B"]
    assert p["pass"] and p["index_copied"] == p["faces"] and p["retriangulated_vertex_copied"] == 0
    assert lay.report["pass"], lay.report["criteria"]
    # B's charts are A's mirrored, in their own atlas boxes (their own texels)
    assert len([c for c in lay.charts if c["part"] == "B"]) == len([c for c in lay.charts if c["part"] == "A"])


def test_retriangulated_quad_is_vertex_copied():
    V, F, part = _pair(flip_quad=True)
    lay = PU.layout(V, F, part, ["A", "B"], ["A", "B"], 12.0)
    p = lay.report["partners"]["B"]
    assert lay.partners == {"B": "A"} and p["pass"] and p["retriangulated_vertex_copied"] == 2
    assert lay.report["criteria"]["flips"] and lay.report["criteria"]["overlaps"]


def test_a_moved_copy_is_not_a_partner():
    V, F, part = _pair(perturb=0.1)
    lay = PU.layout(V, F, part, ["A", "B"], ["A", "B"], 12.0)
    assert lay.partners == {} and lay.report["pass"]


def test_overlap_and_flip_detectors():
    tri = np.array([[[0, 0], [10, 0], [0, 10]], [[2, 2], [12, 2], [2, 12]]], float)      # overlapping
    covered, ov = CK.overlap_texels(tri)
    assert covered > 0 and ov > 0
    assert len(CH.overlapping_triangles(tri)) == 2
    shared = np.array([[[0, 0], [10, 0], [0, 10]], [[10, 0], [10, 10], [0, 10]]], float)  # share an edge
    assert CK.overlap_texels(shared)[1] == 0
    V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], float)
    F = np.array([[0, 1, 2]])
    good = np.array([[[0, 0], [10, 0], [0, -10]]], float)        # seen from outside: negative area (v down)
    bad = good[:, ::-1]
    assert CK.a1_report(V, F, np.array([0]), good, 10.0)["flipped_faces"] == 0
    r = CK.a1_report(V, F, np.array([0]), bad, 10.0)
    assert r["flipped_faces"] == 1 and not r["pass"]


def test_cap_chart_splits_a_self_overlapping_fold():
    # two connected strips both facing +z, one above the other (a fold): one planar chart would overlap itself
    V = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [10, 10, 0],
                  [0, 0, 2], [10, 0, 2], [0, 10, 2], [10, 10, 2]], float)
    F = np.array([[0, 1, 2], [1, 3, 2], [4, 5, 6], [5, 7, 6], [2, 3, 6], [3, 7, 6]])
    F[4:] = F[4:][:, ::-1]
    charts, info = CH.part_charts(V, F, np.arange(len(F)), kind="caps", tx=4.0)
    uvs = [uv for _k, _f, uv in charts]
    for uv in uvs:
        assert CK.overlap_texels(uv * 4.0)[1] == 0
    assert sum(len(f) for _k, f, _uv in charts) == len(F)


def test_pack_gutters_and_density_fit():
    rng = np.random.default_rng(0)
    charts = []
    for _ in range(12):
        w, h = rng.uniform(5, 40), rng.uniform(5, 20)
        charts.append(np.array([[[0, 0], [w, 0], [0, h]], [[w, 0], [w, h], [0, h]]], float))
    out = PK.pack(charts, 10.0)
    boxes = [(int(o[0]), int(o[1]), b[0], b[1]) for o, b in zip(out["offsets"], out["boxes"])]
    for i, (x, y, w, h) in enumerate(boxes):
        assert x >= PK.GUTTER and y >= PK.GUTTER and x + w + PK.GUTTER <= out["width"]
        for j, (x2, y2, w2, h2) in enumerate(boxes[:i]):
            sep = max(x2 - (x + w), x - (x2 + w2), y2 - (y + h), y - (y2 + h2))
            assert sep >= PK.GUTTER, (i, j)
    small = PK.fit_density(charts, 400.0, atlas_max=1024)
    assert small["width"] <= 1024 and small["height"] <= 1024 and small["tx_per_mm"] < 400.0


def test_printuv_is_numpy_only():
    """numpy plus the standard library (glbmesh, step 4: hashlib / json / re / struct / pathlib to read a GLB and hash
    the identities); never bpy, scipy, open3d, cv2 or PIL, so the same code runs in Blender and on the host."""
    allowed = {"numpy", "math", "dataclasses", "__future__", "hashlib", "json", "re", "struct", "pathlib"}
    for path in PKG.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:                        # relative import inside the package
                    continue
                mods = [(node.module or "").split(".")[0]]
            else:
                continue
            assert set(mods) <= allowed, (path.name, mods)
    code = ("import sys; import glasses3d.g3d.printuv; "
            "bad = [k for k in ('scipy', 'open3d', 'bpy', 'cv2', 'PIL') if k in sys.modules]; print(bad); "
            "sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(REPO), capture_output=True, text=True,
                       env={**__import__("os").environ, "PYTHONPATH": str(REPO)})
    assert r.returncode == 0, r.stdout + r.stderr


def test_components_match_a_reference():
    a = np.array([0, 1, 3, 5, 6])
    b = np.array([1, 2, 4, 6, 7])
    lab = CH.components(9, a, b)
    assert lab[0] == lab[1] == lab[2] and lab[3] == lab[4] and lab[5] == lab[6] == lab[7]
    assert len({lab[0], lab[3], lab[5], lab[8]}) == 4
    assert math.isclose(MR.MIRROR_TOL_MM, 0.01)
