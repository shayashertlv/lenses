"""WP4 tools stage, review fix round 1, on the host (numpy core): hide_eyewire's post-checks (exact sections in the
carried pre-move planes, the band at every graded station), the rings spanning a selected station, the lens follow,
the declared-range union, capture_mm and the check's pass floor, and the lazy tool exports without bpy.
WP4_DESIGN.md §4, §6, §8; the Blender half is in test_seat_blender.py."""
import json

import numpy as np
import pytest

import glasses3d.g3d as g3d
from glasses3d.g3d.seat import check as CK
from glasses3d.g3d.seat import deform, geom, rules, stations
from glasses3d.g3d.seat import plan as PL
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.g3d_host.seat_check import check_glb
from glasses3d.tests.fixtures import seat_fixtures as SF


def factory(parts):
    return NumpyBackend(parts)


def _node(i, name, V, T, lens=False):
    return {"node": i, "name": name, "V": V, "T": T, "N": np.full_like(V, np.nan), "roles": ["lens" if lens else "frame"],
            "lens": lens, "translucent": False, "extras": {}}


def _lift(sc, rng):
    L, ew, brow = sc["lens"], sc["parts"]["Eyewire"], sc["parts"]["Brow"]
    return deform.lift_arrays(ew.V, ew.T, geom.edge_table(ew.T).edges, [("Brow", brow.V, brow.T)], [], L.V, L.T,
                              factory, rng, lens_poly_edges_raw=L.poly_edges, lens_name="Lens", eyewire_name="Eyewire")


# --------------------------------------------------------------------------------------------- section area
def test_section_area_is_exact_and_shift_invariant():
    """The eyewire's section in the station planes is its 0.90 x 2.00 mm rectangle (flat caps: the station plane is
    the sweep's section plane up to the chord direction), and an in-plane shift by a fraction of the band's
    0.025 mm column changes nothing (column sampling jumped by up to one column, 0.05 mm2 = 2.8% here)."""
    sc = SF.browline_scene()
    L, ew = sc["lens"], sc["parts"]["Eyewire"]
    rec = stations.recognise(L.V, L.T, "Lens")
    fr = stations.station_frames(rec.bodies[0])
    top = np.flatnonzero(fr.u[:, 1] > 0.99)
    a0 = deform.section_area(fr, top, ew.V, ew.T)
    assert np.allclose(a0, 1.80, rtol=2e-3), (a0.min(), a0.max())
    shifted = ew.V + np.array([0.0, 0.0137, 0.0])                   # along u at the top stations, sub-column
    a1 = deform.section_area(fr, top, shifted, ew.T)
    assert np.abs(a1 - a0).max() <= 1e-4 * a0.max(), np.abs(a1 - a0).max()


def test_carried_frames_follow_the_lens_and_keep_the_pre_move_axes():
    sc = SF.browline_scene()
    L = sc["lens"]
    rec = stations.recognise(L.V, L.T, "Lens")
    body = rec.bodies[0]
    fr = stations.station_frames(body)
    D = np.zeros_like(body.V)
    D[:, 1] = 0.3                                                   # a uniform move: every corner by 0.3 y
    body1 = deform._with_V(body, body.V + D)
    band = deform.lens_band(body, fr, PL.node_points(fr, body.V))
    idx = np.arange(0, len(fr), 7)
    frc = deform.carried_frames(fr, body, body1, band.partner, idx)
    assert np.allclose(frc.P - fr.P[idx], [0, 0.3, 0]) and np.allclose(frc.Pb - fr.Pb[idx], [0, 0.3, 0])
    assert np.array_equal(frc.u, fr.u[idx]) and np.array_equal(frc.n, fr.n[idx]) and np.array_equal(frc.t, fr.t[idx])


# ------------------------------------------------------------------------------------- rings and graded band
@pytest.mark.slow
@pytest.mark.parametrize("end_shift", [6.0, 15.0])
def test_a_range_ending_mid_brow_is_lifted_in_full(end_shift):
    """A range that ends under the brow (the review's q001 [11.6, 30.0] case on the fixture): every ring that
    holds an end of an eyewire edge spanning a selected station is lifted with its own lambda*, so the band is 0 at
    every graded station of the moved scene, which the binding check confirms."""
    sc = SF.browline_scene()
    x0, x1 = sc["range"]
    rng = [(x0, x1 - end_shift)]
    res = _lift(sc, rng)
    assert res.status == "lifted", (res.code, res.message)
    rep = res.report
    assert rep["cover_rings"] > 0
    band = rep["band_after"]
    assert band["graded_points"] > 0 and band["graded_visible_points"] == 0
    assert band["front_max_mm"] == 0 and band["back_max_mm"] == 0 and band["area_outside_max_mm2"] < 0.005
    assert rep["section_area_rel_max"] <= 0.001
    assert rep["eyewire_capture_change_max_mm"] <= 0.005             # the follow correction (§6 step 5)
    L, ew, brow = sc["lens"], sc["parts"]["Eyewire"], sc["parts"]["Brow"]
    nodes = [_node(0, "Lens", L.V + res.lens_result.raw_delta, L.T, True), _node(1, "Brow", brow.V, brow.T),
             _node(2, "Eyewire", ew.V + res.eyewire_delta, ew.T)]
    chk = CK.check_nodes(nodes, [], [], factory, "browline", None, rng)
    g = chk["lenses"][0]["band"]["graded"]
    assert g["ok"] and g["front_max_mm"] == 0 and g["back_max_mm"] == 0, g


@pytest.mark.slow
def test_the_post_check_grades_every_station_not_only_the_nodes(monkeypatch):
    """Without the spanning rings the eyewire between the last selected ring and the next is lifted part way; the
    post-check now grades every selected / graded station and refuses (before, it graded the nodes only and reported
    'lifted' on a scene the check fails)."""
    monkeypatch.setattr(PL, "_covering_rings", lambda *a, **k: np.zeros(0, np.int64))
    sc = SF.browline_scene()
    x0, x1 = sc["range"]
    res = _lift(sc, [(x0, x1 - 6.0)])
    assert res.status == "refused" and res.code == "E_POSTCHECK", (res.status, res.message)
    assert "graded stations of the moved scene" in res.message
    assert res.eyewire_delta is None and res.lens_result is None


def test_covering_rings_are_the_ends_of_edges_spanning_a_station():
    ring_of = np.repeat(np.arange(4), 2)                            # 4 rings of 2 vertices, 1 mm apart, 4 mm loop
    s = ring_of.astype(float)
    E = [(2 * r, 2 * r + 1) for r in range(4)]                       # ring edges: span nothing
    E += [(2 * r + k, (2 * r + 2 + k) % 8) for r in range(4) for k in (0, 1)]
    E = np.array(E)
    assert PL._covering_rings(s, ring_of, E, np.array([1.5]), 4.0).tolist() == [1, 2]
    assert PL._covering_rings(s, ring_of, E, np.array([3.8]), 4.0).tolist() == [0, 3]          # across the seam
    assert PL._covering_rings(s, ring_of, E, np.array([1.0]), 4.0).tolist() == []              # on a ring: none
    assert PL._covering_rings(s, ring_of, E, np.array([0.2, 2.5]), 4.0).tolist() == [0, 1, 2, 3]
    assert PL._covering_rings(s, ring_of, E, np.array([]), 4.0).tolist() == []


# ------------------------------------------------------------------------------------------ ranges, capture_mm
def test_merge_ranges_unions_the_declared_ranges():
    assert rules.merge_ranges([(-56.2, -11.6), (-30.0, -11.6)]) == [(-56.2, -11.6)]
    assert rules.merge_ranges([(-11.6, -30.0), (-60.0, -50.0)]) == [(-60.0, -50.0), (-30.0, -11.6)]
    assert rules.merge_ranges([(-60.0, -50.0), (-50.0, -40.0)]) == [(-60.0, -40.0)]
    assert rules.merge_ranges([]) == []


@pytest.mark.parametrize("name, capture, ok", [("acetate", 0.0, False), ("acetate", 0.79, False),
                                               ("acetate", 0.80, True), ("acetate", 1.2, True),
                                               ("metal", 0.44, False), ("metal", 0.45, True),
                                               ("browline", 0.2, False), ("semi_rimless", 0.6, True)])
def test_capture_mm_never_puts_pass_below_the_plan_lower_bound(name, capture, ok):
    if ok:
        ft = rules.frame_type(name, capture_mm=capture)
        assert ft.pass_mm >= rules.pass_floor(rules.frame_type(name)) - 1e-9 and ft.target_mm == capture
    else:
        with pytest.raises(ValueError, match="below the PLAN §3 lower bound"):
            rules.frame_type(name, capture_mm=capture)


def test_the_check_never_grades_below_the_plan_lower_bound(tmp_path):
    """A lens with a visible 0.15 mm gap all round fails as acetate, also when its g3d_seat declares the
    thresholds capture_mm=0 used to write (pass -0.20, target 0.0): the declared pass is clamped to 0.60."""
    L = SF.base_lens()
    sc = {"lens": L, "parts": {"Rim": SF.ring_frame(L, -0.15)}}
    for key, extras in (("plain", {"frame_type": "acetate"}),
                        ("low", {"frame_type": "acetate", "pass_mm": -0.2, "target_mm": 0.0})):
        p = tmp_path / f"{key}.glb"
        SF.write_glb(p, SF.scene_nodes(sc, seat_extras=extras))
        rep = check_glb(p)
        lens = rep["lenses"][0]
        assert not rep["ok"] and lens["pass_mm"] == pytest.approx(0.60), (key, lens["pass_mm"], rep["reasons"])
        assert lens["verdicts"].get("FAIL", 0) > 300
        if key == "low":
            assert any("below the PLAN §3 lower bound" in w for w in rep["warnings"])
    # a declared pass ABOVE the default (capture_mm 1.0 -> pass 0.80) is kept
    p = tmp_path / "high.glb"
    SF.write_glb(p, SF.scene_nodes({"lens": L, "parts": {"Rim": SF.ring_frame(L, 0.10)}},
                                   seat_extras={"frame_type": "acetate", "pass_mm": 0.8, "target_mm": 1.0}))
    assert check_glb(p)["lenses"][0]["pass_mm"] == pytest.approx(0.8)


# ----------------------------------------------------------------------------------------------- lazy exports
def test_the_tool_names_are_not_attributes_on_the_host():
    """Without bpy, hasattr is False (AttributeError, caused by the ImportError), so a WP7 check that the tools
    exist can test hasattr inside Blender and SEAT_TOOLS anywhere."""
    pytest.importorskip("numpy")
    try:
        import bpy  # noqa: F401
        pytest.skip("bpy importable here")
    except ImportError:
        pass
    assert g3d.SEAT_TOOLS == ("seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire")
    for name in g3d.SEAT_TOOLS:
        assert not hasattr(g3d, name)
        with pytest.raises(AttributeError) as exc:
            getattr(g3d, name)
        assert isinstance(exc.value.__cause__, ImportError)
    with pytest.raises(AttributeError):
        g3d.not_a_tool  # noqa: B018
    json.dumps(list(g3d.SEAT_TOOLS))
