"""WP4 proof stage, review fix round 2 (host, numpy core): the cord-lens overlap that §5 says the check reports.

The tool measures an existing cord's overlap with the grooved lens by Blender's EXACT Boolean (seat_bpy._nylor_cord,
tests/test_seat_blender.py); the check measures it in the station planes (stations.part_overlap: columns along n
every 0.01 mm of u, exact along n) for every frame part holding the groove centre, and fails a nylor lens above
0.01 mm3 (rules.check_verdict). Here: the measurement against an analytic Pappus volume, the stage-proof cord (0.74 mm3
by Blender's Boolean) against the independent occupancy instrument, and the check's verdict and report."""
import numpy as np
import pytest

from glasses3d.g3d.seat import check as CK
from glasses3d.g3d.seat import geom, profile as PR, rules, stations
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.g3d.seat.plan import node_points
from glasses3d.g3d_host.seat_check import Open3DBackend, factory as o3d_factory
from glasses3d.tests.fixtures import seat_fixtures as SF


def grooved_lens(L: SF.Mesh) -> tuple[np.ndarray, np.ndarray]:
    """The lens re-walled with the §5 nylor groove by the core's own plan (profile.rewall_plan), as seat_bpy applies
    it with bmesh: the outer wall's faces deleted, the plan's quads added (split into triangles)."""
    V, T = L.V, L.T
    body = stations.recognise(V, T, "Lens_L").bodies[0]
    Vw, vid = geom.weld_exact(V)
    raw_of = np.full(len(Vw), -1, np.int64)
    raw_of[vid] = np.arange(len(vid))
    fr = stations.station_frames(body)
    npt = node_points(fr, body.V)
    g2b = np.full(len(Vw), -1, np.int64)
    g2b[body.source_verts] = np.arange(len(body.V))
    pe = g2b[vid[L.poly_edges]]
    pe = pe[(pe >= 0).all(1)]
    partner = geom.rungs(body.V, fr.loop, body.bl, pe)
    front, back = raw_of[body.source_verts[fr.loop]], raw_of[body.source_verts[partner]]
    a0, b0 = int(front[0]), int(front[1])
    cap_dir = None
    for t in T.tolist():
        if a0 in t and b0 in t and all(x < L.meta["nfront"] for x in t):
            i, j = t.index(a0), t.index(b0)
            cap_dir = (a0, b0) if (j - i) % 3 == 1 else (b0, a0)
            break
    plan = PR.rewall_plan(V, front, back, fr.u[npt], "groove", cap_dir)
    rim = set(front.tolist()) | set(back.tolist())
    keep = np.array([not set(t) <= rim for t in T.tolist()])
    tris = [tri for f in plan.faces for tri in ((f[0], f[1], f[2]), (f[0], f[2], f[3]))]
    return np.vstack([V, plan.new_V]), np.vstack([T[keep], np.array(tris, np.int64)])


@pytest.fixture(scope="module")
def nylor():
    sc = SF.nylor_scene()
    V2, T2 = grooved_lens(sc["lens"])
    body = stations.recognise(V2, T2, "Lens_L").bodies[0]
    return {"scene": sc, "V": V2, "T": T2, "body": body, "frames": stations.station_frames(body),
            "station": sc["parts"]["Cord"], "outline": SF.nylor_scene(cord_frame="outline")["parts"]["Cord"]}


# ------------------------------------------------------------------------------------------- the measurement
@pytest.mark.parametrize("backend", [NumpyBackend, Open3DBackend])
def test_part_overlap_equals_the_pappus_volume_of_a_half_buried_cord(backend):
    """A flat round lens (R 20, t 2.0) and a closed 12-gon cord (r 0.3) swept along its outline: centred on the edge
    at mid-thickness, exactly half of each section lies in the lens, and the overlap is that half's area times the
    perimeter at its centroid (Pappus); 0.3 mm outside the edge it touches nothing (measured 0.02% off)."""
    L = SF.lens(SF.ellipse(20.0, 20.0, 128, 0.0, 0.0), t=2.0, cap="flat")
    body = stations.recognise(L.V, L.T, "L").bodies[0]
    fr = stations.station_frames(body)
    O, R, W, _ = SF.outline_frame_axes(L, 1)
    per = 128 * 2 * 20.0 * np.sin(np.pi / 128)
    for rc in (0.0, -0.1, 0.3):
        sec = SF.oval_section(rc, 0.0, 0.3, 0.3, 12, 0.5)
        cord = SF._sweep(O, R, W, sec, closed=True)
        H = []
        for i in range(len(sec)):                                 # the section clipped to r <= 0 (inside the lens)
            a, b = sec[i], sec[(i + 1) % len(sec)]
            if a[0] <= 0:
                H.append(a)
            if (a[0] <= 0) != (b[0] <= 0):
                H.append(a + a[0] / (a[0] - b[0]) * (b - a))
        expect = 0.0
        if len(H) >= 3:
            H = np.array(H)
            x, y = H[:, 0], H[:, 1]
            cr = x * np.roll(y, -1) - np.roll(x, -1) * y
            A = 0.5 * cr.sum()
            cx = ((x + np.roll(x, -1)) * cr).sum() / (6 * A)
            expect = abs(A) * per * (1 + cx / 20.0)
        r = stations.part_overlap(fr, backend([(cord.V, cord.T), (body.V, body.T)]), 1, [0])
        got = r["parts"][0]["mm3"]
        assert r["unmeasured_columns"] == 0
        if expect == 0.0:
            assert got == 0.0, rc
        else:
            assert got == pytest.approx(expect, rel=2e-3), (rc, got, expect)


def test_stage_proof_cord_overlap_agrees_with_the_instrument(nylor):
    """On the grooved nylor lens: the stage-proof (outline) cord overlaps it ~0.74 mm3 (Blender's EXACT Boolean
    0.7393 on the proof's lens, the core 0.744 here, the independent occupancy instrument 0.767 at its 1 mm stations);
    the cord in §5's groove frame and a cord the tool would create stay below 0.01 mm3. Both read the groove centre
    as held by the cord only (never the channel)."""
    from glasses3d.tests.instruments import seat_grid as IG
    from glasses3d.tests.instruments import seat_proof_metrics as M
    body, fr = nylor["body"], nylor["frames"]
    ch = nylor["scene"]["parts"]["Channel"]
    F = M.frames(nylor["V"], nylor["T"], 1.0)
    for key, lim in (("outline", None), ("station", 0.01)):
        c = nylor[key]
        be = Open3DBackend([(ch.V, ch.T), (c.V, c.T), (body.V, body.T)])
        g = stations.parts_in_groove(fr, be, 2, PR.GROOVE_DEPTH_MM)
        assert g[0] == 0 and g[1] > 150, (key, g)
        core = stations.part_overlap(fr, be, 2, [1])["parts"][1]["mm3"]
        if lim is None:
            inst = M.pair_overlap_mm3(F, IG.build_scene(c.V, c.T), F["scene"])
            assert 0.70 <= core <= 0.78 and abs(core - inst) <= 0.05 * inst, (core, inst)
        else:
            assert core <= lim, (key, core)


def test_the_check_fails_a_nylor_lens_on_its_cord_overlap(nylor):
    """check_nodes on the grooved lens, its channel and a cord, declared semi_rimless / nylor: the stage-proof cord
    fails the lens with the overlap named (the reason the tool's binding check and both checks now give); the cord
    in the groove frame adds no such reason; a plain semi_rimless lens reports no cord at all."""
    ch = nylor["scene"]["parts"]["Channel"]
    lens = {"node": 0, "name": "Lens_L", "V": nylor["V"], "T": nylor["T"], "N": np.full_like(nylor["V"], np.nan),
            "roles": ["lens"], "lens": True, "translucent": False, "extras": {}}
    for key, bad in (("outline", True), ("station", False)):
        c = nylor[key]
        nodes = [lens] + [{"node": i + 1, "name": nm, "V": m.V, "T": m.T, "N": np.full_like(m.V, np.nan),
                           "roles": ["frame"], "lens": False, "translucent": False, "extras": {}}
                          for i, (nm, m) in enumerate((("Channel", ch), ("Cord", c)))]
        rep = CK.check_nodes(nodes, [], [], o3d_factory("open3d"), "semi_rimless", "nylor")
        L = rep["lenses"][0]
        assert [p["part"] for p in L["cord"]["parts"]] == ["Cord"], L["cord"]
        has = any("cord-lens overlap" in x for x in L["reasons"])
        assert has == bad and (L["cord_lens_overlap_mm3"] > 0.01) == bad, (key, L["cord_lens_overlap_mm3"])
        if bad:
            assert not L["pass"] and L["cord_lens_overlap_mm3"] == pytest.approx(0.744, abs=0.02)
    rep = CK.check_nodes([lens, dict(lens, node=1, name="Channel", V=ch.V, T=ch.T, roles=["frame"], lens=False,
                                     N=np.full_like(ch.V, np.nan))], [], [], o3d_factory("open3d"), "semi_rimless")
    assert rep["lenses"][0]["cord"] is None and rep["lenses"][0]["cord_lens_overlap_mm3"] is None


def test_check_verdict_grades_the_cord_overlap_at_the_section_5_limit():
    ft = rules.frame_type("semi_rimless", "nylor")
    meas = stations.Measurement(np.full((2, 5), 0.5), np.full((2, 5), 1.0), np.zeros((2, 5), np.int64),
                                np.zeros(2), np.zeros(2), np.zeros(2, bool), np.zeros(2, np.int64),
                                np.array(["LIPPED", "LIPPED"], dtype=object), [None, None])
    rl = rules.rule_values(ft, meas)
    v = rules.verdicts(ft, meas, rl)
    assert rules.check_verdict(ft, meas, rl, v, cord_overlap_mm3=0.0052)["pass"]
    bad = rules.check_verdict(ft, meas, rl, v, cord_overlap_mm3=0.7393)
    assert not bad["pass"] and any("cord-lens overlap 0.7393" in x for x in bad["reasons"])
    assert rules.CORD_OVERLAP_MAX_MM3 == PR.OVERLAP_MAX_MM3 == 0.01
