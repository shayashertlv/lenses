"""WP6 g3d.faceted_temple spec: canonical form, hash, validation and the refusals raised while parsing (§3)."""
from __future__ import annotations

import copy
import json
import math

import numpy as np
import pytest

from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import spec as S
from glasses3d.g3d.temple.spec import TempleError


def test_canonical_fills_defaults_sorts_and_expands_shorthand():
    s = FX.pyramid()
    del s["options"], s["chamfers"], s["roll_mm"]
    c = S.canonical(s)
    assert c["options"] == S.DEFAULT_OPTIONS
    assert c["chamfers"] == [] and c["roll_mm"] == 0.0
    assert list(c["nodes"]) == sorted(c["nodes"])
    assert list(c["facets"]) == sorted(c["facets"])
    assert c["facets"]["F2"] == {"poly": ["A", "B", "C"], "plane": "nodes"}
    assert c["outline"]["tip_round_mm"] == 0.0


def test_hash_is_stable_and_sensitive():
    a = FX.pyramid()
    b = copy.deepcopy(a)
    b["nodes"] = dict(reversed(list(b["nodes"].items())))           # key order does not matter
    assert S.spec_sha256(a) == S.spec_sha256(b)
    assert S.canonical_json(a) == S.canonical_json(b)
    c = copy.deepcopy(a)
    c["nodes"]["C"][2] += 1e-9
    assert S.spec_sha256(c) != S.spec_sha256(a)
    json.loads(S.canonical_json(a))


def test_unknown_keys_and_bad_types_are_e_spec():
    for mut in (lambda s: s.update(colour="red"),
                lambda s: s["hinge"].update(axis="x"),
                lambda s: s["nodes"].update(Z=[1, 2]),
                lambda s: s["facets"].update(X={"poly": ["A", "B"]}),
                lambda s: s["facets"].update(X={"poly": ["A", "B", "Q"]}),
                lambda s: s["facets"].update(X={"poly": ["A", "B", "C"], "plane": {"crease": ["A", "Q"],
                                                                                  "rise_per_mm": 0.1}}),
                lambda s: s.update(options={"t_min_mm": -1}),
                lambda s: s.update(roll_mm=-0.5),
                lambda s: s.update(zone_end_d="sixty")):
        s = FX.pyramid()
        mut(s)
        with pytest.raises(TempleError) as e:
            S.parse(s)
        assert e.value.code == "E_SPEC", (e.value.code, e.value.message)
        assert e.value.to_dict()["status"] == "refused"


def test_crease_must_be_an_edge_of_its_polygon():
    s = FX.crease_rise()
    s["facets"]["F1"]["plane"]["crease"] = ["A", "G"]               # A-G is a diagonal of A-B-G-G_hi-A_hi
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_SPEC" and "edge" in e.value.message


@pytest.mark.parametrize("code", ["E_SPEC", "E_OUTLINE", "E_OUTLINE_NOT_SINGLE_VALUED", "E_OUTLINE_NOT_SIMPLE",
                                  "E_ZONE", "E_CHAMFER"])
def test_parse_refusals_fire_with_fields(code):
    spec = FX.refusal_cases()[code]
    with pytest.raises(TempleError) as e:
        S.parse(spec)
    assert e.value.code == code
    assert isinstance(e.value.fields, dict)
    json.dumps(e.value.to_dict())


def test_zone_refusal_names_the_knot():
    with pytest.raises(TempleError) as e:
        S.parse(FX.refusal_cases()["E_ZONE"])
    assert e.value.fields["knot_d"] == 30.0 and e.value.fields["table"] == "inner"
    assert e.value.fields["misfit_mm"] > 0.5


def test_tip_round_arcs_follow_the_round_corner_rule():
    s = FX.slab()
    s["outline"]["tip_round_mm"] = 1.5
    ps = S.parse(s)
    for chain in (ps.top, ps.bottom):
        assert np.all(np.diff(chain[:, 0]) > 0)                     # still single valued
        assert chain[-1, 0] == 136.5
    # arc vertices lie on a circle of radius 1.5 and there are >= 8 segments per 90 deg
    top = ps.top
    n_arc = len(top) - (len(FX.TOP) - 1)
    assert n_arc >= 9
    arc = top[-n_arc:]
    u = np.array(FX.TOP[-1]) - np.array(FX.TOP[-2])
    theta = math.acos(np.clip(u @ np.array([0.0, -1.0]) / np.linalg.norm(u), -1, 1))
    assert n_arc - 1 >= max(8, math.ceil(8 * math.degrees(theta) / 90 - 1e-9))
    # every arc vertex is 1.5 mm from one centre
    A, B, C = arc[0], arc[len(arc) // 2], arc[-1]
    M = np.array([[2 * (B - A)[0], 2 * (B - A)[1]], [2 * (C - A)[0], 2 * (C - A)[1]]])
    ctr = np.linalg.solve(M, np.array([B @ B - A @ A, C @ C - A @ A]))
    assert np.allclose(np.linalg.norm(arc - ctr, axis=1), 1.5, atol=1e-9)


def test_tip_round_too_large_is_refused():
    s = FX.slab()
    s["outline"]["tip_round_mm"] = 9.0
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_OUTLINE"


def test_opening_needs_an_axis():
    s = FX.slab()
    s["hinge"] = {"z_mm": -10.6, "axis_xz_mm": None, "opening_deg": 3.0}
    with pytest.raises(TempleError) as e:
        S.parse(s)
    assert e.value.code == "E_SPEC"


def test_chamfer_width_tapers_to_zero_at_both_ends():
    ch = {"edge": "top", "d": [70.0, 125.0], "width_mm": 1.2, "depth_mm": 0.8, "taper_mm": 4.8}
    w = S.chamfer_width(ch, np.array([69.0, 70.0, 72.4, 74.8, 100.0, 120.2, 125.0, 126.0]))
    assert np.allclose(w, [0, 0, 0.6, 1.2, 1.2, 1.2, 0, 0])
    assert S.canonical(FX.top_chamfer_arm())["chamfers"][0]["taper_mm"] == pytest.approx(4.8)
