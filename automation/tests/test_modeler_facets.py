"""Faceting in the delivered AR model (test-pilot-002 r0006, owner 2026-09-28: "the shapes which construct the model show
when the face moves"). Three causes, one test class each:

* glasses_lib sections: ``tube_along_path`` arc-length resampled its rounded sections, leaving 1-2 points per corner, so
  the section turned 30-53 deg at single vertices and the exporter's 40 deg crease drew hard facet lines along every
  temple and core wire; ``rounded_rect`` put 4 points (22.5 deg steps) on a corner at small ``n``. Run in host Blender.
* the exporter's normals and audit on the delivered r0006 parts (re-exported; no Blender): the flat front plate keeps a
  flat normal field next to its bevel, and the audit names the coarse temple sweeps and the flat mirror lens.
* the r0006 program rebuilt on host Blender with this library: no hard facet lines, within the contract budgets.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np

from modeler.paths import blender_executable
from modeler.worker import run_harness

ROOT = Path(__file__).resolve().parents[1]
R0006 = ROOT / "data" / "modeler" / "agentic" / "test-pilot-002" / "revisions" / "r0006"
OP0013 = ROOT / "data" / "modeler" / "agentic" / "test-pilot-002" / "worker" / "op0013" / "bundle"
P1_R0002 = ROOT / "data" / "modeler" / "agentic" / "test-pilot-001" / "revisions" / "r0002"
P1_OP0009 = ROOT / "data" / "modeler" / "agentic" / "test-pilot-001" / "worker" / "op0009" / "bundle"    # r0002's build
VB_C0002 = ROOT / "data" / "modeler" / "jobs" / "vb-run1" / "candidates" / "c0002" / "build"            # R_WRAP = 300 mm

SECTION_PROGRAM = '''
import json


def turns(ring):
    """Turn per vertex (deg) of a closed 3D polyline, and its shortest edge (mm)."""
    e = np.roll(ring, -1, 0) - ring
    L = np.linalg.norm(e, axis=1)
    u = e / L[:, None]
    return np.degrees(np.arccos(np.clip((np.roll(u, 1, 0) * u).sum(1), -1, 1))), float(L.min())


out = {"rounded_rect": {}, "tubes": {}}
for n in (8, 12, 20, 24, 96):
    p = gl.rounded_rect(3.3, 5.8, 0.85, n=n)
    t, _ = turns(np.c_[p, np.zeros(len(p))])
    out["rounded_rect"][n] = [len(p), float(t.max())]
# section_rect keeps its point count at the documented n=24 (lofts mix it with section_ellipse(n=24))
out["section_rect_24"] = len(gl.section_rect((0, 0, 0), 6.0, 8.0, (1, 0, 0), (0, 1, 0), radius=1.5, n=24))
N = 60
path = gl.smooth_path([(60.0, 10.0, -2.0), (61.0, 10.0, -60.0), (62.0, 8.0, -100.0), (62.0, -6.0, -125.0), (61.0, -22.0, -150.0)], n=N)
d = np.linspace(0.0, 1.0, N)
widths = np.interp(d, [0, 0.6, 0.97, 1], [3.8, 3.0, 3.6, 0.6])
heights = np.interp(d, [0, 0.4, 0.97, 1], [6.1, 7.3, 7.3, 0.6])
radius = np.minimum(0.85, widths * 0.35) * (d < 0.9)          # the rounding goes to 0 near the tip
for name, part, kw in (("arm_n20", "temple_R", dict(radius=radius, n=20)), ("wire_n8", "temple_L", dict(radius=0.3, n=8)),
                       ("square_n24", "frame", dict(radius=0.0, n=24))):
    t = gl.tube_along_path(path, widths if part != "frame" else 2.0, heights if part != "frame" else 2.0, name, part, "arm", **kw)
    V = np.array([v.co[:] for v in t.data.vertices])
    K = len(V) // N
    rows = [turns(r) for r in V[:K * N].reshape(N, K, 3)]
    out["tubes"][name] = {"vertices": len(V), "K": K, "max_turn": max(float(r[0].max()) for r in rows),
                          "min_edge": min(r[1] for r in rows)}
gl.box((0.0, 5.0, -2.0), (20.0, 4.0, 3.0), "bridge", "frame", "bridge", bevel_mm=0.3)
lens_mat = gl.material_lens("lens", gl.lens_optics(transmission_top_rgb=(0.3, 0.3, 0.3)))
for side, cx in (("R", 32.0), ("L", -32.0)):
    l = gl.lens_solid(gl.rounded_rect(48.0, 36.0, 8.0, n=96, center=(cx, 0.0)), z_front=-1.5, name=f"lens_{side}", part=f"lens_{side}", base_curve=4.0)
    gl.assign(l, lens_mat)
gl.note("sections " + json.dumps(out))
'''


def section_report(r: dict) -> dict:
    for n in r.get("notes") or []:
        if n.startswith("sections "):
            return json.loads(n[len("sections "):])
    raise AssertionError(json.dumps({"notes": r.get("notes"), "modules": r.get("module_results"), "error": r.get("error")})[:3000])


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class SectionSampling(unittest.TestCase):
    """Rounded sections keep their corner arcs: no section vertex turns more than 15 deg, the ring size is constant along
    the tube (also where the rounding goes to 0), and the exported temples carry no hard facet line."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "p.py").write_text(SECTION_PROGRAM, encoding="utf-8")
        cls.r = run_harness({"modules": [{"name": "frame", "path": str(root / "p.py")}], "export": True, "save_blend": False,
                             "renders": []}, root / "out", time_limit_s=240)
        cls.out = root / "out"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rounded_rect_steps(self):
        rep = section_report(self.r)
        for n, (count, turn) in rep["rounded_rect"].items():
            self.assertEqual(count % 4, 0, n)
            self.assertLessEqual(turn, 15.0 + 1e-6, f"rounded_rect(n={n}) turns {turn:.1f} deg at a vertex")
        self.assertEqual(rep["rounded_rect"]["96"][0], 96)
        self.assertEqual(rep["section_rect_24"], 24)

    def test_tube_sections(self):
        rep = section_report(self.r)
        for name, t in rep["tubes"].items():
            self.assertEqual(t["vertices"], 60 * t["K"], (name, t))                # constant ring size, no cap vertices
            self.assertGreaterEqual(t["K"], 32, (name, t))                           # >= 8 points per 90 deg corner
            self.assertLessEqual(t["max_turn"], 15.0 + 1e-6, (name, t))
            self.assertGreater(t["min_edge"], 0.005, (name, t))                      # well above the export's 1e-3 mm weld

    def test_exported_temples_have_no_hard_facet_lines(self):
        self.assertTrue(self.r["ok"], json.dumps(self.r.get("module_results"), indent=1)[:3000])
        inv = {row["object"]: row for row in self.r["inventory"]}
        for name in ("arm_n20", "wire_n8", "square_n24"):
            self.assertTrue(inv[name]["closed"], inv[name])
        from modeler import export as mexport
        res = mexport.export_glb(self.out / "parts.npz", self.out / "materials.json", self.out / "m.glb")
        audit = res["receipt"]["audit"]
        for part in ("temple_R", "temple_L"):       # (a millimetre or two: the taper to the tip turns along the path)
            self.assertLess(audit["parts"][part]["hard_crease_mm"], 5.0, audit["parts"][part])
        self.assertNotIn("faceted_sweep", audit["flags"], audit["notes"])


@unittest.skipUnless((R0006 / "build" / "parts.npz").is_file(), "test-pilot-002 r0006 build not present")
class DeliveredR0006(unittest.TestCase):
    """The delivered r0006 parts re-exported with this exporter: the frame front's normal bleed is gone (normals only,
    same triangles), and the audit names what the geometry still carries (coarse temple sections, flat mirror lens)."""

    @classmethod
    def setUpClass(cls):
        from modeler import export as mexport
        cls.tmp = tempfile.TemporaryDirectory()
        cls.res = mexport.export_glb(R0006 / "build" / "parts.npz", R0006 / "build" / "materials.json",
                                     Path(cls.tmp.name) / "r0006.glb")
        cls.audit = cls.res["receipt"]["audit"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_contract_and_same_triangles(self):
        self.assertTrue(self.res["contract"]["ok"], self.res["contract"].get("failures"))
        delivered = json.loads((R0006 / "export.json").read_text(encoding="utf-8"))["receipt"]["triangles"]
        self.assertEqual(self.res["receipt"]["triangles"], delivered)

    def test_frame_front_bleed_is_gone(self):
        from bsa import export
        from modeler import export as mexport
        fr = self.audit["parts"]["frame"]
        self.assertGreater(fr["plane_area_mm2"], 300.0)
        self.assertLess(fr["normal_bleed_fraction"], 0.05, fr)
        self.assertFalse([n for n in self.audit["notes"] if n.startswith("frame") and "stair steps" in n], self.audit["notes"])
        # the same part with the old angle-weighted auto-smooth: most of the plane bled
        objs, mats, _ = mexport.load_parts(R0006 / "build" / "parts.npz", R0006 / "build" / "materials.json")
        parts, _, _ = mexport.assemble(objs, mats)
        V, F = np.asarray(parts["frame"]["V"], float), np.asarray(parts["frame"]["F"], np.int64)
        old = export.surface_audit(V, F, export.crease_normals(V, F, export.DEFAULT_CREASE_DEG))
        self.assertGreater(old["normal_bleed_fraction"], 0.3, old)

    def test_audit_names_the_coarse_temples_and_the_flat_mirror_lens(self):
        self.assertIn("faceted_sweep", self.audit["flags"])
        for side in ("R", "L"):
            self.assertGreater(self.audit["parts"][f"temple_{side}"]["hard_crease_mm"], 500.0)
            self.assertGreater(self.audit["parts"][f"temple_{side}"]["sweep_facet_mm"], 500.0)     # lines along the arm
            lens = self.audit["parts"][f"lens_{side}"]
            self.assertLess(lens["normal_span_h_deg"], 8.0)
            self.assertLess(lens["normal_span_v_deg"], 2.0)
            self.assertGreater(lens["reflectance"], 0.2)
        self.assertIn("planar_mirror_lens", self.audit["flags"])
        self.assertTrue(any(n.startswith("lens_R") and "base curve" in n for n in self.audit["notes"]), self.audit["notes"])
        self.assertTrue(any(n.startswith("temple_R has") and "tube_along_path" in n for n in self.audit["notes"]),
                        self.audit["notes"])

    def test_audit_names_the_near_flat_front(self):
        # the program wraps the front at 700 mm (gl.wrap_cylinder(front, 700)); the fit reads it through the endpieces
        fr = self.audit["parts"]["frame"]
        self.assertIn("planar_front", self.audit["flags"])
        self.assertTrue(500.0 < fr["front_wrap_radius_mm"] < 900.0, fr)
        self.assertGreater(fr["front_width_mm"], 120.0)
        note = next(n for n in self.audit["notes"] if n.startswith("frame") and "nearly flat" in n)
        self.assertIn("gl.wrap_cylinder", note)

    def test_the_bounded_sweep_search_matches_the_ball_query_on_the_temples(self):
        from bsa import export
        from modeler import export as mexport
        from test_bsa_export import hard_edges, reference_sweep_facet_lines
        objs, mats, _ = mexport.load_parts(R0006 / "build" / "parts.npz", R0006 / "build" / "materials.json")
        parts, _, _ = mexport.assemble(objs, mats)
        for name in ("temple_R", "frame"):
            V, F = np.asarray(parts[name]["V"], float), np.asarray(parts[name]["F"], np.int64)
            a, b, pa, pb = hard_edges(V, F)
            self.assertEqual(export.sweep_facet_lines(a, b, pa, pb), reference_sweep_facet_lines(a, b, pa, pb), name)


@unittest.skipUnless((VB_C0002 / "parts.npz").is_file(), "vb-run1 c0002 build not present")
class WrappedFrontIsNotPlanar(unittest.TestCase):
    """A front the program wraps at 300 mm (vb-run1 c0002, 'nearly flat front, as the photos show') fits at about
    254 mm and is not flagged planar_front: the threshold sits between the 300 mm and 700 mm programs."""

    def test_vb_front(self):
        from modeler import export as mexport
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for f in ("parts.npz", "materials.json"):
                shutil.copy2(VB_C0002 / f, root / f)
            audit = mexport.export_glb(root / "parts.npz", root / "materials.json", root / "vb.glb")["receipt"]["audit"]
        fr = audit["parts"]["frame"]
        self.assertTrue(200.0 < fr["front_wrap_radius_mm"] < 350.0, fr)
        self.assertNotIn("planar_front", audit["flags"])


@unittest.skipIf(blender_executable() is None or not (OP0013 / "operation.json").is_file(),
                 "Blender or the test-pilot-002 op0013 bundle not present")
class RebuiltR0006(unittest.TestCase):
    """r0006's own sealed program (worker op0013) rebuilt on host Blender with this library and exported: the temples lose
    their facet lines (delivered: about 690 mm of crystal + 616 mm of gold-core hard edges per temple) within the budget."""

    def test_rebuild(self):
        from bsa import contract
        from modeler import export as mexport
        op = json.loads((OP0013 / "operation.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "build"
            r = run_harness({"evidence_path": str(OP0013 / op["evidence"]), "mode": "build", "renders": [], "export": True,
                             "save_blend": False,
                             "modules": [{"name": m["name"], "path": str(OP0013 / m["file"])} for m in op["modules"]]},
                            out, time_limit_s=600)
            self.assertTrue(r["ok"], json.dumps(r.get("module_results"), indent=1)[:3000])
            res = mexport.export_glb(out / "parts.npz", out / "materials.json", out / "m.glb")
            audit = res["receipt"]["audit"]
            for side in ("R", "L"):
                self.assertLessEqual(audit["parts"][f"temple_{side}"]["hard_crease_mm"], 20.0, audit["parts"][f"temple_{side}"])
                self.assertEqual(audit["parts"][f"temple_{side}"]["sweep_facet_mm"], 0.0, audit["parts"][f"temple_{side}"])
            self.assertNotIn("faceted_sweep", audit["flags"], audit["notes"])
            self.assertLess(audit["parts"]["frame"]["normal_bleed_fraction"], 0.05)
            self.assertIn("planar_mirror_lens", audit["flags"])               # the program's own base_curve=0 stays
            self.assertTrue(res["contract"]["ok"], res["contract"].get("failures"))
            self.assertLessEqual(res["receipt"]["triangles"], contract.MAX_TRIANGLES)
            self.assertLessEqual(res["bytes"], contract.MAX_BYTES)


def sweep_notes(audit: dict) -> list[str]:
    return [n for n in audit["notes"] if "facet lines" in n]


@unittest.skipUnless((P1_R0002 / "build" / "parts.npz").is_file(), "test-pilot-001 r0002 build not present")
class Pilot001R0002Frame(unittest.TestCase):
    """test-pilot-001 r0002 (a copy of its build): the frame's 62 mm of short hard edges (a bevelled plate, stretched
    nose-bearing spheres) are no sweep, so no faceted_sweep note tells the author to rebuild the frame with
    gl.tube_along_path; its coarse old-library temples still are (about 1,290 mm of lines along each arm)."""

    @classmethod
    def setUpClass(cls):
        from modeler import export as mexport
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for f in ("parts.npz", "materials.json"):
            shutil.copy2(P1_R0002 / "build" / f, root / f)
        cls.res = mexport.export_glb(root / "parts.npz", root / "materials.json", root / "r0002.glb")
        cls.audit = cls.res["receipt"]["audit"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_frame_is_not_a_faceted_sweep(self):
        fr = self.audit["parts"]["frame"]
        self.assertGreater(fr["hard_crease_mm"], 50.0, fr)            # the short hard edges are still measured ...
        self.assertEqual(fr["sweep_facet_mm"], 0.0, fr)                # ... and are no facet line along a sweep
        self.assertFalse([n for n in sweep_notes(self.audit) if n.startswith("frame")], self.audit["notes"])

    def test_the_old_library_temples_still_are(self):
        self.assertIn("faceted_sweep", self.audit["flags"])
        for side in ("R", "L"):
            self.assertGreater(self.audit["parts"][f"temple_{side}"]["sweep_facet_mm"], 1000.0)
        self.assertEqual(sorted(n.split(" ")[0] for n in sweep_notes(self.audit)), ["temple_L", "temple_R"])


@unittest.skipIf(blender_executable() is None or not (P1_OP0009 / "operation.json").is_file(),
                 "Blender or the test-pilot-001 op0009 bundle not present")
class RebuiltPilot001R0002(unittest.TestCase):
    """r0002's sealed program (worker op0009, copied) rebuilt on host Blender with the current library: the verifier's
    rebuild still carried faceted_sweep on the frame (62 mm) with advice to use gl.tube_along_path; now nothing is flagged
    as a faceted sweep."""

    def test_rebuild_has_no_faceted_sweep(self):
        from modeler import export as mexport
        op = json.loads((P1_OP0009 / "operation.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            for rel in [op["evidence"]] + [m["file"] for m in op["modules"]]:
                (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(P1_OP0009 / rel, bundle / rel)
            out = Path(tmp) / "build"
            r = run_harness({"evidence_path": str(bundle / op["evidence"]), "mode": "build", "renders": [], "export": True,
                             "save_blend": False,
                             "modules": [{"name": m["name"], "path": str(bundle / m["file"])} for m in op["modules"]]},
                            out, time_limit_s=600)
            self.assertTrue(r["ok"], json.dumps(r.get("module_results"), indent=1)[:3000])
            audit = mexport.export_glb(out / "parts.npz", out / "materials.json", out / "m.glb")["receipt"]["audit"]
            self.assertGreater(audit["parts"]["frame"]["hard_crease_mm"], 50.0, audit["parts"]["frame"])
            for part in ("frame", "temple_R", "temple_L"):
                self.assertEqual(audit["parts"][part]["sweep_facet_mm"], 0.0, audit["parts"][part])
            self.assertNotIn("faceted_sweep", audit["flags"], audit["notes"])
            self.assertEqual(sweep_notes(audit), [])


if __name__ == "__main__":
    unittest.main()
