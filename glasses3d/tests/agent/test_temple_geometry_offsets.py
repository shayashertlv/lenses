"""Offset joins and outline carries must match the precise geometry Astra requested."""
import pytest

from glasses3d.agent.temple_lab import geometry as G
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple.spec import MIN_FEATURE_MM


def test_zero_taper_has_bounded_join_and_reports_both_ramps():
    original = FX.slab()
    facts = {}
    edited, notes = G.apply_edits(original, [{"op": "offset_chain", "chain": "outline.top",
        "d_from_mm": 69., "d_to_mm": 75., "delta_mm": 2., "taper_mm": 0.}], facts=facts)
    before, after = G._chain(original, "outline.top"), G._chain(edited, "outline.top")
    for d in (60., 62., 65., 68., 78., 80.):
        assert G._interp(after, d) == pytest.approx(G._interp(before, d), abs=1e-12)
    for d in (69., 70., 73., 75.):
        assert G._interp(after, d) - G._interp(before, d) == pytest.approx(2.)
    assert len(facts["offset_joins"]) == 2
    for row in facts["offset_joins"]:
        assert row["ramp_run_mm"] == pytest.approx(G.snap_of(original) + MIN_FEATURE_MM)
        assert row["message"] in notes and "offset_chain" in row["message"]


def test_setting_bottom_knot_does_not_carry_nearby_top_node():
    s = FX.slab()
    s["nodes"] = {"A": [30., 15.43, 2.], "C": [60., -4.7, 2.], "K": [45., 0., 2.]}
    s["facets"] = {"F": {"poly": ["A", "C", "K"], "plane": "nodes", "fade": None}}
    out, notes = G.apply_edits(s, [{"op": "set_node", "name": "C", "d_mm": 60., "y_mm": -5.5,
        "t": {"mode": "mm", "mm": 2.}, "outline": "bottom"}])
    assert out["nodes"]["A"] == s["nodes"]["A"]
    assert G._chain(out, "outline.top") == G._chain(s, "outline.top")
    assert all("node A moved" not in n for n in notes)
