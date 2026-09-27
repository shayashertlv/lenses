"""Path-based helpers (tube_along_path, rim_ring, smooth_path) build closed, outward-facing solids in Blender."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from modeler.paths import blender_executable
from modeler.worker import run_harness

PROGRAM = '''
# a temple along -Z that drops behind the ear (path nearly parallel to Z: exercises the up-vector fallback)
ctrl = [(60.0, 10.0, -2.0), (61.0, 10.0, -60.0), (62.0, 8.0, -100.0), (62.0, -6.0, -125.0), (61.0, -22.0, -150.0)]
path = gl.smooth_path(ctrl, n=36)
t = gl.tube_along_path(path, widths=np.linspace(5.0, 3.0, 36), heights=np.linspace(14.0, 5.0, 36), name="temple_R", part="temple_R", component="arm", radius=1.0)
gl.mirror_x(t, "temple_L", "temple_L", "arm")
outline = gl.rounded_rect(52.0, 44.0, 10.0, n=120, center=(32.0, 0.0))
rim = gl.rim_ring(outline, width=5.0, thickness=6.0, z_front=0.0, name="rim_R", part="frame", component="rim_R", profile="rounded")
rim2 = gl.rim_ring(gl.rounded_rect(52.0, 44.0, 10.0, n=120, center=(-32.0, 0.0)), width=5.0, thickness=6.0, z_front=0.0, name="rim_L", part="frame", component="rim_L", profile="round")
lens_mat = gl.material_lens("lens", gl.lens_optics(transmission_top_rgb=(0.3, 0.3, 0.3), mirror_angular=[(0, (0.5, 0.2, 0.4)), (45, (0.2, 0.5, 0.3))]))
for side, cx in (("R", 32.0), ("L", -32.0)):
    l = gl.lens_solid(gl.rounded_rect(52.0, 44.0, 10.0, n=100, center=(cx, 0.0)), z_front=-1.5, name=f"lens_{side}", part=f"lens_{side}", base_curve=4.0)
    gl.assign(l, lens_mat)
'''


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class PathHelpers(unittest.TestCase):
    def test_tubes_and_rims_are_closed_and_outward(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "p.py").write_text(PROGRAM, encoding="utf-8")
            r = run_harness({"modules": [{"name": "frame", "path": str(root / "p.py")}], "export": True, "save_blend": False, "renders": []},
                            root / "out", time_limit_s=180)
            self.assertTrue(r["ok"], json.dumps(r.get("module_results"), indent=1)[:3000])
            inv = {row["object"]: row for row in r["inventory"]}
            for name in ("temple_R", "temple_L", "rim_R", "rim_L"):
                self.assertTrue(inv[name]["closed"], (name, inv[name]))
                self.assertGreater(inv[name]["signed_volume_mm3"], 0.0, (name, inv[name]["signed_volume_mm3"]))
            self.assertIn("lib_sha256", r)
            # the temple reaches the clip depth
            self.assertLess(inv["temple_R"]["bbox_mm"][0][2], -145.0)
            # the mirror coat's angular reflectance table is recorded (auto-extended to 90 degrees) and exported
            mats = json.loads((root / "out" / "materials.json").read_text(encoding="utf-8"))["materials"]
            optics = mats["lens"]["lens"]
            self.assertEqual([row[0] for row in optics["angular"]], [0.0, 45.0, 90.0])
            self.assertTrue(optics["mirror"])
            from modeler import export as mexport
            spec = mexport.material_spec("lens", mats["lens"], lens=True)
            self.assertEqual(len(spec["lens_appearance"]["angular_reflectance_keyframes"]), 3)


REPAIR_PROGRAM = '''
# a closed box with a patch of flipped faces (what a mirrored/boolean solid sometimes carries), a lens sheet,
# a script-font mark printed on the lens, and an open sheet whose majority orientation must survive
import bmesh as _bm
box = gl.box((0.0, 0.0, -5.0), (20.0, 10.0, 8.0), "block", "frame", "block")
bm = _bm.new(); bm.from_mesh(box.data); bm.faces.ensure_lookup_table()
_bm.ops.reverse_faces(bm, faces=[f for f in bm.faces if f.normal.z > 0.5])   # flip the top faces only
bm.to_mesh(box.data); bm.free(); box.data.update()
lens_mat = gl.material_lens("lens", gl.lens_optics(transmission_top_rgb=(0.2, 0.2, 0.2)))
l = gl.lens_solid(gl.rounded_rect(48.0, 36.0, 8.0, n=96, center=(30.0, 0.0)), z_front=-1.0, name="lens_R", part="lens_R", base_curve=4.0)
gl.assign(l, lens_mat)
mark = gl.lens_print(l, "Ray-Ban", 4.0, (18.0, 8.0), "lens_logo_R", font="script")
gl.assign(mark, gl.material_pbr("print", (235, 235, 230), roughness=0.5))
t = gl.tube_along_path(gl.smooth_path([(60.0, 10.0, -2.0), (61.0, 8.0, -80.0), (61.0, -20.0, -150.0)], n=24), widths=np.linspace(5.0, 3.0, 24), heights=np.linspace(12.0, 5.0, 24), name="temple_R", part="temple_R", component="arm", radius=1.0)
gl.mirror_x(t, "temple_L", "temple_L", "arm")
'''


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class ExportRepairs(unittest.TestCase):
    def test_inconsistent_normals_are_repaired_and_noted_and_script_font_prints(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "p.py").write_text(REPAIR_PROGRAM, encoding="utf-8")
            r = run_harness({"modules": [{"name": "frame", "path": str(root / "p.py")}], "export": True, "save_blend": False, "renders": []},
                            root / "out", time_limit_s=180)
            self.assertTrue(r["ok"], json.dumps(r.get("module_results"), indent=1)[:3000])
            inv = {row["object"]: row for row in r["inventory"]}
            self.assertEqual(inv["block"]["misoriented_edges"], 0, inv["block"])
            self.assertTrue(inv["block"]["closed"], inv["block"])
            self.assertGreater(inv["block"]["signed_volume_mm3"], 0.0)
            notes = " ".join(r.get("notes") or [])
            self.assertIn("normals made consistent", notes)
            # the printed mark is a small opaque frame part in front of the lens, built with a script font
            self.assertEqual(inv["lens_logo_R"]["part"], "frame")
            self.assertLess(inv["lens_logo_R"]["bbox_mm"][1][1] - inv["lens_logo_R"]["bbox_mm"][0][1], 8.0)
            self.assertGreater(inv["lens_logo_R"]["bbox_mm"][0][2], inv["lens_R"]["bbox_mm"][1][2] - 1.0)
            self.assertNotIn("not found", notes)


if __name__ == "__main__":
    unittest.main()
