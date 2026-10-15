"""Final-design splice facts: no Blender, geometry repair, network, or appearance gate."""
import copy
import math

import pytest

from glasses3d.agent.temple_lab import geometry as G
from glasses3d.g3d.temple import fixtures as FX


def design():
    s = FX.slab()
    for chain, value in (("outline.top", 10.0), ("outline.bottom", -10.0),
                         ("inner", 60.0), ("base.top", 5.0), ("base.bottom", 5.0)):
        G._set_chain(s, chain, [[0.0, value], [20.0, value], [40.0, value], [60.0, value]])
    return s


def splice(chain="outline.top", points=None, lo=20.0, hi=40.0):
    return {"op": "splice_chain", "chain": chain, "d_from_mm": lo, "d_to_mm": hi,
            "points": points if points is not None else [[20.0, 13.0], [40.0, 13.0]]}


@pytest.mark.parametrize("chain", G.CHAINS)
def test_splice_join_reports_real_final_ramp_and_turn_for_every_chain(chain):
    s = design()
    untouched = copy.deepcopy(s)
    y = G._chain(s, chain)[0][1]
    facts = {}
    out, notes = G.apply_edits(s, [splice(chain, [[20.0, y + 3], [40.0, y + 3]])], facts=facts)
    assert s == untouched
    assert G._chain(out, chain) == [[0.0, y], [20.0, y + 3], [40.0, y + 3], [60.0, y]]
    joins = facts["splice_joins"]
    assert len(joins) == 2
    for row, rise in zip(joins, (3, -3)):
        assert row["chain"] == chain and row["value_change_mm"] == 3
        assert row["ramp_run_mm"] == 20 and row["ramp_rise_mm"] == rise
        assert row["slope_deg"] == pytest.approx(math.degrees(math.atan(rise / 20)))
        assert row["turn_deg"] == pytest.approx(math.degrees(math.atan(3 / 20)))
        assert row["message"] in notes and "joining ramp" in row["message"]
        assert "discontinuity" not in row["message"]


def test_report_uses_last_offset_values_and_current_adjacent_knots():
    facts = {}
    offset = {"op": "offset_chain", "chain": "outline.top", "d_from_mm": 10.0,
              "d_to_mm": 40.0, "delta_mm": 2.0, "taper_mm": 10.0}
    out, notes = G.apply_edits(design(), [splice(), offset], facts=facts)
    row = facts["splice_joins"][0]
    assert row["d_mm"] == 20 and row["value_mm"] == 15 and row["value_change_mm"] == 5
    assert row["ramp_d_mm"] == [10, 20]       # offset inserted the final neighbour at 10
    assert row["ramp_rise_mm"] == 1.5 and row["ramp_run_mm"] == 10
    assert "final value 15.00 mm" in notes[0]
    assert G._chain(out, "outline.top")[1] == [10.0, 13.5]


def test_later_splice_restoration_suppresses_stale_notes():
    facts = {}
    out, notes = G.apply_edits(design(), [splice(), splice(points=[[20, 10], [40, 10]])], facts=facts)
    assert facts["splice_joins"] == [] and notes == []
    assert G._chain(out, "outline.top") == G._chain(design(), "outline.top")


def test_later_splice_replaces_old_boundaries_and_reports_only_new_join():
    facts = {}
    _, notes = G.apply_edits(design(), [splice(), splice(points=[[15, 14], [45, 14]], lo=10, hi=50)],
                            facts=facts)
    assert {r["d_mm"] for r in facts["splice_joins"]} == {15, 45}
    assert {r["edit_index"] for r in facts["splice_joins"]} == {1}
    assert all("edits[1]" in n for n in notes)


def test_full_replacement_and_unchanged_natural_corners_have_no_external_join_warning():
    s = design()
    G._set_chain(s, "outline.top", [[0, 10], [20, 13], [40, 9], [60, 10]])
    for op in (splice(points=[[20, 13], [40, 9]]),
               splice(points=[[0, 10], [30, 15], [60, 10]], lo=0, hi=60)):
        facts = {}
        _, notes = G.apply_edits(s, [op], facts=facts)
        assert facts["splice_joins"] == [] and notes == []


def test_deletion_bridge_reports_actual_new_join_without_reinserting_knots():
    s = design()
    G._set_chain(s, "inner", [[0, 60], [10, 60], [20, 60], [30, 64], [40, 64], [60, 64]])
    facts = {}
    out, _ = G.apply_edits(s, [splice("inner", [], lo=15, hi=35)], facts=facts)
    assert G._chain(out, "inner") == [[0, 60], [10, 60], [40, 64], [60, 64]]
    rows = facts["splice_joins"]
    assert len(rows) == 2 and {r["d_mm"] for r in rows} == {10, 40}
    assert all(r["ramp_run_mm"] == 30 and r["ramp_rise_mm"] == 4 for r in rows)


def test_original_restored_by_offset_and_unrelated_scalar_edits_do_not_leave_splice_notes():
    offset = {"op": "offset_chain", "chain": "outline.top", "d_from_mm": 20.0,
              "d_to_mm": 40.0, "delta_mm": -3.0, "taper_mm": 0.0}
    facts = {}
    _, notes = G.apply_edits(design(), [splice(), offset, {"op": "set_scalar", "key": "roll_mm", "value": 0}],
                            facts=facts)
    assert facts["splice_joins"] == [] and notes == []


def test_reused_facts_do_not_retain_prior_splice_report():
    facts = {}
    G.apply_edits(design(), [splice()], facts=facts)
    assert facts["splice_joins"]
    G.apply_edits(design(), [], facts=facts)
    assert facts == {"outline_bends": [], "deleted_creases": []}
