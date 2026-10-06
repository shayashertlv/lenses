"""WP4 tools stage, review fix round 2, on the host (numpy core): orientation-free node ownership (a lens and its
mirror are planned alike), the binding check's verdict in the tool's status (E_NOT_HELD, partial), the CLI's
usage-error receipt, export_and_validate's seating arguments and the host star import. WP4_DESIGN.md §4, §8,
§10 P2 ('left = mirrored right within 0.01 mm'); the Blender half is in test_seat_blender.py."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.seat import check as CK
from glasses3d.g3d.seat import deform, rules, stations
from glasses3d.g3d.seat import plan as PL
from glasses3d.g3d.seat.intervals import NumpyBackend
from glasses3d.g3d_host import blender
from glasses3d.tests.fixtures import seat_fixtures as SF

CLI = Path(__file__).resolve().parents[1] / "g3d" / "cli.py"
MIRROR = np.array([-1.0, 1.0, 1.0])


def factory(parts):
    return NumpyBackend(parts)


def mirrored(V, T):
    """x -> -x with the winding flipped (still outward): the other side of the bridge, vertex for vertex."""
    return np.asarray(V) * MIRROR, np.asarray(T)[:, ::-1].copy()


# ------------------------------------------------------------------------------------------- halfway ownership
def test_halfway_points_are_owned_by_both_ends():
    """A point exactly halfway along its loop segment (w = 0.5: segments 0.5-1.0 mm long get 2 points) belongs to
    both nodes; every other point to the nearer one."""
    body = stations.recognise(SF.base_lens().V, SF.base_lens().T, "L").bodies[0]
    fr = stations.station_frames(body)
    o1, o2 = fr.owners
    tie = o1 != o2
    assert tie.sum() >= 30                                         # the 50 x 38 ellipse's flanks (< 1 mm segments)
    assert np.array_equal(o2[tie], (o1[tie] + 1) % len(fr.loop))
    P = body.V[fr.loop]
    mid = 0.5 * (P[o1[tie]] + P[o2[tie]])
    assert np.abs(fr.P[tie] - mid).max() < 1e-9
    assert np.array_equal(o1[~tie], fr.node[~tie]) and np.array_equal(o2[~tie], fr.node[~tie])


def test_halfway_growth_rule():
    """§4 step 3 from the points: own points set the node (max); a halfway point is taken in full by an end with no
    growth of its own, else met by the mean of the two ends (both raised by any shortfall); no order dependence."""
    o1 = np.array([0, 1, 1, 2, 3, 4])
    o2 = np.array([0, 2, 1, 3, 3, 5])                              # points 1 and 3 are halfway points
    grow = np.ones(6, bool)
    g = PL.halfway_growth(6, o1, o2, grow, np.array([0.5, 0.6, 0.2, 0.4, 0.3, 0.7]))
    # node 1 own 0.2, node 2 no own -> takes halfway point 1 in full (0.6); halfway point 3 (nodes 2, 3): node 2
    # has no own growth -> 0.4, node 3 own 0.3; mean (0.6 + 0.3) / 2 >= 0.4. Point 5: halfway 4-5, node 5 alone.
    assert np.allclose(g, [0.5, 0.2 + 0.0, 0.6, 0.3, 0.7, 0.7]), g
    # both ends grow on their own but their mean falls short (0.3 < 0.5): both are raised by the shortfall
    g = PL.halfway_growth(3, np.array([0, 0, 1]), np.array([0, 1, 1]), np.ones(3, bool), np.array([0.2, 0.5, 0.4]))
    assert np.allclose(g, [0.4, 0.6, 0.0]), g
    # points that do not grow count for nothing
    g = PL.halfway_growth(3, np.array([0, 0, 1]), np.array([0, 1, 1]), np.array([True, False, True]),
                          np.array([0.2, 0.5, 0.4]))
    assert np.allclose(g, [0.2, 0.4, 0.0]), g
    # reversing the loop (the mirror) relabels the nodes and gives the same growth per node
    n = 7
    rng = np.random.default_rng(3)
    a = rng.integers(0, n, 40)
    b = np.where(rng.random(40) < 0.4, (a + 1) % n, a)
    gr = rng.random(40)
    g1 = PL.halfway_growth(n, a, b, np.ones(40, bool), gr)
    rev = (n - 1 - np.arange(n))
    g2 = PL.halfway_growth(n, rev[b][::-1], rev[a][::-1], np.ones(40, bool), gr[::-1])
    assert np.allclose(g1, g2[rev])


def test_a_lens_and_its_mirror_are_seated_alike():
    """Review fix round 2 (major): the stage-1 rule gave a halfway point to its segment's START node, and a mirrored
    lens's loop runs the other way, so the metal fixture seated while its mirror was refused (35.5 deg) and FT q001's
    left and mirrored right differed by up to 0.054 mm. Now both sides seat and agree to float noise (§10 P2: within
    0.01 mm), and the former failures stay in §10 P4's band."""
    sc = SF.metal_scene()
    L = sc["lens"]
    parts = [(k, m.V, m.T) for k, m in sc["parts"].items()]
    ft = rules.frame_type("metal")
    a = deform.seat_arrays(L.V, L.T, parts, ft, factory, poly_edges_raw=L.poly_edges, name="L")
    VR, TR = mirrored(L.V, L.T)
    b = deform.seat_arrays(VR, TR, [(k, *mirrored(V, T)) for k, V, T in parts], ft, factory,
                           poly_edges_raw=L.poly_edges, name="R")
    assert a.status == b.status == "seated", (a.as_dict(), b.as_dict())
    assert a.moved_raw == b.moved_raw
    d = np.abs((L.V + a.raw_delta) * MIRROR - (VR + b.raw_delta)).max()
    assert d <= 1e-9, d
    st0, st1 = a.states[0]
    np0, np1 = PL.node_points(st0.frames, st0.body.V), PL.node_points(st1.frames, st1.body.V)
    after = st1.rl.value[np1[st0.plan.verdict.astype(str)[np0] == "FAIL"]]
    assert after.min() >= 0.35 - rules.PASS_TOLERANCE_MM and after.max() <= 0.47 + 1e-3, after


# -------------------------------------------------------------------------------------- the check in the status
def test_a_lens_nothing_holds_is_refused_not_held():
    """Review fix round 2 (major): a semi_rimless lens with no frame part (or its channel 6 mm away) came back
    already_seated while the check failed it ('no required station'). Now E_NOT_HELD, nothing planned."""
    sc = SF.nylor_scene(with_cord=False)
    L, ch = sc["lens"], sc["parts"]["Channel"]
    ft = rules.frame_type("semi_rimless")
    for parts in ([], [("Channel", ch.V + np.array([0.0, 6.0, 0.0]), ch.T)]):
        r = deform.seat_arrays(L.V, L.T, parts, ft, factory, poly_edges_raw=L.poly_edges, name="L")
        assert r.status == "refused" and r.code == "E_NOT_HELD" and r.raw_delta is None, r.as_dict()
        assert "nothing holds this lens" in r.message
        nodes = [{"node": 0, "name": "L", "V": L.V, "T": L.T, "N": np.full_like(L.V, np.nan), "roles": ["lens"],
                  "lens": True, "translucent": False, "extras": {}}]
        nodes += [{"node": i + 1, "name": n, "V": V, "T": T, "N": np.full_like(V, np.nan), "roles": ["frame"],
                   "lens": False, "translucent": False, "extras": {}} for i, (n, V, T) in enumerate(parts)]
        chk = CK.check_nodes(nodes, [], [], factory, "semi_rimless")
        assert not chk["ok"] and any("no required station" in x for x in chk["lenses"][0]["reasons"])


def test_the_tool_says_partial_whenever_the_check_fails():
    """A semi_rimless lens held by a channel over the top 20% only: every FAIL is grown (the plan alone says
    seated), but the channel ends leave 4 of 67 required stations TRANSITION (> 3%), so the check fails; the tool
    now says partial with W_CHECK_FAILS and the check's own reason (before: seated). Over the top 30% both pass."""
    for share, expect in ((0.20, "partial"), (0.30, "seated")):
        sc = SF.short_channel_scene(share)
        L = sc["lens"]
        parts = [(k, m.V, m.T) for k, m in sc["parts"].items()]
        r = deform.seat_arrays(L.V, L.T, parts, rules.frame_type("semi_rimless"), factory,
                               poly_edges_raw=L.poly_edges, name="L")
        assert r.status == expect and r.plan_status == "seated" and r.moved_raw > 0, r.as_dict()
        V1 = L.V + r.raw_delta
        nodes = [{"node": 0, "name": "L", "V": V1, "T": L.T, "N": np.full_like(V1, np.nan), "roles": ["lens"],
                  "lens": True, "translucent": False, "extras": {}}]
        nodes += [{"node": i + 1, "name": n, "V": V, "T": T, "N": np.full_like(V, np.nan), "roles": ["frame"],
                   "lens": False, "translucent": False, "extras": {}} for i, (n, V, T) in enumerate(parts)]
        lens = CK.check_nodes(nodes, [], [], factory, "semi_rimless")["lenses"][0]
        tool = r.bodies[0]["check"]
        assert lens["pass"] == tool["pass"] == (expect != "partial"), (lens["reasons"], tool)
        if expect == "partial":
            assert tool["reasons"] == ["TRANSITION 4/67 > 3%"] and lens["reasons"] == tool["reasons"]
            assert any(w.startswith("W_CHECK_FAILS") and "TRANSITION" in w for w in r.warnings)


def test_halfway_visible_points_hold_neither_end():
    """A visible-by-design halfway point is nearer to neither node, so it holds neither; holding both made the
    nylor fixture's channel-end nodes stop short (TRANSITION 6/161, check FAIL), holding the segment's start (stage
    1) depended on the loop's direction. The nylor fixture and its mirror seat alike and pass the check."""
    sc = SF.nylor_scene()
    L = sc["lens"]
    parts = [(k, m.V, m.T) for k, m in sc["parts"].items()]
    ft = rules.frame_type("semi_rimless", "nylor")
    a = deform.seat_arrays(L.V, L.T, parts, ft, factory, poly_edges_raw=L.poly_edges, name="L")
    VR, TR = mirrored(L.V, L.T)
    b = deform.seat_arrays(VR, TR, [(k, *mirrored(V, T)) for k, V, T in parts], ft, factory,
                           poly_edges_raw=L.poly_edges, name="R")
    assert a.status == b.status == "seated" and a.moved_raw == b.moved_raw, (a.as_dict(), b.as_dict())
    assert np.abs((L.V + a.raw_delta) * MIRROR - (VR + b.raw_delta)).max() <= 1e-9
    assert a.bodies[0]["check"]["pass"], a.bodies[0]["check"]


# ----------------------------------------------------------------------------------------- CLI, host wiring
def test_cli_usage_errors_write_a_refused_receipt(tmp_path):
    """Review fix round 2 (minor): an option without its value raised IndexError and wrote no receipt. The usage
    error is reported before anything imports bpy, so this runs on the host."""
    args = tmp_path / "args.json"
    args.write_text(json.dumps({"lens": "Lens_L", "frame_type": "metal"}), encoding="utf-8")
    for extra, why in ((["--save-copy"], "--save-copy needs a path"), (["--bogus"], "unknown option"),
                       (["--export", "--save-copy", "x.blend"], "--export needs a path"),
                       (["more"], "extra arguments")):
        receipt = tmp_path / f"r-{len(extra)}-{extra[0].strip('-')}.json"
        proc = subprocess.run([sys.executable, str(CLI), "--", "seat", str(args), str(receipt)] + extra,
                              capture_output=True, text=True, timeout=60)
        rec = json.loads(receipt.read_text(encoding="utf-8"))
        assert rec["status"] == "refused" and rec["code"] == "E_ARGS" and why in rec["message"], (rec, proc.stderr)
    proc = subprocess.run([sys.executable, str(CLI), "--", "seat", str(args)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode != 0 and "needs <args.json> <receipt.json>" in proc.stderr


def test_export_and_validate_reports_bad_seating_arguments(tmp_path, monkeypatch):
    """Review fix round 2 (minor): an unknown key in `seating` raised TypeError out of export_and_validate."""
    def fake_export(blend, out_glb, timeout_s=300.0):
        Path(out_glb).write_bytes(b"glTF")
        return {"status": "exported"}
    monkeypatch.setattr(blender, "export_copy", fake_export)
    monkeypatch.setattr("glasses3d.g3d_host.spec.validate_spec", lambda p: {"ok": True, "errors": []})
    res = blender.export_and_validate(tmp_path / "x.blend", tmp_path / "x.glb", seating={"frame_typ": "metal"})
    assert res["seating"]["ok"] is False and "frame_typ" in res["seating"]["error"], res["seating"]
    res = blender.export_and_validate(tmp_path / "x.blend", tmp_path / "x.glb", seating=["metal"])
    assert res["seating"]["ok"] is False and "dict" in res["seating"]["error"]


def test_star_import_works_on_the_host():
    """Review fix round 2 (minor): __all__ listed the bpy-only tools, so the star import raised on the host."""
    try:
        import bpy  # noqa: F401
        pytest.skip("bpy importable here")
    except ImportError:
        pass
    ns: dict = {}
    exec("from glasses3d.g3d import *", ns)
    assert ns["SEAT_TOOLS"][0] == "seat_lens" and "seat_lens" not in ns
