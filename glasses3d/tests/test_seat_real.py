"""WP4 P0, the host half of P1 and the P2-P5 proof re-check on the real data (WP4_DESIGN.md §10); skips when the Studio
job data or the proof outputs are absent.

P0: core vs the independent occupancy-grid instrument (tests/instruments/seat_grid.py) at the instrument's stations:
|delta capture| <= 0.075 mm at >= 99% of stations, for the capture check_glb GRADES (core `measure` with the lens
part vs the instrument's occupancy-only §3 measurement) and for the designers' own definition (continuity with R);
outer front loops 138.0 / 143.6 / 383.5 / 458.1 mm +- 0.1; riders found: Prada etch, OAKLEY, PRIZM, INVU print.
P1 (host): numpy vs open3d within 1e-3 mm; two check runs byte-identical. Band: the designers' eyewire-band
instrument reproduces its q001 numbers and agrees with the core's buried_band.
"""
import json

import pytest

from glasses3d.tests.instruments import seat_p0 as P0

pytestmark = pytest.mark.slow
KEYS = ("ft", "prada", "oakley", "invu")


def need(key):
    if not P0.available(key):
        pytest.skip(f"{key} GLB not on disk")


@pytest.fixture(scope="module")
def inputs():
    return {k: P0.lens_inputs(k) for k in KEYS if P0.available(k)}


@pytest.mark.parametrize("key", KEYS)
def test_p0_core_agrees_with_the_grid_instrument(key, inputs):
    need(key)
    for body, parts in inputs[key]:
        r = P0.p0_lens(body, parts)
        assert r["graded"]["share_agree"] >= P0.P0_SHARE, r
        assert r["designers"]["share_agree"] >= P0.P0_SHARE, r
        assert abs(r["core_loop_mm"] - P0.LOOP_MM[key]) <= 0.1, r
        assert abs(r["instrument_perimeter_mm"] - P0.LOOP_MM[key]) <= 0.1, r
        if key != "ft":                       # FT: the 128-gon fan slivers; R counted 3196, every fan triangle is 3198
            assert r["cap_triangles_core"] == [P0.CAP_TRIANGLES_R[key]] * 2, r
        else:
            assert r["cap_triangles_core"] == [3198, 3198]


@pytest.mark.parametrize("key", ("prada", "oakley", "invu"))
def test_p0_riders_are_found(key):
    need(key)
    from glasses3d.g3d_host.seat_check import check_glb
    rep = check_glb(P0.GLBS[key])
    names = {r["node"] for r in rep["riders"]}
    for want in P0.RIDERS[key]:
        assert any(want in n for n in names), (want, names)
    lens_names = {L["lens"] for L in rep["lenses"]}
    assert not any(want in n for n in lens_names for want in P0.RIDERS[key])        # never a lens body


@pytest.mark.parametrize("key", ("invu",))
def test_p1_numpy_and_open3d_agree_on_real_meshes(key, inputs):
    need(key)
    for body, parts in inputs[key]:
        r = P0.p1_lens(body, parts)
        for k in ("cap", "room", "cover_front", "cover_back", "edge"):
            assert r[k]["finite_mismatch"] == 0 and r[k]["max_abs_mm"] <= P0.P1_TOL_MM, (k, r[k])
        assert r["classes_equal"] and r["owners_equal"] and r["unmeasured"] == [0, 0]
        assert r["band_front_max_abs_mm"] <= 0.025 + 1e-9                          # at most one band column


def test_p1_check_runs_are_bit_identical():
    need("invu")
    from glasses3d.g3d_host.seat_check import check_glb
    a = json.dumps(check_glb(P0.GLBS["invu"], stations_table=True), sort_keys=True)
    b = json.dumps(check_glb(P0.GLBS["invu"], stations_table=True), sort_keys=True)
    assert a == b


def test_finals_check_verdicts_before_seating():
    """§10 P5 before-state: the Oakley final passes as semi_rimless (top LIPPED 0.65-0.80, rear seats visible by
    design); the INVU top rail fails; Prada fails acetate's 0.60 at its nasal/temporal stations."""
    for key in ("oakley", "invu", "prada"):
        need(key)
    from glasses3d.g3d_host.seat_check import check_glb
    rep = check_glb(P0.GLBS["oakley"])
    oak = rep["lenses"][0]
    assert oak["frame_type"] == "semi_rimless" and oak["frame_type_source"] == "inferred"
    assert oak["graded_pass"] and not oak["pass"] and not rep["ok"]           # provisional: never ok on inference
    rep = check_glb(P0.GLBS["oakley"], frame_type="semi_rimless")
    oak = rep["lenses"][0]
    assert oak["pass"] and rep["ok"], rep["reasons"]
    top = oak["sectors"]["top"]["mid_depth_p5_p50_p95"]
    assert 0.6 <= top[0] and top[2] <= 0.9
    invu = check_glb(P0.GLBS["invu"], frame_type="semi_rimless")["lenses"][0]
    assert invu["frame_type"] == "semi_rimless" and not invu["pass"] and not invu["graded_pass"]
    assert invu["sectors"]["top"]["classes"].get("ENCLOSED", 0) + invu["sectors"]["top"]["classes"].get("LIPPED", 0) \
        == invu["sectors"]["top"]["points"]                                         # the whole top rail is required
    for L in check_glb(P0.GLBS["prada"], frame_type="acetate")["lenses"]:
        assert L["frame_type"] == "acetate" and not L["pass"] and L["classes"]["ENCLOSED"] == L["points"]
        assert L["sectors"]["top"]["value_p5_p50_p95"][0] >= 0.6


def test_p2_before_state_q001_fails_as_browline_with_the_ranges():
    """§10 P2: 'the check FAILS on the input with the same ranges' (the PASS after seating is the tool stage's)."""
    need("ft")
    from glasses3d.g3d_host.seat_check import check_glb
    rep = check_glb(P0.GLBS["ft"], frame_type="browline", hidden_x_range_mm=[(-56.2, -11.6), (11.6, 56.2)])
    for L in rep["lenses"]:
        assert not L["pass"] and any("band" in r for r in L["reasons"])
        assert 0 < L["in_ranges"]["points"] < L["sectors"]["top"]["points"] + L["sectors"]["nasal"]["points"]
        assert L["band"]["graded"]["front_max_mm"] > 0.5                            # q001's visible eyewire band


def test_band_instrument_reproduces_q001_and_agrees_with_the_core():
    """The designers' ft_eyewire_hidden.py numbers on q001 (49 upper stations with brow per lens; band visible from
    the front at 76% of them, 0.60 mm median, 0.78 max; 0.80 median from the back), and the core's buried_band at
    the same station frames within the band criterion's 0.05 mm. Candidates are chosen by membership (the held
    eyewire in the §6 window with the brow reaching farther, review round 2), so every instrument station is one
    (round 1's innermost-only rule left out 11 at x 11.6-19.5, where the brow starts first); where the core sees
    no candidate, the instrument must see no band."""
    need("ft")
    for r in P0.band_q001():
        assert r["instrument_stations"] == 49, r
        assert r["instrument_front_p50_max"] == [0.6, 0.78] and r["instrument_back_p50"] == 0.8, r
        assert abs(r["instrument_front_share_gt_0.05"] - 0.76) <= 0.01, r
        assert r["held_parts"] == ["Eyewire"] and r["core_candidates_at_instrument_stations"] == 49, r
        assert r["front_delta_max_abs"] <= 0.05 and r["back_delta_max_abs"] <= 0.05, r
        nc = r["instrument_at_non_candidates_front_back_max"]
        assert nc is None or max(nc) <= 0.05, r


def test_q001_band_pairs_are_the_eyewire_under_the_brow_only():
    """Every candidate is the eyewire under the brow (never the brow under the saddle bridge: the brow is not
    held). The visible share counts the stations where the brow starts first and covers the eyewire's front too
    (band 0), as the instrument does: it reads 0.755 at its 49 upper stations."""
    need("ft")
    from glasses3d.g3d_host.seat_check import check_glb
    for L in check_glb(P0.GLBS["ft"], frame_type="browline")["lenses"]:
        pairs = L["band"]["pairs"]
        assert list(pairs) == [f"Eyewire_{L['lens'].split('_')[1]} under Brow_{L['lens'].split('_')[1]}"], pairs
        p = next(iter(pairs.values()))
        assert 0.6 <= p["front"]["share_gt_0.05"] <= 0.8 and 0.6 <= p["front"]["p50_mm"] <= 0.8, p


# ======================================================================================= §10 P2-P5 (stage 'proof')
# glasses3d/data/wp4/proof.json is written by `python -m glasses3d.tests.proofs.wp4_proof` (headless Blender on
# copies, the WP3 exporter, then tests/proofs/wp4_proof_grade.py). These tests re-check the recorded criteria from the
# produced files (raw Blender arrays, Blender outputs, exported GLBs) and skip when they are absent.
def _proof_dir():
    return P0.REPO / "glasses3d/data/wp4"


def _proof_or_skip() -> dict:
    """proof.json, or a skip when the outputs are absent or STALE: every proof records the sha256 of the code that
    produced its files (g3d/seat/*, seat_bpy.py, the exporter, the Blender runners, the fixture builder; proof
    review: the outputs were not tied to that code), and a file changed since the run, or cases run with different
    code, make it stale (re-run python -m glasses3d.tests.proofs.wp4_proof)."""
    p = _proof_dir() / "proof.json"
    st = _proof_dir() / "proof/run_state.json"
    if not p.is_file() or not st.is_file():
        pytest.skip("WP4 proof outputs not produced (python -m glasses3d.tests.proofs.wp4_proof)")
    from glasses3d.tests.proofs.wp4_proof import tool_hashes
    state = json.loads(st.read_text(encoding="utf-8"))
    rec = state.get("tools_sha256") or {}
    now = tool_hashes()
    if rec != now or not state.get("tools_consistent"):
        changed = sorted(k for k in set(rec) | set(now) if k != "*" and rec.get(k) != now.get(k))
        pytest.skip(f"stale proof: changed since the run {changed or '(no tool hashes recorded)'}; cases consistent "
                    f"{state.get('tools_consistent')}")
    proof = json.loads(p.read_text(encoding="utf-8"))
    if proof.get("tools_sha256") != rec:
        pytest.skip("stale proof: proof.json was graded from another run (python -m glasses3d.tests.proofs.wp4_proof "
                    "--grade-only)")
    return proof


@pytest.fixture(scope="module")
def proof():
    return _proof_or_skip()


@pytest.fixture(scope="module")
def regraded(proof):
    """Every criterion measured again from the produced files (core, instrument, arrays), by the same grader."""
    from glasses3d.tests.proofs import wp4_proof_grade as G
    try:
        return G.grade_all()
    except FileNotFoundError as exc:
        pytest.skip(f"a WP4 proof output is missing: {exc}")


def _same(a, b, tol=1e-9) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k], tol) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(float(a) - float(b)) <= tol
    return a == b


def test_proof_sources_were_never_written(proof):
    """§10: every source .blend's sha256 is unchanged (recorded before and after the runs, and re-hashed now)."""
    from glasses3d.tests.proofs.wp4_proof import SOURCES, sha
    assert proof["summary"]["sources_sha256_unchanged"] is True
    for key, rec in proof["sources"].items():
        assert rec["sha256_before"] == rec["sha256_after"], key
        if SOURCES[key].is_file():
            assert sha(SOURCES[key]) == rec["sha256_before"], key


def test_proof_record_matches_a_fresh_grading_of_the_files(proof, regraded):
    """The recorded criteria (ids, thresholds, measured values, verdicts) are what the files say now."""
    for cid in ("P2", "P3", "P4", "P5"):
        rec, now = proof["cases"][cid]["criteria"], regraded["cases"][cid]["criteria"]
        assert [r["id"] + "/" + r["by"] for r in rec] == [r["id"] + "/" + r["by"] for r in now], cid
        for a, b in zip(rec, now):
            assert a["pass"] == b["pass"] and a["threshold"] == b["threshold"], (a, b)
            assert _same(a["measured"], b["measured"]), (a["id"], a["measured"], b["measured"])
    assert regraded["summary"] == proof["summary"]


def test_proof_stop_rule_items_hold(proof):
    """§10 P5 stop rule: no Oakley vertex moved, no moved vertex outside the band of failing nodes (every seat call
    graded: q001 both sides, Prada, the metal fixture, the acetate matrix, INVU), no missed P2 criterion."""
    s = proof["summary"]
    assert s["stop_rule_triggered"] == []
    assert s["P2"]["pass"] and s["P2"]["failed"] == []
    recs = [r for c in proof["cases"].values() for r in c["criteria"]]
    stop = [r for r in recs if r["id"] == "P5.oakley.zero_moved" or "moved_in_failing" in r["id"]
            or "moved_set" in r["id"] or "seat_moves_only" in r["id"] or r["id"] == "P4.metal.all_else_bit_identical"]
    assert len(stop) >= 40 and not [r["id"] for r in stop if r["pass"] is False]
    # not applicable only where nothing moved (Prada refused, Oakley 0), never a pass by default
    na = [r for r in stop if r["pass"] is None]
    assert all(r["measured"].get("moved_nodes", r["measured"].get("moved")) == 0 for r in na), na
    # every seat call that moved vertices is graded by the check's verdict AND by the instrument on its before-GLB
    for rid in ("P2.seat.Left", "P2.seat.Right", "P4.metal", "P5.invu") + tuple(
            f"P4.matrix.{c}" for c in ("sphere", "toric", "flat", "solidified", "chamfer", "flipped", "rounded",
                                       "coarse")):
        for by, key in (("core", "nodes_failing_by_check"), ("instrument", "nodes_failing_by_instrument")):
            r = [x for x in recs if x["id"] == f"{rid}.moved_set.{key}" and x["by"] == by]
            assert len(r) == 1 and r[0]["pass"] is True and r[0]["measured"]["moved_nodes"] > 0, (rid, key, r)


def test_proof_moved_sets_from_the_raw_arrays(proof):
    """Directly from the raw Blender arrays and the before-state recorded inside Blender (no grader): every vertex
    seat_lens moved is in the band of the nodes that failed before, and every such vertex moved."""
    import numpy as np
    out_dir = _proof_dir() / "proof"
    cases = [("invu", "a0", "a1", 0, "ns_before"), ("metal", "a0", "a1", 0, "ns_before"),
             ("metal_narrow", "a0", "a1", 0, "ns_before"),
             ("oakley", "a0", "a1", 0, "ns_before"), ("ft", "amid", "a1", 0, "ns_mid_Left"),
             ("ft", "amid", "a1", 1, "ns_mid_Right")] + [
        (f"acetate_{c}", "a0", "a1", 0, "ns_before") for c in ("sphere", "toric", "flat", "solidified", "chamfer",
                                                              "flipped", "rounded", "coarse")]
    for case, k0, k1, i, ns in cases:
        z0 = np.load(out_dir / "npz" / f"{case}-{k0}.npz")
        z1 = np.load(out_dir / "npz" / f"{case}-{k1}.npz")
        moved = set(np.flatnonzero(np.any(z0[f"o{i}_co"] != z1[f"o{i}_co"], axis=1)).tolist())
        res = {r["key"]: r["result"] for r in json.loads((out_dir / "outs" / f"{case}.json").read_text(
            encoding="utf-8"))["results"]}
        b0 = res[ns]["bodies"][0]
        expected = set(b0["expected_raw"])
        assert moved == expected, (case, len(moved), len(expected), len(moved - expected))
        # not circular: expected_raw comes from the planner's own post-clamp FAIL list, so also check the moved rim
        # nodes against the CHECK's verdict recorded in the same before-state (proof review)
        v = np.asarray(b0["point"]["verdict_check"])
        fail_nodes = set(np.asarray(b0["point"]["node"])[v == "FAIL"].tolist()) |             set(np.asarray(b0["point"]["node2"])[v == "FAIL"].tolist())
        moved_nodes = {k for k, r in enumerate(b0["loop_raw"]) if r in moved}
        assert moved_nodes <= fail_nodes, (case, sorted(moved_nodes - fail_nodes)[:10])
        assert (len(moved_nodes) > 0) == (case != "oakley"), case
    z0 = np.load(out_dir / "npz" / "oakley-a0.npz")
    z1 = np.load(out_dir / "npz" / "oakley-a1.npz")
    assert np.array_equal(z0["o0_co"], z1["o0_co"])                         # Oakley: 0 vertices moved


def test_proof_oakley_lens_primitive_reexports_byte_identical(proof):
    from glasses3d.tests.instruments import seat_proof_metrics as M
    from glasses3d.tests.proofs.wp4_proof import OAKLEY_LENS
    glb = _proof_dir() / "proof/glb"
    a = M.accessor_bytes(glb / "oakley-before.glb", OAKLEY_LENS)
    b = M.accessor_bytes(glb / "oakley-after.glb", OAKLEY_LENS)
    assert a and a == b


def test_proof_q001_after_passes_both_checks_and_the_spec(proof):
    """§10 P2 on the exported GLB: the host check (frame type and ranges from the exporter's g3d_seat extras) and
    validate_spec pass; the input GLB fails the check with the same ranges."""
    from glasses3d.g3d_host.seat_check import check_glb
    from glasses3d.g3d_host.spec import validate_spec
    glb = _proof_dir() / "proof/glb"
    after = check_glb(glb / "ft-after.glb")
    assert after["ok"] and all(L["frame_type_source"] == "g3d_seat" for L in after["lenses"]), after["reasons"]
    assert validate_spec(glb / "ft-after.glb")["ok"]
    before = check_glb(glb / "ft-before.glb", frame_type="browline", hidden_x_range_mm=[(-56.2, -11.6), (11.6, 56.2)])
    assert not before["ok"]


def test_proof_records_the_crystal_verdict_and_the_refusals(proof):
    """Proof review: the crystal case's binding verdict is recorded (proof fix round, open item B: the check reads the
    declared V-bevel from the groove's lips and PASSES it, the tool says seated), the self-intersecting lens is refused
    E_SELF_INTERSECTING with nothing written, the open MANIFOLD cutter is rejected by the tool's own acceptance test
    while the closed control cutter is accepted (MANIFOLD and EXACT), and records with nothing to measure are
    'not_applicable' (pass null) and report-only ones 'report_only', never counted as passing."""
    recs = {(r["id"], r["by"]): r for c in proof["cases"].values() for r in c["criteria"]}
    cr = recs[("P4.crystal.check", "core")]
    assert cr["pass"] is True and cr["measured"]["tool"]["status"] == "seated"
    assert cr["measured"]["blender_check"]["ok"] is True and cr["measured"]["host_check"]["ok"] is True
    sx = recs[("P4.refusal.selfx", "core")]
    assert sx["pass"] is True and sx["measured"]["code"] == "E_SELF_INTERSECTING" and sx["measured"]["digest_unchanged"]
    om = recs[("P4.crystal.open_cutter_manifold", "core")]
    assert om["pass"] is True
    assert om["measured"]["closed"]["accepted_by_the_tool_check"] and om["measured"]["closed_exact"][
        "accepted_by_the_tool_check"] and not om["measured"]["open"]["accepted_by_the_tool_check"]
    for cid, c in proof["cases"].items():
        n = c["counts"]
        total = n["pass"] + n["fail"] + n["not_applicable"] + n["report_only"] + n.get("owner_pending", 0)
        assert total == n["criteria"] == len(c["criteria"])
        for r in c["criteria"]:
            assert (r["pass"] is None) == (r["status"] in ("not_applicable", "report_only", "owner_pending")), r["id"]
    # open item G: Oakley's top LIPPED 0.65-0.80 is report-only (§7: over-capture is a warning; owner accepted Oakley)
    for by in ("core", "instrument"):
        r = recs[("P5.oakley.top_lipped", by)]
        assert r["status"] == "report_only" and r["pass"] is None and "report-only" in r["note"], r
    p3 = proof["cases"]["P3"]
    if any(p3["info"]["tool"][s]["status"] == "refused" for s in ("L", "R")):
        for s in ("L", "R"):
            for rid in ("max_delta", "moved", "moved_in_failing_nodes", "volume_gain"):
                assert recs[(f"P3.{rid}.{s}", "arrays")]["pass"] is None


def test_proof_review_round2_records(proof):
    """Proof review round 2: (1) the cord-lens overlap is graded from the TOOL's report (EXACT Boolean on the grooved
    lens) and both checks' reports, and the stage-proof cord (off the §5 groove) is reported: tool partial with
    W_CORD_OVERLAP at ~0.74 mm3, both checks fail the lens on it, the instrument sees the same volume; (2) the notch
    refusal counts only when it comes from the CONCAVE clamp or the gate, so the post-check refusal is a FAIL (open
    item D); (3) the crystal bevel and the nylor grooves are measured by the core on the exported lens, not copied
    from the plan; (4) the browline instrument applies the deep rule only where the station is layered."""
    recs = {(r["id"], r["by"]): r for c in proof["cases"].values() for r in c["criteria"]}
    for case in ("nylor", "nylor_nocord"):
        r = recs[(f"P4.{case}.cord_overlap", "core")]
        m = r["measured"]
        assert r["pass"] is True and m["tool_mm3"] <= 0.01 and m["blender_check_mm3"] <= 0.01, m
        assert m["host_check_mm3"] <= 0.01 and len(m["check_cord_parts"]) == 1, m
    m = recs[("P4.nylor.cord_overlap", "core")]["measured"]
    assert m["tool_cord"] == "existing" and [c["object"] for c in m["tool_cord_parts"]] == ["Cord"]
    ctl = recs[("P4.nylor_outline.cord_overlap_reported", "core")]
    m = ctl["measured"]
    assert ctl["pass"] is True and m["tool_status"] == "partial" and m["tool_warning"], m
    assert abs(m["tool_mm3"] - 0.7393) <= 0.01 and abs(m["host_check_mm3"] - m["blender_check_mm3"]) <= 1e-3, m
    assert m["blender_check_fails_on_it"] and m["host_check_fails_on_it"]
    assert recs[("P4.nylor_outline.cord_overlap_reported", "instrument")]["pass"] is True
    notch = recs[("P4.refusal.notch", "core")]
    # proof fix round (open item E): the plan refuses the notch at its CONCAVE gate, nothing written
    assert notch["measured"]["code"] == "E_CONCAVE" and notch["pass"] is True, notch["measured"]
    assert notch["measured"]["digest_unchanged"] and (notch["measured"]["blocked_by"] or {}).get("CONCAVE", 0) >= 1
    for rid in ("P4.crystal.bevel_angle", "P4.crystal.bevel_h", "P4.nylor.groove", "P4.nylor_nocord.groove"):
        m = recs[(rid, "core")]["measured"]
        assert m["stations"] > 100 and m["measured"] == m["stations"], (rid, m)
    for side in ("Left", "Right"):
        m = recs[(f"P2.seat.{side}.moved_set.nodes_failing_by_instrument", "instrument")]["measured"]
        assert m["deep_rule_stations"] > 0 and m["in_range_not_layered"] > 0, m
        assert m["violations_n"] == 0, m


def test_proof_p2_ramp_exclusion_is_recorded(proof):
    """Proof review: P2's 'outside the ranges' reading excludes the 3 mm lift ramp; what changes inside it (moved
    stations' capture rises) is recorded for the owner, by the core and the instrument, both sides."""
    recs = [r for r in proof["cases"]["P2"]["criteria"] if r["id"].startswith("P2.outside_capture_unchanged")]
    assert len(recs) == 4
    for r in recs:
        ramp = r["measured"]["excluded_ramp_stations"]
        assert ramp["moved"]["n"] > 0 and ramp["unmoved"]["n"] > 0
        assert ramp["moved"]["delta_min_over_depth_min_max_mm"][1] > 0.5        # the ramp's min-over-depth rises
    assert any("ramp" in x for x in proof["summary"]["owner_readings"])


def test_proof_review_fix_round1_records(proof):
    """Proof review fix round 1: (1) the crystal overlap is graded from the tool's Boolean AND both checks, and the
    profiled lens in the ungrooved frame (the g3d_groove Boolean gone) fails the check (review control); (2) the
    narrow eyewire is a knife edge at exactly pass: its two §10 records fail with that note (the pass + 0.05 floor
    that made them pass was withdrawn), and the partial seat it writes moves only the band of FAIL nodes (stop rule);
    (3) 'top >= 0.44 on the flats' grades every required point, and a point within 2 mm of a channel end that the
    check passes below 0.35 is an owner_pending exception, never a silent pass; the instrument's transition stations
    are placed by position, at most one per channel end; (4) the margins of by-construction passes are in the
    summary."""
    recs = {(r["id"], r["by"]): r for c in proof["cases"].values() for r in c["criteria"]}
    m = recs[("P4.crystal.overlap", "core")]["measured"]
    assert m["blender_check_mm3"] <= 0.01 and m["host_check_mm3"] <= 0.01 and m["blender_stale_cutter"] is False, m
    for by in ("core", "instrument"):
        assert recs[("P4.crystal_control.no_groove_fails", by)]["pass"] is True
    ex_m = recs[("P4.metal_narrow.exits_material", "core")]
    assert "knife edge" in ex_m["note"] and "knife edge" in recs[("P4.metal_narrow.zero_moved", "arrays")]["note"]
    cap = ex_m["measured"]["top_capacity_mm"]
    assert cap["min_max"][0] >= cap["pass_mm"] - 0.005 and cap["min_max"][1] <= cap["pass_mm"] + 0.01, cap
    if ex_m["pass"] is False:                         # §4 step 4 literal: clamped at pass, seated partially
        assert recs[("P4.metal_narrow.seat_moves_only_failing_nodes", "arrays")]["pass"] is True
    tf = recs[("P4.nylor.top_flats", "core")]["measured"]
    assert tf["inside_n_min"][0] + tf["end_zone_n"] == tf["required"] and tf["inside_n_min"][0] > 100, tf
    it = recs[("P4.nylor.top_flats_end_zone", "instrument")]["measured"]
    assert len(it["transition_by_position"]) <= it["transition_limit"], it
    mg = proof["summary"]["margins"]
    assert "P5.invu.former_failures.inside" in mg["P5"] and "P4.metal.inside_runs.min" in mg["P4"], mg
    assert any(k.startswith("P3.face_rotation") for k in mg.get("P3", {})) or \
        proof["cases"]["P3"]["info"]["tool"]["L"]["status"] == "refused"


def test_proof_review_fix_round2_records(proof):
    """Proof review fix round 2: (1) the former failures of P4 metal and P5 INVU are graded at EVERY required point
    whose owning nodes lie >= 2 mm inside a former failing run (Blender check state and host check), not only at the
    node points the correction pass corrects by construction, and the margins are those of every point; (2) the
    FOLD blocks of the narrow eyewire are reported as direct and knock-on; (3) the nylor end-zone reading is one
    owner-pending record covering every point within 2 mm of a channel end (the two below 0.35 among them); (4) the
    owner decisions are listed in the summary (CORRECTION_AIM_MM among them)."""
    recs = {(r["id"], r["by"]): r for c in proof["cases"].values() for r in c["criteria"]}
    for rid, target in (("P4.metal.inside_runs", 0.44), ("P5.invu.former_failures", 0.45)):
        r = recs[(rid, "core")]
        m = r["measured"]
        n, lo, hi = m["inside_n_min_max"]
        nn, nlo, _ = m["node_points_only"]["inside_n_min_max"]
        assert n > nn > 0 and lo <= nlo, (rid, m)                       # every point, the node points among them
        hn = m["host_check"]["inside_n_min_max"]
        assert m["host_check"]["required_unmatched"] == 0 and hn[0] == n, (rid, m["host_check"])
        below = m[f"inside_below_{target}"] + m["host_check"][f"inside_below_{target}"]
        assert (r["pass"] is False) == (below > 0 or m["misses_n"] > 0 or m["host_check"]["misses_n"] > 0), (rid, m)
        assert "every point" in r["note"]
    mg = proof["summary"]["margins"]
    assert mg["P4"]["P4.metal.inside_runs.min"]["measured"] == pytest.approx(
        min(recs[("P4.metal.inside_runs", "core")]["measured"]["inside_n_min_max"][1],
            recs[("P4.metal.inside_runs", "core")]["measured"]["host_check"]["inside_n_min_max"][1]), abs=1e-4)
    fb = recs[("P4.metal_narrow.exits_material", "core")]["measured"]["fold_blocks"]
    assert fb is None or (fb["direct"] + fb["knock_on"] ==
                          recs[("P4.metal_narrow.exits_material", "core")]["measured"]["plan_counts"]["blocked_by"]
                          .get("FOLD", 0) and fb["knock_on"] > fb["direct"]), fb
    ez = recs[("P4.nylor.top_flats_end_zone", "core")]
    tf = recs[("P4.nylor.top_flats", "core")]["measured"]
    assert ez["status"] == "owner_pending" and ez["measured"]["end_zone_points"] == tf["end_zone_n"] > 2, ez
    assert sum(ez["measured"]["by_reading"].values()) == ez["measured"]["end_zone_points"]
    assert ("P4.nylor.top_flats_exception", "core") not in recs
    od = " ".join(proof["summary"]["owner_decisions"])
    for key in ("CORRECTION_AIM_MM", "FOLD", "junction", "narrow eyewire"):
        assert key in od, key
