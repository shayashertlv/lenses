"""WP4 host check: the review round-2 holes (WP4_DESIGN.md §6 step 1, §8). Each test is the reviewer's probe.

1. The buried-part band is graded by MEMBERSHIP in the held parts, not by the held part being innermost: after
   hide_eyewire lifts the eyewire, the brow is innermost over the hidden range and the band used to go ungraded
   (a false PASS with the eyewire sticking out of the brow's back face).
2. An open frame component within 6 mm of a rim fails the file (E_OPEN_PART): dropping it turned a failing
   semi-rimless lens into a pass.
3. A skipped lens primitive (compressed, not triangles) fails the file (E_EMPTY / E_UNREADABLE), and so does an
   undecoded frame surface near a rim (E_UNREADABLE_PART).
"""
import json

import numpy as np
import pytest

from glasses3d.g3d.seat import geom, stations
from glasses3d.g3d_host.seat_check import check_glb
from glasses3d.tests.fixtures import seat_fixtures as SF


def glb(tmp_path, sc, name="s.glb", **kw):
    p = tmp_path / name
    SF.write_glb(p, SF.scene_nodes(sc, **kw))
    return p


# ----------------------------------------------------------------- 1. band by membership, not innermost
def test_band_is_graded_where_the_lifted_eyewire_sits_under_an_innermost_brow(tmp_path):
    """After hide_eyewire the brow is the innermost part over the hidden range (q001: brow start - eyewire start
    p50 0.66 mm, lift ~1.0 mm). The eyewire still belongs to the band: here it sticks out 0.2 mm behind the
    brow's back face, so §8's 'eyewire area outside the brow 0.00 mm2' and 'band <= 0.05 mm' must FAIL."""
    sc = SF.browline_lifted_scene(0.2)
    rep = check_glb(glb(tmp_path, sc, seat_extras={"frame_type": "browline"}), hidden_x_range_mm=[sc["range"]],
                    stations_table=True)
    R = rep["lenses"][0]
    inr = [s for s in R["stations"] if s["rule"] == "deep"]
    assert inr and {s["owner"] for s in inr} == {"Brow"}                           # the brow is innermost
    assert min(s["value"] for s in inr) >= 0.60                                    # deep capture alone passes
    band = R["band"]
    assert band["held_parts"] == ["Eyewire"]
    assert band["graded_stations"] == R["in_ranges"]["points"] == len(inr)
    g = band["graded"]
    assert g["ok"] is False and g["front_max_mm"] == 0.0
    assert g["area_outside_max_mm2"] == pytest.approx(0.2 * 0.7, abs=0.02)        # 0.2 mm proud x 0.7 mm wide
    assert 0.6 <= g["back_max_mm"] <= 1.0
    assert not R["pass"] and not rep["ok"] and any("band" in r for r in R["reasons"])
    # the designers' instrument (independent occupancy grid) on the same scene, its own stations in the range
    from glasses3d.tests.instruments import seat_grid as IG
    L, ew, brow = sc["lens"], sc["parts"]["Eyewire"], sc["parts"]["Brow"]
    rows = [r for r in IG.eyewire_band(L.V, L.T, (brow.V, brow.T), (ew.V, ew.T))["rows"]
            if sc["range"][0] <= r["x"] <= sc["range"][1]]
    assert rows
    assert max(r["outside_mm2"] for r in rows) == pytest.approx(g["area_outside_max_mm2"], abs=0.02)
    assert max(r["front_mm"] for r in rows) == 0.0
    assert float(np.median([r["back_mm"] for r in rows])) == pytest.approx(0.7, abs=0.05)


def test_band_passes_when_the_lifted_eyewire_is_buried_in_the_brow(tmp_path):
    """Control: the same lift with the eyewire inside the brow (0.2 mm short of its back face): band 0 at every
    graded station, area 0, and the lens passes."""
    sc = SF.browline_lifted_scene(-0.2)
    rep = check_glb(glb(tmp_path, sc, seat_extras={"frame_type": "browline"}), hidden_x_range_mm=[sc["range"]])
    R = rep["lenses"][0]
    assert R["band"]["graded_stations"] == R["in_ranges"]["points"] > 0
    assert R["band"]["graded"] == {"front_max_mm": 0.0, "back_max_mm": 0.0, "area_outside_max_mm2": 0.0,
                                   "ok": True}
    assert R["pass"] and rep["ok"], rep["reasons"]


def _meas(N):
    return stations.Measurement(np.full((N, 5), 0.4), np.full((N, 5), 1.0), np.zeros((N, 5), np.int64),
                                np.full(N, -1.0), np.full(N, -1.0), np.zeros(N, bool), np.zeros(N, np.int64),
                                np.full(N, "LIPPED", dtype=object), [None] * N, np.zeros((N, 5)))


def test_held_parts_are_counted_outside_the_hidden_ranges():
    """On q001 the brow becomes innermost at all 117 in-range stations after the lift, which drops the eyewire's
    share over all 337 stations to 0.61, a knife edge on §6's 0.60; outside the ranges it is 0.94."""
    N = 100
    m = _meas(N)
    in_range = np.zeros(N, bool)
    in_range[:45] = True
    m.owner[in_range] = 1                                                         # the brow, after the lift
    assert stations.held_parts(m) == ()                                           # 55% over all stations
    assert stations.held_parts(m, mask=~in_range) == (0,)
    assert stations.held_parts(m, mask=np.zeros(N, bool)) == ()                  # empty mask: all stations count


@pytest.mark.parametrize("eyewire,brow,want", [
    ([[0.3, 1.0]], [[-0.7, 3.5]], (0, 1)),      # brow innermost, reaches farther: the lifted eyewire is buried
    ([[-0.4, 0.4]], [[0.2, 3.5]], (0, 1)),      # eyewire innermost, brow outward (q001 before the lift)
    ([[-0.4, 1.2]], [[-0.7, 1.0]], (-1, -1)),   # the brow ends first: nothing outward of the eyewire
    ([[0.3, 1.0]], [[-0.7, 1.0]], (-1, -1)),    # coincident far faces are not 'outward'
    ([[4.5, 5.0]], [[-0.7, 6.0]], (-1, -1)),    # the eyewire starts beyond the §6 window (4 mm)
    (None, [[-0.7, 3.5]], (-1, -1)),            # no eyewire on the line (a corner where only the brow is)
])
def test_band_candidate_is_membership_in_the_window(eyewire, brow, want):
    per = [None if eyewire is None else np.array(eyewire, float), np.array(brow, float)]
    assert stations.band_candidate(per, 0.0, (0,)) == want
    assert stations.band_candidate(per, 0.0, ()) == (-1, -1)                      # nobody holds the lens


def test_band_candidate_skips_a_corner_where_the_brow_is_under_the_saddle():
    """FT's brow-under-saddle corner: the brow is innermost with the saddle outward but the brow is not held and
    the held eyewire is not on the line there."""
    per = [None, np.array([[-0.3, 1.5]]), np.array([[0.4, 4.0]])]                 # eyewire, brow, saddle
    assert stations.band_candidate(per, 0.0, (0,)) == (-1, -1)


# ------------------------------------------------------- 2. an open frame part near a rim fails the file
def test_an_open_channel_near_the_rim_fails_the_file(tmp_path):
    """A semi-rimless channel at 0.10 capture plus a small closed nasal clip at 0.45. Closed: FAIL (the channel
    stations miss the 0.30 thin rule). With its 12 end-cap triangles removed the channel is open: before the fix
    it was dropped, its stations turned EXPOSED = visible by design, and the lens PASSED on the clip alone."""
    L = SF.base_lens()
    ch = SF.channel(L, SF.arc_mask(L, "top", 0.45), 0.10)
    open_ch = SF.Mesh(ch.V, ch.T[:-12].copy())
    assert geom.topology(open_ch.T, len(open_ch.V))["boundary_edges"] == 16
    clip = SF.channel(L, SF.arc_mask(L, "nasal", 0.06), 0.45)
    closed = check_glb(glb(tmp_path, {"lens": L, "parts": {"Channel": ch, "Clip": clip}}, "c.glb",
                           seat_extras={"frame_type": "semi_rimless"}))
    assert not closed["ok"] and closed["lenses"][0]["verdicts"]["FAIL"] > 100 and closed["lens_problems"] == []
    rep = check_glb(glb(tmp_path, {"lens": L, "parts": {"Channel": open_ch, "Clip": clip}}, "o.glb",
                        seat_extras={"frame_type": "semi_rimless"}))
    R = rep["lenses"][0]
    assert R["parts"] == ["Clip"] and R["verdicts"].get("FAIL", 0) == 0          # what used to pass
    assert rep["ok"] is False and not R["pass"] and not R["graded_pass"]
    assert [(x["code"], x["node"], x["lenses"]) for x in rep["lens_problems"]] == [
        ("E_OPEN_PART", "Channel", ["Lens_L"])]
    assert "16 boundary" in rep["lens_problems"][0]["message"]
    assert [o["node"] for o in rep["open_parts"]] == ["Channel"]
    assert R["unmeasured_parts"] == [{"code": "E_OPEN_PART", "component": "Channel"}]
    assert any(r.startswith("Channel: E_OPEN_PART") for r in rep["reasons"])


def test_an_open_frame_part_far_from_every_rim_is_ignored(tmp_path):
    L = SF.base_lens()
    sheet = SF.Mesh(np.array([[60, 0, 0], [61, 0, 0], [61, 1, 0]], float), np.array([[0, 1, 2]]))
    rep = check_glb(glb(tmp_path, {"lens": L, "parts": {"Eyewire": SF.eyewire(L, 0.45), "Far": sheet}},
                        seat_extras={"frame_type": "metal"}))
    assert rep["ok"] and rep["open_parts"] == [] and rep["lens_problems"] == [], rep["reasons"]


# ------------------------------------------------- 3. skipped primitives are never silently dropped
def two_lens_glb(tmp_path):
    L = SF.base_lens()
    right = SF.mirrored_lens()
    src = tmp_path / "two.glb"
    SF.write_glb(src, SF.scene_nodes({"lens": L, "parts": {"Rim": SF.ring_frame(L, 0.8)}},
                                     seat_extras={"frame_type": "acetate"}, extra_nodes=[
        {"name": "Lens_R", "mesh": right, "role": "lens", "material": {"name": "Lens", "transmission": 1.0},
         "extras": {"g3d_seat": {"frame_type": "acetate"}}},
        {"name": "Rim_R", "mesh": SF.ring_frame(right, 0.8), "role": "frame"}]))
    return src


def _mesh(doc, name):
    return next(m for m in doc["meshes"] if m["name"] == name)


def _draco(prim, doc):
    prim.setdefault("extensions", {})["KHR_draco_mesh_compression"] = {"bufferView": 0, "attributes": {}}
    if "KHR_draco_mesh_compression" not in doc.setdefault("extensionsUsed", []):
        doc["extensionsUsed"].append("KHR_draco_mesh_compression")


@pytest.mark.parametrize("how", ("draco", "lines"))
def test_a_skipped_lens_primitive_fails_the_file(tmp_path, how):
    src = two_lens_glb(tmp_path)
    assert check_glb(src)["ok"]

    def edit(doc):
        prim = _mesh(doc, "Lens_R")["primitives"][0]
        if how == "draco":
            _draco(prim, doc)
        else:
            prim["mode"] = 1                                                        # LINES: no triangles
    dst = tmp_path / f"{how}.glb"
    SF.patch_glb(src, dst, edit)
    rep = check_glb(dst)
    assert [L["lens"] for L in rep["lenses"]] == ["Lens_L"] and rep["lenses"][0]["pass"]
    assert rep["ok"] is False and rep["lens_nodes"] == ["Lens_L", "Lens_R"]
    assert [(x["code"], x["node"]) for x in rep["lens_problems"]] == [("E_EMPTY", "Lens_R")]
    assert any(r.startswith("Lens_R: E_EMPTY") for r in rep["reasons"])


def test_a_skipped_second_lens_primitive_is_unreadable(tmp_path):
    """The node keeps a readable lens primitive (graded), but its second, compressed one is E_UNREADABLE."""
    src = two_lens_glb(tmp_path)

    def edit(doc):
        m = _mesh(doc, "Lens_R")
        extra = json.loads(json.dumps(m["primitives"][0]))
        _draco(extra, doc)
        m["primitives"].append(extra)
    dst = tmp_path / "two_prims.glb"
    SF.patch_glb(src, dst, edit)
    rep = check_glb(dst)
    assert sorted(L["lens"] for L in rep["lenses"]) == ["Lens_L", "Lens_R"]
    assert rep["ok"] is False
    assert [(x["code"], x["node"]) for x in rep["lens_problems"]] == [("E_UNREADABLE", "Lens_R")]


@pytest.mark.parametrize("name,mode,ok,codes", [("Channel", "draco", False, ["E_UNREADABLE_PART"]),
                                                ("Far", "draco", True, []), ("Channel", "lines", False, [])])
def test_an_undecoded_frame_surface_near_a_rim_fails(tmp_path, name, mode, ok, codes):
    """The frame-side twin: a compressed channel primitive on a semi-rimless lens (its POSITION min/max box lies
    at the rim) fails the file and leaves the lens ungraded; the same compression on a far part only warns. A
    LINES primitive has no surface, so it is skipped with a warning and graded as absent (here the lens then has
    no required station and fails on that, not on a read problem)."""
    L = SF.base_lens()
    far = SF.translated(SF.channel(L, SF.arc_mask(L, "top", 0.2), 0.45), (0.0, 80.0, 0.0))
    sc = {"lens": L, "parts": {"Channel": SF.channel(L, SF.arc_mask(L, "top", 0.45), 0.45), "Far": far}}
    src = glb(tmp_path, sc, seat_extras={"frame_type": "semi_rimless"})
    assert check_glb(src)["ok"]

    def edit(doc):
        prim = _mesh(doc, name)["primitives"][0]
        if mode == "draco":
            _draco(prim, doc)
        else:
            prim["mode"] = 1
    dst = tmp_path / f"{name}_{mode}.glb"
    SF.patch_glb(src, dst, edit)
    rep = check_glb(dst)
    assert [x["code"] for x in rep["lens_problems"]] == codes, rep["lens_problems"]
    assert any(w.startswith(f"{name}: primitive 0 skipped") for w in rep["warnings"])
    assert rep["ok"] is ok, rep["reasons"]
    R = rep["lenses"][0]
    if codes:
        assert not R["pass"] and not R["graded_pass"]
        assert R["unmeasured_parts"] == [{"code": "E_UNREADABLE_PART", "component": "Channel primitive 0"}]
        assert rep["lens_problems"][0]["lenses"] == ["Lens_L"]
    elif not ok:
        assert R["required"] == 0 and any("no required station" in r for r in R["reasons"])
