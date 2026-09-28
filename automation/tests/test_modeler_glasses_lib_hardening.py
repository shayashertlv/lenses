"""Hardening of glasses_lib where the first paid run (test-pilot-001) broke: gl.box clamps an oversized bevel and stays
closed, gl.assign on the whole object leaves a single slot after a boolean, and gl.front_outline builds the one-piece front
silhouette the author had to derive by hand (r0002/program/frame.py). Every check runs inside the host Blender through the
harness (the library imports bpy); the module skips cleanly when Blender is absent."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from modeler.paths import blender_executable
from modeler.worker import run_harness


def build(program: str, root: Path, *, export: bool = True) -> dict:
    (root / "p.py").write_text(program, encoding="utf-8")
    return run_harness({"modules": [{"name": "frame", "path": str(root / "p.py")}], "export": export, "save_blend": False,
                        "renders": []}, root / "out", time_limit_s=240)


def failure_text(r: dict) -> str:
    return json.dumps({"module_results": r.get("module_results"), "error": r.get("error"),
                       "stderr_tail": (r.get("process") or {}).get("stderr_tail")}, indent=1)[:4000]


def inventory_row(r: dict, name: str) -> dict:
    rows = {row["object"]: row for row in r["inventory"]}
    return rows[name]


# the six gold parts of test-pilot-001/r0001/program/hardware.py that arrived unclosed at export
BOX_PROGRAM = '''
gold = gl.material_metal("gold", (212, 175, 90))
thin = gl.box((70.16, 4.4, -12.2), (.23, 1.0, 15.5), "gold_T_arm_R", "frame", "T_inlay", bevel_mm=.12)
cross = gl.box((70.16, 4.4, -5.1), (.24, 5.2, 1.0), "gold_T_side_cross_R", "frame", "T_inlay", bevel_mm=.12)
plate = gl.box((66.0, 4.0, -5.6), (3.0, 4.0, 1.7), "endpiece_hinge_plate_R", "frame", "hinge_R", bevel_mm=.25)
plain = gl.box((60.0, 0.0, 0.0), (2.0, 2.0, 2.0), "plain_box", "frame", "plain")
for o in (thin, cross, plate, plain):
    gl.assign(o, gold)
'''

ASSIGN_PROGRAM = '''
gold = gl.material_metal("gold", (212, 175, 90))
silver = gl.material_metal("silver", (200, 200, 200))
a = gl.box((0.0, 0.0, 0.0), (4.0, 4.0, 4.0), "crystal_rim_R", "frame", "front", smooth=False)
b = gl.box((2.0, 0.0, 0.0), (4.0, 4.0, 4.0), "plain_bridge", "frame", "bridge", smooth=False)
gl.boolean(a, b, "UNION"); gl.apply_modifiers(a); gl.delete(b)
a.data.materials.append(None)                      # an imported, empty slot (what the boolean left in r0001)
a.data.materials.append(silver)
before = len(a.data.materials)
gl.assign(a, gold)
slots = [m.name if m else None for m in a.data.materials]
assert slots == ["gold"], f"whole-object assign must leave a single slot, got {slots} (before: {before})"
idx = sorted({p.material_index for p in a.data.polygons})
assert idx == [0], f"every polygon must use slot 0, got {idx}"
# the multi-slot path for explicit faces is unchanged
gl.assign(a, silver, faces=[0, 1])
slots = [m.name if m else None for m in a.data.materials]
assert slots == ["gold", "silver"], f"explicit faces keep both slots, got {slots}"
assert a.data.polygons[0].material_index == 1 and a.data.polygons[2].material_index == 0
gl.note("assign_ok")
'''

OUTLINE_PROGRAM = '''
import json
lensR = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(32.0, -2.0))
lensL = lensR.copy(); lensL[:, 0] *= -1; lensL = gl.ensure_ccw(lensL)
kw = dict(rim_width_mm=3.5, bridge_top_crown_mm=1.5, bridge_bottom_mm=1.0, bridge_top_mm=8.0, bridge_bottom_attach_mm=0.0, n=192)
outline = gl.front_outline([lensL, lensR], **kw)
assert outline.ndim == 2 and outline.shape[1] == 2 and len(outline) >= 100, outline.shape
assert gl.signed_area(outline) > 0, "the outline must be CCW"
assert not np.allclose(outline[0], outline[-1]), "no repeated closing vertex"
assert np.all(np.linalg.norm(np.roll(outline, -1, 0) - outline, axis=1) > 1e-6), "no duplicate vertices"
# encloses both lens outlines with the rim margin
for lens in (lensR, lensL):
    assert gl.point_in_polygon(lens, outline).all(), "a lens vertex lies outside the front"
    d = gl._dist_to_polylines(lens, [outline])
    assert d.min() >= 3.5 - 0.06, f"rim margin {d.min():.3f} < 3.5"
# symmetric for symmetric input
mirrored = outline.copy(); mirrored[:, 0] *= -1
gap = np.linalg.norm(outline[:, None, :] - mirrored[None, :, :], axis=2).min(1)
assert gap.max() < 1e-6, f"not mirror-symmetric: {gap.max()}"
# the bridge: underside at bridge_bottom_mm at x = 0; the crown rises bridge_top_crown_mm above the attachment, which is
# the nasal vertex nearest bridge_top_mm (within half a sample spacing of it)
centre = outline[np.abs(outline[:, 0]) < 1.0]
assert abs(centre[:, 1].min() - 1.0) < 0.05, centre[:, 1].min()
assert abs(centre[:, 1].max() - 9.5) < 0.6, centre[:, 1].max()
flat = gl.front_outline([lensL, lensR], **dict(kw, bridge_top_crown_mm=0.0))
flat_centre = flat[np.abs(flat[:, 0]) < 1.0]
assert abs(flat_centre[:, 1].max() - 8.0) < 0.6, flat_centre[:, 1].max()
assert abs((centre[:, 1].max() - flat_centre[:, 1].max()) - 1.5) < 1e-3, "the crown is the rise above the attachment"
low = gl.front_outline([lensL, lensR], **dict(kw, bridge_bottom_mm=3.0))
assert abs(low[np.abs(low[:, 0]) < 1.0][:, 1].min() - 3.0) < 0.05, "bridge_bottom_mm is the underside at x = 0"
# the endpiece flare widens the front on the temporal side only
flared = gl.front_outline([lensR, lensL], endpiece_flare_mm=6.0, **kw)
assert abs(flared[:, 0].max() - outline[:, 0].max() - 6.0) < 0.3, (flared[:, 0].max(), outline[:, 0].max())
assert abs(flared[:, 1].max() - outline[:, 1].max()) < 1e-6 and abs(flared[:, 1].min() - outline[:, 1].min()) < 1e-6
# deterministic
again = gl.front_outline([lensL, lensR], **kw)
assert np.array_equal(again, outline)
# defaults: the attachments derive from the rims when not given
auto = gl.front_outline([lensR, lensL], rim_width_mm=3.5, bridge_top_crown_mm=1.5, bridge_bottom_mm=1.0)
assert gl.point_in_polygon(lensR, auto).all() and gl.point_in_polygon(lensL, auto).all()
gl.note("outline " + json.dumps({"n": int(len(outline)), "xmax": float(outline[:, 0].max()), "flared_xmax": float(flared[:, 0].max())}))
front = gl.plate_with_holes(outline, [lensR, lensL], 0.0, 5.2, "one_piece_front", "frame", "front", smooth=False)
acetate = gl.material_acetate("acetate", (40, 30, 24))
gl.assign(front, acetate)
'''

OUTLINE_ERRORS_PROGRAM = '''
lensR = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(32.0, -2.0))
lensL = lensR.copy(); lensL[:, 0] *= -1; lensL = gl.ensure_ccw(lensL)
try:
    gl.front_outline([lensR], rim_width_mm=3.5, bridge_top_crown_mm=1.5, bridge_bottom_mm=1.0)
    raise AssertionError("one outline must be refused")
except ValueError as e:
    assert "two" in str(e), e
try:
    gl.front_outline([lensR, lensL], rim_width_mm=9.0, bridge_top_crown_mm=1.5, bridge_bottom_mm=1.0)
    raise AssertionError("overlapping rims must be refused")
except ValueError as e:
    assert "overlap" in str(e), e
try:
    gl.front_outline([lensR, lensL], rim_width_mm=0.0, bridge_top_crown_mm=1.5, bridge_bottom_mm=1.0)
    raise AssertionError("a zero rim must be refused")
except ValueError as e:
    assert "rim_width_mm" in str(e), e
gl.note("errors_ok")
'''

# the parts of test-pilot-001/r0001/program/hardware.py whose bevel was exactly half the thinnest side (0.30/0.15 and
# 0.4/0.2), a hairline box whose clamped bevel (0.0005 mm) falls under the export's 1e-3 mm weld, and a requested bevel
# that small on a box that fits it
BOX_EDGE_PROGRAM = '''
gold = gl.material_metal("gold", (212, 175, 90))
objs = [gl.box((65.0, 3.6, -1.18), (.92, 5.6, .3), "gold_T_crossbar_R", "frame", "T_inlay", bevel_mm=.15),
        gl.box((67.0, 3.6, -1.20), (4.65, 1.0, .30), "gold_T_front_bar_R", "frame", "T_inlay", bevel_mm=.15),
        gl.box((67.75, 4.25, -16.0), (0.4, 2.6, 12.0), "inner_gold_plate_R", "frame", "hinge_reinforcement", bevel_mm=.2),
        gl.box((50.0, 0.0, 0.0), (0.021, 1.0, 1.0), "hairline_box", "frame", "inlay", bevel_mm=.0105),
        gl.box((45.0, 0.0, 0.0), (0.03, 1.0, 1.0), "thread_box", "frame", "inlay", bevel_mm=.015),
        gl.box((40.0, 0.0, 0.0), (2.0, 2.0, 2.0), "speck_bevel_box", "frame", "inlay", bevel_mm=.001)]
for o in objs:
    gl.assign(o, gold)
'''

# test-pilot-001's right lens (evidence front.lenses[R].outline_mm_64, mm): nasal-most at y = -8.3, far below the
# bridge attachments, so rims pushed together overlap BELOW the bridge where the old four-point check never looked
TOM_FORD_R_64 = [
    [37.299,9.071], [39.423,9.019], [41.547,9.025], [43.658,8.809], [45.764,8.541], [47.885,8.454], [49.91,7.852], [51.759,6.807],
    [53.448,5.527], [54.698,3.817], [55.715,1.953], [56.375,-0.059], [56.666,-2.163], [56.891,-4.275], [56.862,-6.396], [56.473,-8.483],
    [55.937,-10.538], [55.301,-12.564], [54.49,-14.527], [53.529,-16.421], [52.389,-18.212], [51.075,-19.881], [49.663,-21.466], [48.057,-22.854],
    [46.3,-24.047], [44.482,-25.145], [42.532,-25.982], [40.522,-26.668], [38.509,-27.347], [36.435,-27.797], [34.335,-28.121], [32.226,-28.368],
    [30.104,-28.348], [27.985,-28.194], [25.896,-27.827], [23.92,-27.054], [21.995,-26.155], [20.118,-25.164], [18.403,-23.913], [16.729,-22.605],
    [15.173,-21.163], [13.778,-19.562], [12.121,-18.239], [10.622,-16.764], [10.229,-14.7], [10.253,-12.576], [10.145,-10.454], [10.136,-8.331],
    [10.333,-6.217], [10.823,-4.152], [11.413,-2.111], [12.101,-0.104], [13.3,1.637], [15.028,2.855], [16.984,3.681], [18.854,4.685],
    [20.732,5.676], [22.719,6.426], [24.731,7.105], [26.815,7.507], [28.914,7.822], [30.979,8.323], [33.073,8.674], [35.18,8.938],
]
PILOT_EVIDENCE = Path(__file__).resolve().parents[1] / "data/modeler/agentic/test-pilot-001/evidence/evidence.json"

# shared by the programs below: the pair at a given nasal gap, and an independent count of properly crossing
# non-adjacent edges of a closed polygon (0 = simple)
PAIR_HELPERS = '''
import json
def pair_at_gap(lens_r, gap):
    r = gl.ensure_ccw(np.asarray(lens_r, float))
    r[:, 0] -= r[:, 0].min() - gap / 2.0
    l = r.copy(); l[:, 0] *= -1.0
    return r, gl.ensure_ccw(l)

def crossings(p):
    a, b = p, np.roll(p, -1, 0)
    def o(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])
    A, B, C, D = a[:, None], b[:, None], a[None], b[None]
    x = (o(A, B, C) * o(A, B, D) < 0) & (o(C, D, A) * o(C, D, B) < 0)
    return int(np.triu(x, 2).sum())

def lens_crossings(lens, outline):
    a, b, c, d = lens, np.roll(lens, -1, 0), outline, np.roll(outline, -1, 0)
    def o(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])
    A, B, C, D = a[:, None], b[:, None], c[None], d[None]
    return int(((o(A, B, C) * o(A, B, D) < 0) & (o(C, D, A) * o(C, D, B) < 0)).sum())
'''

TOM_FORD_FRONT_PROGRAM = PAIR_HELPERS + '''
lens_r = json.loads(LENS_R_JSON)
r, l = pair_at_gap(lens_r, GAP)
kw = dict(rim_width_mm=3.15, bridge_top_crown_mm=1.8, bridge_bottom_mm=1.25)
outline = gl.front_outline([r, l], **kw)
assert crossings(outline) == 0, f"the valid Tom Ford outline self-intersects {crossings(outline)} times"
assert gl.point_in_polygon(r, outline).all() and gl.point_in_polygon(l, outline).all()
assert lens_crossings(r, outline) == 0 and lens_crossings(l, outline) == 0
explicit = gl.front_outline([r, l], bridge_top_mm=5.0, bridge_bottom_attach_mm=0.4, **kw)
assert crossings(explicit) == 0
gl.note("tom_ford_ok " + json.dumps({"n": int(len(outline))}))
front = gl.plate_with_holes(outline, [r, l], 0.0, 5.2, "tom_ford_front", "frame", "front", smooth=False)
gl.assign(front, gl.material_acetate("acetate", (230, 225, 215)))
'''

OUTLINE_CROSSING_PROGRAM = PAIR_HELPERS + '''
lens_r = json.loads(LENS_R_JSON)
# 1. rims that overlap BELOW the bridge (the real lens at a 3.0 mm nasal gap): refused as an overlap, not returned as a
#    self-intersecting silhouette for plate_with_holes to build into an open front
r, l = pair_at_gap(lens_r, 3.0)
for kw in (dict(bridge_top_crown_mm=1.8, bridge_bottom_mm=1.25),
           dict(bridge_top_crown_mm=1.8, bridge_bottom_mm=1.25, bridge_top_mm=5.0, bridge_bottom_attach_mm=0.4)):
    try:
        bad = gl.front_outline([r, l], rim_width_mm=3.15, **kw)
        raise AssertionError(f"rims overlapping below the bridge must be refused ({crossings(bad)} crossings returned)")
    except ValueError as e:
        assert "overlap" in str(e), e
        assert "rim_width_mm" in str(e), e
# 2. a wavy lens whose concave dips are tighter than the rim: the vertex-normal offset folds into loops
t = np.linspace(0.0, 2.0 * np.pi, 160, endpoint=False)
rad = 1.0 + 0.1 * np.sin(9 * t)
wavy = np.column_stack([36.0 + 25.0 * rad * np.cos(t), -2.0 + 18.0 * rad * np.sin(t)])
wr, wl = pair_at_gap(wavy, 12.0)
try:
    bad = gl.front_outline([wr, wl], rim_width_mm=3.5, bridge_top_crown_mm=1.5, bridge_bottom_mm=1.0)
    raise AssertionError(f"a folded rim offset must be refused ({crossings(bad)} crossings returned)")
except ValueError as e:
    assert "self-intersect" in str(e), e
    assert "rim_width_mm" in str(e) and "x=" in str(e), e
    assert "gl.smooth_closed" in str(e) and "Fourier" not in str(e), e
# ... and following it (20 smoothing passes over the nine lobes) builds a simple silhouette
sr, sl = pair_at_gap(gl.smooth_closed(wavy, iterations=20), 12.0)
smoothed = gl.front_outline([sr, sl], rim_width_mm=3.5, bridge_top_crown_mm=1.5, bridge_bottom_mm=1.0)
assert crossings(smoothed) == 0 and lens_crossings(sr, smoothed) == 0
# 3. a negative endpiece flare (it would pull the temporal side into the lens)
r, l = pair_at_gap(lens_r, 20.24)
try:
    gl.front_outline([r, l], rim_width_mm=3.15, bridge_top_crown_mm=1.8, bridge_bottom_mm=1.25, endpiece_flare_mm=-2.0)
    raise AssertionError("a negative endpiece_flare_mm must be refused")
except ValueError as e:
    assert "endpiece_flare_mm" in str(e), e
gl.note("crossing_errors_ok")
'''

# a 6-point hexagon and a 4-point diamond whose lower-nasal edge the bridge underside cuts BETWEEN vertices when it climbs
# from a low bottom attachment to bridge_bottom_mm = 1.25: every lens vertex stays inside, so the old vertex-only check
# returned the outline and plate_with_holes built an open front (48 boundary edges). Moving the attachment DOWN (the old
# advice) cuts deeper; raising it or lowering bridge_bottom_mm clears the lens.
LENS_EDGE_PROGRAM = PAIR_HELPERS + '''
def ring(n, w, h, rot):
    a = np.deg2rad(rot) + np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack([32.0 + w / 2.0 * np.cos(a), -2.0 + h / 2.0 * np.sin(a)])

def refused(label, r, l, **kw):
    try:
        bad = gl.front_outline([r, l], **kw)
    except ValueError as e:
        return str(e)
    raise AssertionError(f"{label} {kw}: returned with every lens vertex inside ({bool(gl.point_in_polygon(r, bad).all())}) "
                         f"and {lens_crossings(r, bad)} lens R edge crossing(s)")

for label, lens, rim, attach in (("hexagon", ring(6, 50.0, 40.0, 55.0), 3.5, -18.0), ("diamond", ring(4, 52.0, 36.0, 70.0), 2.5, -20.0)):
    r, l = pair_at_gap(lens, 16.0)
    kw = dict(rim_width_mm=rim, bridge_top_crown_mm=1.5)
    for att in (attach, attach - 2.0):
        msg = refused(label, r, l, bridge_bottom_mm=1.25, bridge_bottom_attach_mm=att, **kw)
        assert "lens R" in msg and "underside" in msg and "x=" in msg, msg
        assert "raise bridge_bottom_attach_mm" in msg and "lower bridge_bottom_mm" in msg, msg
        assert "attach_mm down" not in msg, msg
    # the two moves the message names build a front that clears both lenses
    for fix in (dict(bridge_bottom_mm=1.25, bridge_bottom_attach_mm=attach + 6.0), dict(bridge_bottom_mm=-2.0, bridge_bottom_attach_mm=attach)):
        good = gl.front_outline([r, l], **fix, **kw)
        assert crossings(good) == 0, (label, fix)
        assert lens_crossings(r, good) == 0 and lens_crossings(l, good) == 0, (label, fix)
        assert gl.point_in_polygon(r, good).all() and gl.point_in_polygon(l, good).all(), (label, fix)
    front = gl.plate_with_holes(good, [r, l], 0.0, 5.2, f"{label}_front", "frame", "front", smooth=False)
    gl.assign(front, gl.material_acetate("acetate", (40, 30, 24)))
gl.note("lens_edges_ok")
'''

# any contact between two rings of one plate is refused, not only a proper crossing
CONTACTS_PROGRAM = '''
def pairs(a0, a1, b0, b1):
    return sorted(map(tuple, gl._segment_contacts(np.array(a0, float), np.array(a1, float), np.array(b0, float), np.array(b1, float)).tolist()))
a0, a1 = [[0.0, 0.0]], [[4.0, 0.0]]
assert pairs(a0, a1, [[2.0, -1.0]], [[2.0, 1.0]]) == [(0, 0)]                  # a proper crossing
assert pairs(a0, a1, [[2.0, 0.0]], [[2.0, 1.0]]) == [(0, 0)]                   # a T-touch: an end on the interior
assert pairs(a0, a1, [[2.0, 1e-8]], [[2.0, 1.0]]) == [(0, 0)]                  # ... within the CDT weld epsilon
assert pairs(a0, a1, [[4.0, 0.0]], [[5.0, 3.0]]) == [(0, 0)]                   # a shared end
assert pairs(a0, a1, [[3.0, 0.0]], [[6.0, 0.0]]) == [(0, 0)]                   # a collinear overlap
assert pairs(a0, a1, [[1.0, 0.0]], [[2.0, 0.0]]) == [(0, 0)]                   # collinear, contained
assert pairs(a0, a1, [[5.0, 0.0]], [[6.0, 0.0]]) == []                         # collinear, apart
assert pairs(a0, a1, [[2.0, 1e-3]], [[2.0, 1.0]]) == []                        # a near miss (1 micron)
assert pairs(a0, a1, [[0.0, 1.0]], [[4.0, 1.0]]) == []                         # parallel
assert pairs([[0.0, 0.0], [10.0, 0.0]], [[4.0, 0.0], [14.0, 0.0]], [[2.0, 0.0], [12.0, -1.0]], [[2.0, 1.0], [12.0, 1.0]]) == [(0, 0), (1, 1)]
# the proper-crossing test is unchanged: none of the touches count there
assert len(gl._proper_crossings(np.array(a0), np.array(a1), np.array([[2.0, 0.0]]), np.array([[2.0, 1.0]]))) == 0
gl.note("contacts_ok")
'''


def lens_program(template: str, lens_r, gap: float) -> str:
    return template.replace("LENS_R_JSON", repr(json.dumps(lens_r))).replace("GAP", repr(float(gap)))


def pilot_lens_r() -> list | None:
    if not PILOT_EVIDENCE.is_file():
        return None
    front = json.loads(PILOT_EVIDENCE.read_text(encoding="utf-8"))["front"]
    return next(lens["outline_mm"] for lens in front["lenses"] if lens["side"] == "R")


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class BoxBevelClamp(unittest.TestCase):
    def test_oversized_bevel_is_clamped_noted_and_the_box_stays_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = build(BOX_PROGRAM, Path(tmp))
            self.assertTrue(r["ok"], failure_text(r))
            for name in ("gold_T_arm_R", "gold_T_side_cross_R", "endpiece_hinge_plate_R", "plain_box"):
                row = inventory_row(r, name)
                self.assertTrue(row["closed"], f"{name}: {row}")
                self.assertEqual(row["boundary_edges"], 0, name)
                self.assertGreater(row["signed_volume_mm3"], 0.0, name)
            notes = "\n".join(r["notes"])
            self.assertIn("gold_T_arm_R", notes)
            self.assertIn("gold_T_side_cross_R", notes)
            self.assertIn("clamp", notes)
            self.assertNotIn("endpiece_hinge_plate_R", notes, "a bevel that fits is not noted")
            self.assertNotIn("plain_box", notes)
            # the thin arm is still bevelled (rounded corners: more than the 12 triangles of a plain box)
            self.assertGreater(inventory_row(r, "gold_T_arm_R")["triangles"], 12)
            self.assertEqual(inventory_row(r, "plain_box")["triangles"], 12)


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class AssignClearsStraySlots(unittest.TestCase):
    def test_whole_object_assign_leaves_one_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = build(ASSIGN_PROGRAM, Path(tmp))
            self.assertTrue(r["ok"], failure_text(r))
            self.assertIn("assign_ok", r["notes"])
            self.assertEqual(inventory_row(r, "crystal_rim_R")["materials"], ["gold", "silver"])
            self.assertNotIn("unassigned", "\n".join(r["notes"]))


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class FrontOutline(unittest.TestCase):
    def test_front_outline_geometry_and_watertight_plate(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = build(OUTLINE_PROGRAM, Path(tmp))
            self.assertTrue(r["ok"], failure_text(r))
            stat = next(n for n in r["notes"] if n.startswith("outline "))
            stat = json.loads(stat[len("outline "):])
            self.assertGreater(stat["flared_xmax"], stat["xmax"] + 5.5)
            row = inventory_row(r, "one_piece_front")
            self.assertTrue(row["closed"], row)
            self.assertEqual(row["boundary_edges"], 0)
            self.assertEqual(row["nonmanifold_edges"], 0)
            self.assertEqual(row["misoriented_edges"], 0)
            self.assertGreater(row["signed_volume_mm3"], 0.0)
            self.assertEqual(row["materials"], ["acetate"])

    def test_front_outline_refuses_bad_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = build(OUTLINE_ERRORS_PROGRAM, Path(tmp), export=False)
            self.assertTrue(r["ok"], failure_text(r))
            self.assertIn("errors_ok", r["notes"])


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class BoxBevelUnderTheWeld(unittest.TestCase):
    def test_half_side_bevels_and_hairline_boxes_arrive_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = build(BOX_EDGE_PROGRAM, Path(tmp))
            self.assertTrue(r["ok"], failure_text(r))
            for name in ("gold_T_crossbar_R", "gold_T_front_bar_R", "inner_gold_plate_R", "hairline_box", "thread_box",
                         "speck_bevel_box"):
                row = inventory_row(r, name)
                self.assertTrue(row["closed"], f"{name}: {row}")
                self.assertEqual((row["boundary_edges"], row["nonmanifold_edges"], row["misoriented_edges"]), (0, 0, 0), name)
                self.assertGreater(row["signed_volume_mm3"], 0.0, name)
            for name in ("gold_T_crossbar_R", "gold_T_front_bar_R", "inner_gold_plate_R"):
                self.assertGreater(inventory_row(r, name)["triangles"], 12, f"{name} keeps a (clamped) bevel")
            notes = "\n".join(r["notes"])
            for name in ("hairline_box", "speck_bevel_box"):
                self.assertEqual(inventory_row(r, name)["triangles"], 12, f"{name}: the bevel is dropped, not built")
                self.assertTrue(any(name in n and "dropped" in n for n in r["notes"]), notes)


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class FrontOutlineIsSimple(unittest.TestCase):
    def assert_closed_front(self, r: dict):
        self.assertTrue(r["ok"], failure_text(r))
        self.assertTrue(any(n.startswith("tom_ford_ok") for n in r["notes"]), r["notes"])
        row = inventory_row(r, "tom_ford_front")
        self.assertTrue(row["closed"], row)
        self.assertEqual((row["boundary_edges"], row["nonmanifold_edges"], row["misoriented_edges"]), (0, 0, 0))
        self.assertGreater(row["signed_volume_mm3"], 0.0)

    def test_tom_ford_at_the_real_nasal_gap_builds_a_closed_front(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_closed_front(build(lens_program(TOM_FORD_FRONT_PROGRAM, TOM_FORD_R_64, 20.24), Path(tmp)))

    def test_tom_ford_full_resolution_outline_builds_a_closed_front(self):
        lens_r = pilot_lens_r()
        if lens_r is None:
            self.skipTest(f"{PILOT_EVIDENCE} is not present")
        with tempfile.TemporaryDirectory() as tmp:
            self.assert_closed_front(build(lens_program(TOM_FORD_FRONT_PROGRAM, lens_r, 20.24), Path(tmp)))

    def test_rims_crossing_anywhere_folds_and_negative_flare_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = build(lens_program(OUTLINE_CROSSING_PROGRAM, TOM_FORD_R_64, 20.24), Path(tmp), export=False)
            self.assertTrue(r["ok"], failure_text(r))
            self.assertIn("crossing_errors_ok", r["notes"])

    def test_a_lens_edge_cut_between_vertices_is_refused_with_the_working_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = build(LENS_EDGE_PROGRAM, Path(tmp))
            self.assertTrue(r["ok"], failure_text(r))
            self.assertIn("lens_edges_ok", r["notes"])
            for name in ("hexagon_front", "diamond_front"):
                row = inventory_row(r, name)
                self.assertTrue(row["closed"], f"{name}: {row}")
                self.assertEqual((row["boundary_edges"], row["nonmanifold_edges"], row["misoriented_edges"]), (0, 0, 0), name)
                self.assertGreater(row["signed_volume_mm3"], 0.0, name)

    def test_touches_and_collinear_overlaps_count_as_contacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = build(CONTACTS_PROGRAM, Path(tmp), export=False)
            self.assertTrue(r["ok"], failure_text(r))
            self.assertIn("contacts_ok", r["notes"])

    def test_full_resolution_outline_at_a_3mm_gap_is_refused(self):
        lens_r = pilot_lens_r()
        if lens_r is None:
            self.skipTest(f"{PILOT_EVIDENCE} is not present")
        with tempfile.TemporaryDirectory() as tmp:
            r = build(lens_program(OUTLINE_CROSSING_PROGRAM, lens_r, 20.24), Path(tmp), export=False)
            self.assertTrue(r["ok"], failure_text(r))
            self.assertIn("crossing_errors_ok", r["notes"])


class HelperReferenceShowsTheNewHelper(unittest.TestCase):
    def test_front_outline_and_translucency_wording_in_the_reference(self):
        from modeler.author import helper_reference
        ref = helper_reference()
        self.assertIn("gl.front_outline(lens_outlines, *, rim_width_mm", ref)
        self.assertIn("gl.material_translucent(", ref)
        head = ref.split("gl.scene()")[0]                       # the module docstring, before the first signature
        self.assertIn("hardware (metal) stays opaque", head)
        self.assertIn("single-sided", head)
        translucent = ref.split("gl.material_translucent(")[1].split("\ngl.")[0]
        self.assertIn("frame and temple parts", translucent)
        self.assertIn("hardware (metal) stays opaque", translucent)
        self.assertNotIn("temples stay opaque", ref.lower())
        outline = ref.split("gl.front_outline(")[1].split("\ngl.")[0]
        self.assertNotIn("Fourier", outline)
        self.assertIn("gl.smooth_closed", outline)
        self.assertIn("raise bridge_bottom_attach_mm", outline)
        self.assertIn("lower bridge_bottom_mm", outline)


# --------------------------------------------------------------------------- fonts (test-pilot-002 MVP-10 / INF-09)
FONT_PROGRAM = '''
import json, shutil, tempfile
from pathlib import Path
out = {"styles": {s: list(gl.FONT_STYLES[s]) for s in ("sans", "serif", "mono")}, "resolved": {}}
for s in ("sans", "serif", "mono"):
    out["resolved"][s] = gl.font_file(s)
for i in range(3):                                             # three marks in one style: one note
    gl.text_mesh("TF", 3.0, 0.2, f"mark_{i}", "frame", font="sans")
# the first EXISTING candidate wins: a copy of a host font stands in for the worker's file at the head of the list
stand_in = str(Path(tempfile.mkdtemp()) / "WorkerSerif.ttf")
shutil.copy2(gl.font_file("serif"), stand_in)
gl.FONT_STYLES["serif"] = ["/usr/share/fonts/truetype/no-such/Missing.ttf", stand_in] + gl.FONT_STYLES["serif"]
vf = gl.load_font("serif")
out["serif_loaded"] = bpy.path.abspath(vf.filepath) if vf else None
out["stand_in"] = stand_in
out["missing"] = gl.load_font("no_such_font_file.ttf") is None
gl.note("fonts " + json.dumps(out))
'''

WORKER_FONTS = {"sans": ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf"),
                "serif": ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "LiberationSerif-Regular.ttf", "times.ttf"),
                "mono": ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "consola.ttf")}


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class TextStyleFonts(unittest.TestCase):
    """The worker had no fonts and FONT_STYLES listed only Windows files, so every lettering style fell back to Blender's
    default inside Docker (three 'font 'sans' not found' notes per run-2 build). Styles now resolve to the first existing
    file of an ordered list (the worker's DejaVu, then Liberation, then the Windows host's), and the build names it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.r = build(FONT_PROGRAM, Path(cls.tmp.name), export=False)
        cls.out = next((json.loads(n[len("fonts "):]) for n in cls.r.get("notes") or [] if n.startswith("fonts ")), None)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_styles_list_the_worker_fonts_first(self):
        self.assertTrue(self.r["ok"], failure_text(self.r))
        for style, (worker, liberation, host) in WORKER_FONTS.items():
            files = self.out["styles"][style]
            self.assertEqual(files[0], worker, style)
            names = [Path(f).name for f in files]
            self.assertIn(liberation, names, style)
            self.assertIn(host, names, style)
            self.assertLess(names.index(liberation), names.index(host), f"{style}: the worker's fonts before the host's")

    def test_the_file_used_is_noted_once_and_exists(self):
        self.assertTrue(self.r["ok"], failure_text(self.r))
        for style in ("sans", "serif", "mono"):
            self.assertTrue(self.out["resolved"][style] and Path(self.out["resolved"][style]).is_file(), self.out)
        sans = [n for n in self.r["notes"] if n.startswith("font 'sans'")]
        self.assertEqual(len(sans), 1, sans)
        self.assertIn(self.out["resolved"]["sans"], sans[0])
        self.assertNotIn("not found", sans[0])

    def test_the_first_existing_candidate_wins_and_the_default_is_the_last_resort(self):
        self.assertTrue(self.r["ok"], failure_text(self.r))
        self.assertEqual(Path(self.out["serif_loaded"]).resolve(), Path(self.out["stand_in"]).resolve())
        self.assertTrue(any(n.startswith("font 'serif'") and "WorkerSerif.ttf" in n for n in self.r["notes"]), self.r["notes"])
        self.assertTrue(self.out["missing"])
        missing = [n for n in self.r["notes"] if "no_such_font_file.ttf" in n]
        self.assertEqual(len(missing), 1, missing)
        self.assertIn("not found", missing[0])
        self.assertIn("last resort", missing[0])


# --------------------------------------------------------------------------- the EEVEE lens stand-in
LENS_STANDIN_PROGRAM = '''
import json
red = gl.material_pbr("red", (220, 40, 40), roughness=0.6)
blue = gl.material_pbr("blue", (40, 60, 220), roughness=0.6)
a = gl.box((-15.0, 0.0, -30.0), (30.0, 60.0, 2.0), "back_red", "frame", "back_red"); gl.assign(a, red)
b = gl.box((15.0, 0.0, -30.0), (30.0, 60.0, 2.0), "back_blue", "frame", "back_blue"); gl.assign(b, blue)
# run 2's lens: a light-brown gradient-free tint under a flash mirror
optics = gl.lens_optics(transmission_top_rgb=(0.59, 0.49, 0.38), mirror_rgb=(0.25, 0.20, 0.15))
mat = gl.material_lens("lens", optics)
lens = gl.lens_solid(gl.rounded_rect(40.0, 30.0, 6.0, n=96), z_front=0.0, name="lens_C", part="lens_C", base_curve=0.0)
gl.assign(lens, mat)
bsdf = mat.node_tree.nodes.get("Principled BSDF")
plain = gl.material_lens("plain", gl.lens_optics(transmission_top_rgb=(0.59, 0.49, 0.38)))
pb = plain.node_tree.nodes.get("Principled BSDF")
out = {k: (list(bsdf.inputs[k].default_value) if k in ("Base Color", "Specular Tint") else bsdf.inputs[k].default_value)
       for k in ("Base Color", "Metallic", "Transmission Weight", "Specular IOR Level", "Specular Tint")}
out["plain_level"] = pb.inputs["Specular IOR Level"].default_value
out["plain_tint"] = list(pb.inputs["Specular Tint"].default_value)
gl.note("lens " + json.dumps(out))
'''


def mean_px(img, x0, x1, y0, y1, ppm=6.0, size=400):
    """Mean RGB of the model-mm box [x0, x1] x [y0, y1] in a front orbit render centred on the origin."""
    c0, c1 = int(size / 2 + x0 * ppm), int(size / 2 + x1 * ppm)
    r0, r1 = int(size / 2 - y1 * ppm), int(size / 2 - y0 * ppm)
    return img[r0:r1, c0:c1].reshape(-1, 3).mean(0)


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class LensStandIn(unittest.TestCase):
    """material_lens rendered a mirrored lens with Metallic 0.6 and the reflectance as base colour, so EEVEE drew run 2's
    flash lens opaque and dark (see-through contrast 0.06 of the bare backdrop's): the author's sheets could not show the
    tint. The stand-in keeps Transmission 1 over the mean transmission and puts the flash in a tinted specular."""

    def test_a_mirrored_lens_renders_see_through_and_tinted(self):
        import numpy as np
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "p.py").write_text(LENS_STANDIN_PROGRAM, encoding="utf-8")
            view = {"id": "front", "width": 400, "height": 400, "kind": "textured", "transparent": False,
                    "background": [0.85, 0.85, 0.85],
                    "camera": {"type": "orbit", "yaw": 0, "pitch": 0, "px_per_mm": 6.0, "target": [0.0, 0.0, 0.0]}}
            r = run_harness({"modules": [{"name": "p", "path": str(root / "p.py")}], "export": False, "save_blend": False,
                             "renders": [view]}, root / "out", time_limit_s=240)
            self.assertTrue(r["ok"], failure_text(r))
            lens = json.loads(next(n for n in r["notes"] if n.startswith("lens "))[len("lens "):])
            self.assertEqual(lens["Metallic"], 0.0)
            self.assertEqual(lens["Transmission Weight"], 1.0)
            self.assertTrue(np.allclose(lens["Base Color"][:3], (0.59, 0.49, 0.38), atol=1e-6), lens)
            self.assertAlmostEqual(lens["Specular IOR Level"], 0.25 / 0.08, places=5)       # F0 0.25 = 0.08 x level
            self.assertTrue(np.allclose(lens["Specular Tint"][:3], (1.0, 0.8, 0.6), atol=1e-6), lens)
            self.assertAlmostEqual(lens["plain_level"], 0.5, places=6)                        # 4 % glass: the default
            self.assertTrue(np.allclose(lens["plain_tint"][:3], 1.0))
            img = np.asarray(Image.open(r["renders"][0]["path"]).convert("RGB"), float)
        over_red, over_blue = mean_px(img, -12, -4, -6, 6), mean_px(img, 4, 12, -6, 6)
        bare_red, bare_blue = mean_px(img, -25, -22, 18, 25), mean_px(img, 22, 25, 18, 25)
        contrast = np.abs(over_red - over_blue).sum() / np.abs(bare_red - bare_blue).sum()
        self.assertGreater(contrast, 0.4, (over_red, over_blue))          # the backdrop shows through (old: 0.06)
        # tinted, not clear: the lens darkens the red backdrop's red channel
        self.assertLess(over_red[0], bare_red[0] - 20.0, (over_red, bare_red))


if __name__ == "__main__":
    unittest.main()
