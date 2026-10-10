"""g3d.temple/1 -> /2 conversion (plan/WP6_FACETS.md §2.3, §5 T1): the formulas, the surface equivalence with the v1
builder (snapshotted from the local tag wp6-v1-final) by barycentric comparison in both directions over the v1 zone,
and the crease check on Astra's c0003 layout. Tests that need the tag or the data files skip cleanly without them."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple, report
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple.convert import parse_v1, v1_to_v2
from glasses3d.g3d.temple.planes import crease_frame
from glasses3d.g3d.temple.spec import TempleError, parse

G3D = Path(__file__).resolve().parents[1]
REPO = G3D.parent
PREFERRED = G3D / "data" / "wp6" / "pose_arm_resume_20261010" / "distal_all_views" / "before" / "spec.json"
PREFERRED_SHA = "cfc11127d085c4a642f4a77fb2d6daae676470ffabe699af55b16fc49ec908ce"
C0003 = (G3D / "data" / "wp6" / "astra_temple_lab_20261010" / "real-astra-001" / "work" / "temple_lab"
         / "candidate-3.json")
C0003_SHA = "e4baea51360ea11d789d660fd7dcc178b9dd6823f8af2bad37dfcb5eee50e049"
V1_TAG = "wp6-v1-final"


def load(path: Path, sha: str) -> dict:
    if not path.exists():
        pytest.skip(f"{path.name} not present (local evidence)")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == sha, f"{path} is not the spec this test is defined on"
    return json.loads(raw)


@pytest.fixture(scope="module")
def v1(tmp_path_factory):
    """The v1 core (spec, arrange, planes, build) from the tag, imported as its own package."""
    pkg = tmp_path_factory.mktemp("v1core") / "v1temple"
    pkg.mkdir()
    for name in ("spec", "arrange", "planes", "build"):
        try:
            src = subprocess.run(["git", "show", f"{V1_TAG}:glasses3d/g3d/temple/{name}.py"], cwd=REPO,
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pytest.skip("git is not available")
        if src.returncode != 0:
            pytest.skip(f"tag {V1_TAG} is not available")
        (pkg / f"{name}.py").write_text(src.stdout, encoding="utf-8")
    (pkg / "__init__.py").write_text('SCHEMA = "g3d.temple/1"\nfrom .build import build_temple\n', encoding="utf-8")
    spec = importlib.util.spec_from_file_location("v1temple", pkg / "__init__.py",
                                                  submodule_search_locations=[str(pkg)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v1temple"] = mod
    spec.loader.exec_module(mod)
    yield mod
    for k in [k for k in sys.modules if k == "v1temple" or k.startswith("v1temple.")]:
        del sys.modules[k]


def v1_pyramid() -> dict:
    """A v1 spec on the fixture outline: the Prada pyramid plus F1 by crease A-B + rise, a virtual node beyond the
    zone end (on F4's polygon) and one before the hinge-end cap."""
    return {"schema": "g3d.temple/1", "hinge": copy.deepcopy(FX.HINGE),
            "outline": {"top": copy.deepcopy(FX.TOP), "bottom": copy.deepcopy(FX.BOTTOM), "tip_round_mm": 0.0},
            "inner": copy.deepcopy(FX.INNER), "thickness": copy.deepcopy(FX.THICK), "zone_end_d": 65.0,
            "nodes": {"A": [0.0, 10.5, 0.0], "B": [46.8, 9.52, 0.0], "C": [22.6, -16.3, 3.0],
                      "E": [55.0, FX._yb(55.0), 0.0], "H": [0.0, -9.3, 0.5], "H_lo": [0.0, -40.0, None],
                      "C_lo": [22.6, -40.0, None], "E_lo": [55.0, -40.0, None], "Z": [70.0, -40.0, -2.0]},
            "facets": {"F2": ["A", "B", "C"], "F3": ["A", "H", "H_lo", "C_lo", "C"],
                       "F4": ["B", "C", "C_lo", "E_lo", "E"]},
            "chamfers": [], "roll_mm": 0.0, "options": {"crease_sharp_deg": 6.0}}


def outer_tris(tb, kinds=("outer", "roll")):
    sel = np.array([k in kinds for k in tb.poly_kind])[tb.tri_poly]
    return tb.tris[sel]


def lookup(V, T, pts) -> np.ndarray:
    """X of the piecewise-linear surface (V, T) at (d, y) points by barycentric interpolation (nan outside)."""
    A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    e0, e1 = B[:, :2] - A[:, :2], C[:, :2] - A[:, :2]
    den = e0[:, 0] * e1[:, 1] - e0[:, 1] * e1[:, 0]
    out = np.full(len(pts), np.nan)
    for i, p in enumerate(pts):
        w = p - A[:, :2]
        a = (w[:, 0] * e1[:, 1] - w[:, 1] * e1[:, 0]) / den
        b = (e0[:, 0] * w[:, 1] - e0[:, 1] * w[:, 0]) / den
        ok = np.nonzero((a >= -1e-9) & (b >= -1e-9) & (a + b <= 1 + 1e-9))[0]
        if len(ok):
            k = ok[0]
            out[i] = (1 - a[k] - b[k]) * A[k, 2] + a[k] * B[k, 2] + b[k] * C[k, 2]
    return out


def zone_deviation(t1, t2, zone_end: float) -> tuple[float, float, int, int]:
    """Max |v2 - v1| at v1's outer vertices and |v1 - v2| at v2's, over d <= zone_end."""
    T1, T2 = outer_tris(t1, ("outer",)), outer_tris(t2)
    m1 = np.unique(T1)
    m1 = m1[t1.V[m1, 0] <= zone_end]
    m2 = np.unique(T2)
    m2 = m2[t2.V[m2, 0] <= zone_end]
    a = lookup(t2.V, T2, t1.V[m1, :2]) - t1.V[m1, 2]
    b = lookup(t1.V, T1, t2.V[m2, :2]) - t2.V[m2, 2]
    assert not np.isnan(a).any() and not np.isnan(b).any()
    return float(np.max(np.abs(a))), float(np.max(np.abs(b))), len(m1), len(m2)


# --------------------------------------------------------------------------------------------- formulas
def test_conversion_formulas():
    s1 = v1_pyramid()
    s1["facets"]["F1"] = {"poly": ["A", "B", "G", "G_hi", "A_hi"],
                          "plane": {"crease": ["A", "B"], "rise_per_mm": -0.25}}
    s1["nodes"].update({"G": [20.0, FX._yt(20.0), None], "G_hi": [20.0, 40.0, None], "A_hi": [0.0, 40.0, None]})
    s2 = v1_to_v2(s1)
    assert s2["schema"] == "g3d.temple/2" and "zone_end_d" not in s2 and "thickness" not in s2
    assert s2["base"]["top"] == s2["base"]["bottom"] == FX.THICK
    assert s2["options"] == {"snap_mm": 0.0} and v1_to_v2(s1, snap_mm=None)["options"] == {}
    x_lin = lambda d: 65.5 - 3.7 * d / 65.0
    ps = parse(s2)
    for name in ("A", "C", "H", "Z"):
        d, y, r = s1["nodes"][name]
        assert ps.inner(d) + s2["nodes"][name][2] == pytest.approx(x_lin(d) + 5.0 + r, abs=1e-12)
    assert s2["nodes"]["C"][2] == pytest.approx(8.0, abs=1e-12)          # inside the zone: t = T + r
    assert s2["nodes"]["Z"][2] != pytest.approx(5.0 - 2.0, abs=1e-3)     # beyond it the v2 inner table differs
    assert s2["nodes"]["G"][2] is None
    _, n_in, _ = crease_frame(s1["nodes"]["A"], s1["nodes"]["B"], True)
    assert s2["facets"]["F1"]["plane"]["rise_per_mm"] == pytest.approx(-0.25 + (-3.7 / 65.0) * n_in[0], abs=1e-15)
    assert all(f["fade"] is None for f in s2["facets"].values()) and s2["creases"] == []


def test_conversion_refusals():
    with pytest.raises(TempleError) as e:
        v1_to_v2(FX.slab())
    assert e.value.code == "E_SPEC"
    s1 = v1_pyramid()
    s1["inner"] = [[0.0, 65.5], [30.0, 63.0], [65.0, 61.8], [136.5, 47.1]]
    with pytest.raises(TempleError) as e:
        v1_to_v2(s1)
    assert e.value.code == "E_SPEC" and e.value.fields["knot_d"] == 30.0
    s1 = v1_pyramid()
    s1["options"]["zone"] = 1
    with pytest.raises(TempleError):
        parse_v1(s1)


def test_equivalence_on_a_synthetic_v1_spec(v1):
    s1 = v1_pyramid()
    t1, t2 = v1.build_temple(s1), build_temple(v1_to_v2(s1))
    a, b, n1, n2 = zone_deviation(t1, t2, s1["zone_end_d"])
    assert a <= 1e-6 and b <= 1e-6 and n1 > 100 and n2 > 100


# --------------------------------------------------------------------------------------------- the real specs
def test_equivalence_on_the_owner_preferred_spec(v1):
    pref = load(PREFERRED, PREFERRED_SHA)
    t1, t2 = v1.build_temple(pref), build_temple(v1_to_v2(pref))
    a, b, n1, n2 = zone_deviation(t1, t2, pref["zone_end_d"])
    assert a <= 1e-6 and b <= 1e-6, (a, b)
    assert n1 > 150 and n2 > 150
    c1 = {tuple(c["regions"]): c for c in t1.tables["creases"] if c["kind"] == "crease"}
    c2 = {tuple(c["regions"]): c for c in t2.tables["creases"] if c["kind"] == "crease"}
    assert set(c1) == set(c2)
    for k in c1:
        assert c2[k]["shape"] == c1[k]["shape"]
        assert c2[k]["dihedral_deg"][1] == pytest.approx(c1[k]["dihedral_deg"][1], abs=1e-9)


def test_default_snapping_moves_the_preferred_spec_within_snap():
    pref = load(PREFERRED, PREFERRED_SHA)
    t0, t5 = build_temple(v1_to_v2(pref)), build_temple(v1_to_v2(pref, snap_mm=None))
    assert t0.spec.snaps == []
    assert [s["what"] for s in t5.spec.snaps] == ["node A"]               # A sat 0.024 mm below the corner
    assert abs(t5.spec.snaps[0]["to"] - t5.spec.snaps[0]["from"]) < 0.05
    a, _, _, _ = zone_deviation(t0, t5, pref["zone_end_d"])
    assert 1e-4 < a < 0.05


def test_the_crease_check_refuses_astra_c0003_valley():
    c3 = load(C0003, C0003_SHA)
    s2 = v1_to_v2(c3)
    tb = build_temple(s2)
    assert "logo_plane|upper_plane valley 18.3° undeclared" in report.crease_lines(tb)
    s2["creases"] = [{"facets": ["logo_plane", "upper_plane"], "shape": "ridge"}]
    with pytest.raises(TempleError) as e:
        build_temple(s2)
    f = e.value.fields
    assert e.value.code == "E_CREASE_SHAPE" and f["facets"] == ["logo_plane", "upper_plane"]
    assert f["declared"] == "ridge" and f["built"] == "valley" and round(f["dihedral_deg"], 1) == 18.3
    assert "valley 18.3 deg" in e.value.message
