"""End-to-end smoke of the modeler runtime: a generic (non-product) program -> Blender -> parts -> GLB -> contract.

Skips when Blender is unavailable. The fixture program is deliberately generic (a rounded rectangular acetate
frame, two lenses, two straight temples) - it is NOT a product candidate and never enters a job.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from modeler import export as mexport
from modeler.paths import blender_executable
from modeler.worker import run_harness

GENERIC_FRAME = '''
# generic test frame (mm). Front plate with two lens holes, bevelled; lenses; straight temples.
W, Hh = 140.0, 50.0
outer = gl.rounded_rect(W, Hh, 10.0, n=160)
lensR = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(32.0, -2.0))
lensL = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(-32.0, -2.0))
front = gl.plate_with_holes(outer, [lensR, lensL], z_front=0.0, thickness=6.0, name="front_plate", part="frame", component="front")
gl.bevel(front, 1.5, segments=3)
acetate = gl.material_acetate("acetate_navy", (28, 40, 70))
gl.assign(front, acetate)
gold = gl.material_metal("gold", (212, 175, 90))
plate = gl.box((60.0, -5.0, -3.0), (10.0, 2.0, 8.0), "logo_R", "frame", "logo_R")
gl.assign(plate, gold)
gl.mirror_x(plate, "logo_L", "frame", "logo_L")
optics = gl.lens_optics(transmission_top_rgb=(0.25, 0.20, 0.14), transmission_bottom_rgb=(0.75, 0.70, 0.62))
lens_mat = gl.material_lens("lens", optics)
for side, outline in (("R", lensR), ("L", lensL)):
    lens = gl.lens_solid(gl.offset_closed(outline, 0.8), z_front=-1.5, name=f"lens_{side}", part=f"lens_{side}", base_curve=4.0, thickness=2.0)
    gl.assign(lens, lens_mat)
for side, sx in (("R", 1.0), ("L", -1.0)):
    secs = []
    for k, z in enumerate(np.linspace(-3.0, -140.0, 12)):
        w = 6.0 if k < 8 else 6.0 - (k - 8) * 0.8
        h = 8.0 if k < 6 else 8.0 - (k - 6) * 0.6
        y = 0.0 if k < 8 else -(k - 8) * 3.0
        secs.append(gl.section_rect((sx * (W / 2 - 3.0), y, z), w, h, (1, 0, 0), (0, 1, 0), radius=1.5, n=24))
    t = gl.loft(secs, f"temple_{side}", f"temple_{side}", "arm")
    gl.assign(t, acetate)
gl.set_bridge_underside((0.0, -7.0, -3.0))
gl.note("generic fixture built")
'''


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class WorkerEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        prog = root / "frame.py"
        prog.write_text(GENERIC_FRAME, encoding="utf-8")
        job = {"modules": [{"name": "frame", "path": str(prog)}], "evidence_path": None, "export": True, "save_blend": True,
               "renders": [
                   {"id": "front_clay", "kind": "clay", "width": 480, "height": 240,
                    "camera": {"type": "orbit", "yaw": 0, "pitch": 0, "roll": 0, "ortho": True, "px_per_mm": 3.0, "target": "bbox"}},
                   {"id": "angled_tex", "kind": "textured", "width": 480, "height": 320,
                    "camera": {"type": "orbit", "yaw": 35, "pitch": 15, "roll": 0, "ortho": False, "px_per_mm": 2.5,
                               "distance_mm": 700, "target": "bbox"}}],
               "samples": 8}
        cls.result = run_harness(job, root / "out", time_limit_s=240)
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_program_built(self):
        r = self.result
        self.assertTrue(r["ok"], json.dumps(r, indent=1)[:4000])
        self.assertEqual([m["ok"] for m in r["module_results"]], [True])
        self.assertIn("generic fixture built", r["notes"])
        parts = {row["part"] for row in r["inventory"]}
        self.assertEqual(parts, {"frame", "lens_R", "lens_L", "temple_R", "temple_L"})
        closed = {row["object"]: row["closed"] for row in r["inventory"]}
        self.assertTrue(closed["lens_R"] and closed["temple_R"] and closed["logo_R"], closed)

    def test_renders_written(self):
        for row in self.result["renders"]:
            self.assertIsNone(row["error"], row)
            self.assertTrue(Path(row["path"]).is_file())
            from PIL import Image
            im = Image.open(row["path"])
            self.assertEqual(im.size, (row["width"], row["height"]))
            a = np.asarray(im.convert("RGBA"))
            self.assertGreater(int((a[..., 3] > 0).sum()), 500, "render is empty")

    def test_export_and_contract(self):
        r = self.result
        out = self.root / "model.glb"
        rec = mexport.export_glb(Path(r["parts_npz"]), Path(r["materials_json"]), out)
        self.assertTrue(out.is_file())
        self.assertEqual(set(rec["parts"]), {"frame", "lens_R", "lens_L", "temple_R", "temple_L"})
        c = rec["contract"]
        self.assertTrue(c["ok"], json.dumps({k: v for k, v in c.get("checks", {}).items() if not v.get("pass")}, indent=1, default=str))


class ExportUnits(unittest.TestCase):
    def test_transmission_profile_orientation(self):
        prof = mexport.transmission_profile({"transmission_top_rgb": [0.2, 0.2, 0.2], "transmission_bottom_rgb": [0.8, 0.8, 0.8],
                                             "profile": "linear"})
        self.assertEqual(prof.shape, (16, 3))
        self.assertAlmostEqual(prof[0, 0], 0.2)
        self.assertAlmostEqual(prof[-1, 0], 0.8)

    def test_lens_material_gets_descriptor(self):
        spec = mexport.material_spec("lens", {"base_color_linear": [0.5, 0.5, 0.5], "lens": {
            "transmission_top_rgb": [0.3, 0.25, 0.2], "transmission_bottom_rgb": [0.7, 0.65, 0.6], "profile": "smooth",
            "reflectance_rgb": [0.04, 0.04, 0.04], "mirror": False, "roughness": 0.05}}, lens=True)
        d = spec["lens_appearance"]
        keys = d["optical_density_keyframes"]
        self.assertEqual(keys[0]["v"], 0.0)
        self.assertEqual(keys[-1]["v"], 1.0)
        # bottom (v = 0) is lighter than the top (v = 1): lower density at v = 0
        self.assertLess(keys[0]["optical_density_rgb"][0], keys[-1]["optical_density_rgb"][0])
        self.assertEqual(spec["transmission"], 1.0)


class DegenerateTriangles(unittest.TestCase):
    def test_zero_area_faces_are_dropped_with_a_note(self):
        V = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [5, 5, 3]], float)
        F = np.array([[0, 1, 2], [0, 1, 1], [1, 2, 3], [0, 0, 0]])
        M = np.zeros(len(F), int)
        F2, M2, UV2, dropped = mexport.drop_degenerate(V, F, M, None)
        self.assertEqual(dropped, 2)
        self.assertEqual(len(F2), 2)
        objects = {"o": {"object": "o", "part": "frame", "component": "c", "V": V, "F": F, "M": M, "UV": None, "materials": [None]}}
        parts, mats, notes = mexport.assemble(objects, {})
        self.assertEqual(len(parts["frame"]["F"]), 2)
        self.assertTrue(any("degenerate" in n for n in notes))

    def test_a_closed_mesh_is_kept_whole(self):
        # a closed tetrahedron (four non-degenerate faces): assemble keeps every face and reports nothing degenerate
        V = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 10]], float)
        F = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]])
        self.assertTrue(mexport.is_closed(V, F))
        objects = {"o": {"object": "o", "part": "frame", "component": "c", "V": V, "F": F, "M": np.zeros(4, int), "UV": None, "materials": [None]}}
        parts, mats, notes = mexport.assemble(objects, {})
        self.assertEqual(len(parts["frame"]["F"]), 4)
        self.assertFalse(any("degenerate" in n for n in notes), notes)


class SharedLensMaterial(unittest.TestCase):
    def _tri(self, part, mats):
        V = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0]], float)
        return {"object": part, "part": part, "component": part, "V": V, "F": np.array([[0, 1, 2]]), "M": np.zeros(1, int), "UV": None, "materials": mats}

    def test_frame_material_on_a_lens_is_exported_with_optics(self):
        # the author assigned the frame's opaque material to the shield lens (Oakley/Invu turn 0, 2026-09-26)
        materials = {"black": {"kind": "acetate", "base_color_linear": [0.02, 0.02, 0.02], "roughness": 0.3}}
        objects = {"frame": self._tri("frame", ["black"]), "lens_C": self._tri("lens_C", ["black"])}
        parts, mats, notes = mexport.assemble(objects, materials)
        self.assertEqual(mats[parts["frame"]["material"][0]]["transmission"], 0.0)
        self.assertEqual(mats[parts["lens_C"]["material"]]["transmission"], 1.0)
        self.assertTrue(any("clear lens" in n for n in notes), notes)

    def test_lens_material_is_preferred_over_an_opaque_slot(self):
        materials = {"black": {"kind": "acetate", "base_color_linear": [0.02, 0.02, 0.02], "roughness": 0.3},
                     "tint": {"kind": "lens", "base_color_linear": [0.3, 0.3, 0.3], "roughness": 0.05,
                              "lens": {"transmission_top_rgb": [0.2, 0.2, 0.2], "transmission_bottom_rgb": [0.5, 0.5, 0.5],
                                       "profile": "smooth", "reflectance_rgb": [0.04, 0.04, 0.04], "mirror": False, "roughness": 0.05}}}
        objects = {"lens_R": self._tri("lens_R", ["black", "tint"])}
        parts, mats, notes = mexport.assemble(objects, materials)
        self.assertEqual(parts["lens_R"]["material"], "tint")
        self.assertIn("lens_appearance", mats["tint"])


if __name__ == "__main__":
    unittest.main()
