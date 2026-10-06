"""WP4 host check (g3d_host/seat_check.py) on the fixtures behind the core-stage review findings (WP4_DESIGN.md §3,
§7, §8): no vacuous pass, an open lens never dropped silently, the full-rim thin rule needs a front lip, frame
solids are not filtered by the lens-body thickness, provisional grading of inferred frame types, the browline
sector summary, and the export_and_validate wiring."""
import numpy as np
import pytest

from glasses3d.g3d.seat import report, rules, stations
from glasses3d.g3d_host import blender
from glasses3d.g3d_host import seat_check as SC
from glasses3d.g3d_host.seat_check import check_glb
from glasses3d.tests.fixtures import seat_fixtures as SF


def glb(tmp_path, sc, name="s.glb", **kw):
    p = tmp_path / name
    SF.write_glb(p, SF.scene_nodes(sc, **kw))
    return p


def seated_acetate(inset=0.8):
    L = SF.base_lens()
    return {"lens": L, "parts": {"Rim": SF.ring_frame(L, inset)}}


# ---------------------------------------------------------------------------------- no vacuous pass (review r7)
def test_a_lens_with_no_frame_is_never_ok(tmp_path):
    rep = check_glb(glb(tmp_path, {"lens": SF.base_lens(), "parts": {}}))
    L = rep["lenses"][0]
    assert rep["ok"] is False and L["pass"] is False and L["frame_type"] is None
    assert L["classes"]["EXPOSED"] == L["points"] and L["required"] == 0
    assert any("frame type unknown" in r for r in L["reasons"])
    # declared semi_rimless: zero required stations is a failure, not a pass
    rep = check_glb(glb(tmp_path, {"lens": SF.base_lens(), "parts": {}}, "d.glb"), frame_type="semi_rimless")
    L = rep["lenses"][0]
    assert not rep["ok"] and L["required"] == 0 and any("no required station" in r for r in L["reasons"])


@pytest.mark.parametrize("inset,dz,backed", [(0.10, 6.7, False), (0.8, 5.0, True)])
def test_a_lens_pushed_out_of_its_ring_is_never_ok(tmp_path, inset, dz, backed):
    sc = seated_acetate(inset)
    sc = dict(sc, lens=SF.translated(sc["lens"], (0.0, 0.0, dz)))
    rep = check_glb(glb(tmp_path, sc))
    L = rep["lenses"][0]
    assert L["classes"]["ENCLOSED"] == L["classes"]["LIPPED"] == 0
    assert L["classes"]["BACKED"] + L["classes"]["EXPOSED"] == L["points"]
    assert (L["classes"]["BACKED"] > L["points"] // 2) == backed               # the ring right behind the lens
    assert rep["ok"] is False and L["frame_type"] is None
    rep = check_glb(glb(tmp_path, sc, "a.glb"), frame_type="acetate")
    assert not rep["ok"] and any("EXPOSED/BACKED" in r for r in rep["lenses"][0]["reasons"])
    rep = check_glb(glb(tmp_path, sc, "s.glb"), frame_type="semi_rimless")
    assert not rep["ok"] and any("no required station" in r for r in rep["lenses"][0]["reasons"])


def test_inferred_frame_type_grades_provisionally_but_never_passes(tmp_path):
    p = glb(tmp_path, seated_acetate())
    rep = check_glb(p)
    L = rep["lenses"][0]
    assert L["frame_type"] == "acetate" and L["frame_type_source"] == "inferred" and L["provisional"]
    assert L["graded_pass"] is True and L["pass"] is False and rep["ok"] is False
    assert any("not declared" in r and "would PASS" in r for r in rep["reasons"])
    rep = check_glb(p, frame_type="acetate")
    L = rep["lenses"][0]
    assert rep["ok"] and L["pass"] and L["graded_pass"] and not L["provisional"], rep["reasons"]
    p2 = glb(tmp_path, seated_acetate(), "x.glb", seat_extras={"frame_type": "acetate"})
    assert check_glb(p2)["ok"]


def test_infer_frame_type_needs_a_captured_station():
    assert SC.infer_frame_type(np.array(["EXPOSED"] * 6 + ["BACKED"] * 4, dtype=object)) is None
    assert SC.infer_frame_type(np.array(["NEAR"] * 10, dtype=object)) is None
    assert SC.infer_frame_type(np.array(["EXPOSED"] * 6 + ["LIPPED"] * 4, dtype=object)) == "semi_rimless"


# -------------------------------------------------------------------------- an open lens fails the file (r4 a)
def test_an_open_lens_beside_a_seated_one_fails_the_file(tmp_path):
    sc = seated_acetate()
    right = SF.open_lens(SF.mirrored_lens(), drop=4)
    p = tmp_path / "two.glb"
    SF.write_glb(p, SF.scene_nodes(sc, seat_extras={"frame_type": "acetate"},
                                   extra_nodes=[{"name": "Lens_R", "mesh": right, "role": "lens",
                                                 "material": {"name": "Lens", "transmission": 1.0}}]))
    rep = check_glb(p)
    assert [L["lens"] for L in rep["lenses"]] == ["Lens_L"] and rep["lenses"][0]["pass"]
    assert rep["ok"] is False
    assert [x["code"] for x in rep["lens_problems"]] == ["E_OPEN"] and rep["lens_problems"][0]["node"] == "Lens_R"
    assert any(r.startswith("Lens_R: E_OPEN") for r in rep["reasons"])
    assert rep["riders"] == [] and rep["open_parts"] == []


def test_a_thin_closed_lens_that_is_no_marking_fails(tmp_path):
    thin = SF.mirrored_lens(t=0.2)
    p = tmp_path / "thin.glb"
    SF.write_glb(p, SF.scene_nodes(seated_acetate(), seat_extras={"frame_type": "acetate"},
                                   extra_nodes=[{"name": "Lens_R", "mesh": thin, "role": "lens"}]))
    rep = check_glb(p)
    assert not rep["ok"] and [x["code"] for x in rep["lens_problems"]] == ["E_THIN"]


# ---------------------------------------------------------------- full-rim thin rule needs a front lip (r4 c)
def test_v_ridge_eyewire_without_a_front_lip_fails_as_metal(tmp_path):
    L = SF.base_lens()
    sc = {"lens": L, "parts": {"Eyewire": SF.swept(L, SF.v_ridge_section(-0.4))}}
    rep = check_glb(glb(tmp_path, sc), frame_type="metal", stations_table=True)
    R = rep["lenses"][0]
    cap = np.array([s["cap"] for s in R["stations"]], float)
    p50 = np.nanmedian(cap, axis=0)
    assert p50[0] < -0.2 and p50[2] > 0.3 and p50[4] < -0.2                    # gap front and back, ridge mid
    assert R["classes"]["LIPPED"] == R["points"]                                # held at the front depth...
    assert R["hidden_front_share"] == 0.0                                      # ...but nothing over the front face
    assert R["verdicts"] == {"FAIL": R["points"]} and not rep["ok"]
    assert any("front corner not covered" in r for r in R["reasons"])
    # the same stations are the semi_rimless required set (an INVU-like rail) and pass its thin rule there
    rep = check_glb(glb(tmp_path, sc, "s.glb"), frame_type="semi_rimless")
    S = rep["lenses"][0]
    assert S["required"] == S["points"] and S["pass"] and rep["ok"]


def test_metal_eyewire_with_lips_still_passes(tmp_path):
    L = SF.base_lens()
    sc = {"lens": L, "parts": {"Eyewire": SF.eyewire(L, 0.45)}}
    rep = check_glb(glb(tmp_path, sc), frame_type="metal")
    R = rep["lenses"][0]
    assert rep["ok"] and R["hidden_front_share"] == 1.0, R["reasons"]


# ------------------------------------------------------------- frame solids are not lens bodies (r6 a, r6 b)
def test_nylor_cord_is_a_frame_part(tmp_path):
    sc = SF.nylor_scene()
    rep = check_glb(glb(tmp_path, sc), frame_type="semi_rimless", lower_edge="nylor")
    R = rep["lenses"][0]
    assert R["parts"] == ["Channel", "Cord"] and rep["riders"] == []
    c = stations.split_components(sc["parts"]["Cord"].V, sc["parts"]["Cord"].T, "Cord")[0]
    assert c.closed and c.thickness_eff_mm < stations.BODY_MIN_THICKNESS_MM      # 0.29 < 0.3: thinner than a lens
    assert R["innermost_part_mid"].get("Cord", 0) > 0


def test_round_wire_eyewire_is_measured_not_dropped(tmp_path):
    L = SF.base_lens()
    wire = SF.round_wire(L, 0.55, 0.0)
    c = stations.split_components(wire.V, wire.T, "Wire")[0]
    assert c.closed and 0.25 < c.thickness_eff_mm < 0.3
    rep = check_glb(glb(tmp_path, {"lens": L, "parts": {"Wire": wire}}), frame_type="metal")
    R = rep["lenses"][0]
    assert R["parts"] == ["Wire"] and R["classes"]["EXPOSED"] == 0
    assert R["sectors"]["top"]["mid_depth_p5_p50_p95"][1] == pytest.approx(0.275, abs=0.02)


def test_closed_marking_on_the_lens_is_a_rider_not_a_frame_part(tmp_path):
    sc = SF.metal_scene()
    mark = SF.decal(sc["lens"])
    c = stations.split_components(mark.V, mark.T, "m")[0]
    assert c.closed and c.thickness_eff_mm < SC.MARKING_MAX_MM
    rep = check_glb(glb(tmp_path, sc, extra_nodes=[{"name": "Marking | LOGO", "mesh": mark, "role": "frame"}]),
                    frame_type="metal")
    assert [r["node"] for r in rep["riders"]] == ["Marking | LOGO"] and rep["riders"][0]["closed"]
    assert rep["lenses"][0]["parts"] == ["Eyewire"]
    # the same slab away from every lens is a (thin) frame solid, not a rider
    far = SF.translated(mark, (0.0, 60.0, 0.0))
    rep = check_glb(glb(tmp_path, sc, "f.glb", extra_nodes=[{"name": "Far", "mesh": far, "role": "frame"}]),
                    frame_type="metal")
    assert rep["riders"] == []


# ------------------------------------------------------------------------------- report: rules and node ids
def test_browline_sector_summary_names_each_rule(tmp_path):
    L = SF.base_lens(cap="flat")
    top = SF.arc_mask(L, "top", 0.30)
    sc = {"lens": L, "parts": {"Eyewire": SF.swept(L, SF.rect_section(-0.4, 0.4, -0.6, 1.0)),
                               "Brow": SF.swept(L, SF.rect_section(0.2, 3.5, -2.4, 2.1), top)}}
    rep = check_glb(glb(tmp_path, sc), frame_type="browline", hidden_x_range_mm=[(-45.0, -32.0)],
                    stations_table=True)
    R = rep["lenses"][0]
    t = R["sectors"]["top"]
    assert t["rule"] is None and set(t["by_rule"]) == {"in_ranges", "outside_ranges"}
    assert t["by_rule"]["in_ranges"]["rule"] == "min over depths 0.05-0.95"
    assert t["by_rule"]["outside_ranges"]["rule"] == "mid-depth 0.5"
    rows = [s for s in R["stations"] if s["sector"] == "top"]
    inr = [s["value"] for s in rows if s["rule"] == "deep"]
    assert t["by_rule"]["in_ranges"]["points"] == len(inr)
    assert t["by_rule"]["in_ranges"]["value_p5_p50_p95"][1] == pytest.approx(float(np.median(inr)), abs=1e-4)
    assert R["sectors"]["bottom"]["rule"] == "mid-depth 0.5" and "by_rule" not in R["sectors"]["bottom"]
    assert R["band"]["held_parts"] == ["Eyewire"]


def test_station_rows_name_the_owning_rim_vertex():
    L = SF.base_lens(nseg=64)
    b = stations.recognise(L.V, L.T).bodies[0]
    ring = SF.ring_frame(L, 0.1)
    for body in (b, _reversed_front_loop(b)):
        fr = stations.station_frames(body)
        be = SC.make_backend("numpy", [(ring.V, ring.T), (body.V, body.T)])
        m = stations.measure(fr, be, 1)
        ft = rules.frame_type("acetate")
        rl = rules.rule_values(ft, m)
        rows = report.station_rows(fr, m, rl, rules.verdicts(ft, m, rl), ["Rim"])
        nv = np.array([r["node"] for r in rows])
        assert (nv == fr.node_vertex).all() and set(nv.tolist()) == set(body.fl.tolist())
        seg = np.linalg.norm(np.diff(body.V[np.r_[body.fl, body.fl[:1]]], axis=0), axis=1).max()
        assert np.linalg.norm(fr.P - body.V[nv], axis=1).max() <= seg / 2 + 1e-9  # the nearer rim vertex


def _reversed_front_loop(b):
    import dataclasses
    return dataclasses.replace(b, front_loops=[b.fl[::-1].copy()] + list(b.front_loops[1:]))


# ------------------------------------------------------------------------------- export_and_validate wiring
def test_export_and_validate_returns_the_seating_section(tmp_path, monkeypatch):
    src = glb(tmp_path, seated_acetate(), "src.glb", seat_extras={"frame_type": "acetate"})

    def fake_export(blend, out_glb, timeout_s=300.0):
        if str(blend).endswith("bad.blend"):
            return {"status": "failed", "error": "refused"}
        out_glb.write_bytes(src.read_bytes())
        return {"status": "exported"}
    monkeypatch.setattr(blender, "export_copy", fake_export)
    monkeypatch.setattr("glasses3d.g3d_host.spec.validate_spec", lambda p: {"ok": True, "errors": []})
    res = blender.export_and_validate(tmp_path / "ok.blend", tmp_path / "out.glb")
    assert res["export"]["status"] == "exported" and res["spec"]["ok"]
    assert res["seating"]["ok"] is True and [L["lens"] for L in res["seating"]["lenses"]] == ["Lens_L"]
    res = blender.export_and_validate(tmp_path / "bad.blend", tmp_path / "bad.glb")
    assert res["export"]["status"] == "failed" and res["spec"] is None and res["seating"] is None
    # no g3d_seat extras in the GLB: the inferred type is provisional unless the caller declares it
    src.write_bytes(glb(tmp_path, seated_acetate(), "plain.glb").read_bytes())
    res = blender.export_and_validate(tmp_path / "ok.blend", tmp_path / "out2.glb")
    assert res["seating"]["ok"] is False and res["seating"]["lenses"][0]["provisional"]
    res = blender.export_and_validate(tmp_path / "ok.blend", tmp_path / "out3.glb", seating={"frame_type": "acetate"})
    assert res["seating"]["ok"] is True and res["seating"]["lenses"][0]["frame_type_source"] == "argument"
