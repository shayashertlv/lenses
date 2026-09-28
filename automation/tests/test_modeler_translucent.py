"""Translucent (crystal) frames: gl.material_translucent -> KHR transmission/ior/volume on the frame AND the temples (single-sided,
flagged), the contract's runtime-role rule for both, the see-through measurement of the temples (the near arm only, in
front of the runtime's temple clip, bare-fixture pixels dropped), and the tags/coverage bookkeeping of the calibration set."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from bsa import contract, export as bsa_export
from modeler import export as mexport
from modeler.paths import blender_executable
from modeler.worker import run_harness

CANONICAL_LENS = {"kind": "lens", "base_color_linear": [0.4, 0.4, 0.4], "metallic": 0.0, "roughness": 0.05, "transmission": 1.0, "ior": 1.5,
                  "alpha": 1.0, "coat": 0.0, "texture_path": None,
                  "lens": {"transmission_top_rgb": [0.3, 0.3, 0.3], "transmission_bottom_rgb": [0.6, 0.6, 0.6], "profile": "smooth",
                           "reflectance_rgb": [0.04, 0.04, 0.04], "mirror": False, "angular": None, "roughness": 0.05}}
TRANSLUCENT = {"kind": "translucent", "base_color_linear": [0.95, 0.90, 0.80], "metallic": 0.0, "roughness": 0.08, "transmission": 1.0,
               "ior": 1.49, "alpha": 1.0, "coat": 0.0, "texture_path": None,
               "translucent": {"thickness_mm": 4.0, "attenuation_rgb_linear": [0.95, 0.90, 0.80], "attenuation_distance_mm": 4.0,
                               "ior": 1.49, "transmission": 1.0}}


def glb_doc(path: Path) -> dict:
    return contract.read_glb(path)["doc"]


def patch_glb(src: Path, dst: Path, edit_doc) -> Path:
    """Rewrite a GLB's JSON chunk (the same helper as tests/test_bsa_contract.py, inlined: the test modules are top-level)."""
    import struct
    raw = src.read_bytes()
    n = struct.unpack_from("<I", raw, 12)[0]
    doc = json.loads(raw[20:20 + n])
    binary = bytearray(raw[20 + n + 8:])
    edit_doc(doc)
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    body = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(binary), b"BIN\0") + bytes(binary)
    dst.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    return dst


class TranslucentSpec(unittest.TestCase):
    def test_material_spec_carries_volume_transmission_and_ior_single_sided(self):
        spec = mexport.material_spec("crystal", TRANSLUCENT, lens=False)
        self.assertEqual(spec["transmission"], 1.0)
        self.assertEqual(spec["ior"], 1.49)
        self.assertFalse(spec["double_sided"])
        self.assertEqual(spec["base_color"], [1.0, 1.0, 1.0, 1.0], "the tint lives in the volume only (never applied twice)")
        vol = spec["gltf"]["extensions"]["KHR_materials_volume"]
        self.assertAlmostEqual(vol["thicknessFactor"], 0.004)
        self.assertAlmostEqual(vol["attenuationDistance"], 0.004)
        self.assertEqual(vol["attenuationColor"], [0.95, 0.90, 0.80])
        opaque = mexport.material_spec("acetate", {"kind": "acetate", "base_color_linear": [0.1, 0.1, 0.1], "roughness": 0.2}, lens=False)
        self.assertEqual(opaque["transmission"], 0.0)
        self.assertNotIn("gltf", opaque)

    def _parts(self):
        parts, materials = bsa_export.synthetic_parts()
        materials["lens"] = mexport.material_spec("lens", CANONICAL_LENS, lens=True)
        return parts, materials

    def test_translucent_front_exports_and_passes_the_contract(self):
        parts, materials = self._parts()
        materials["frame"] = mexport.material_spec("crystal", TRANSLUCENT, lens=False)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "crystal.glb"
            receipt = bsa_export.write_glb(parts, materials, out)
            self.assertIn("frame_material_translucent", receipt["flags"])
            doc = glb_doc(out)
            frame_mat = next(m for m in doc["materials"] if m["name"] == "frame")   # glTF name = the material key
            ext = frame_mat["extensions"]
            self.assertEqual(ext["KHR_materials_transmission"]["transmissionFactor"], 1.0)
            self.assertEqual(ext["KHR_materials_ior"]["ior"], 1.49)
            self.assertAlmostEqual(ext["KHR_materials_volume"]["thicknessFactor"], 0.004)
            self.assertFalse(frame_mat.get("doubleSided", False))
            self.assertIn("KHR_materials_volume", doc["extensionsUsed"])
            roles = {n["name"]: n.get("extras", {}).get("partRole") for n in doc["nodes"]}
            self.assertEqual(roles["frame"], "frame")
            c = contract.check(out)
            self.assertTrue(c["ok"], c["failures"])
            self.assertTrue(c["checks"]["frame_temple_materials"]["pass"])
            self.assertTrue(c["checks"]["lens_materials_private"]["pass"])
            # the runtime classifies the front as frame: two optical meshes, not three
            self.assertEqual(c["checks"]["lens_detection"]["value"]["runtime_lens_meshes"], 2)

    def test_translucent_temple_exports_and_passes_the_contract(self):
        # a crystal product is crystal on its temples too (2026-09-28): the exporter writes the temple material with its
        # transmission, forced single-sided and flagged, and the contract classifies it as a temple through its role
        parts, materials = self._parts()
        materials["frame"] = mexport.material_spec("crystal", TRANSLUCENT, lens=False)
        materials["temple"] = mexport.material_spec("crystal_temple", TRANSLUCENT, lens=False)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "crystal_temples.glb"
            receipt = bsa_export.write_glb(parts, materials, out)
            self.assertIn("temple_R_material_translucent", receipt["flags"])
            self.assertIn("temple_L_material_translucent", receipt["flags"])
            doc = glb_doc(out)
            temple_mat = next(m for m in doc["materials"] if m["name"] == "temple")
            self.assertEqual(temple_mat["extensions"]["KHR_materials_transmission"]["transmissionFactor"], 1.0)
            self.assertAlmostEqual(temple_mat["extensions"]["KHR_materials_volume"]["thicknessFactor"], 0.004)
            self.assertFalse(temple_mat.get("doubleSided", False))
            roles = {n["name"]: n.get("extras", {}).get("partRole") for n in doc["nodes"]}
            self.assertEqual((roles["temple_R"], roles["temple_L"]), ("temple", "temple"))
            c = contract.check(out)
            self.assertTrue(c["ok"], c["failures"])
            self.assertTrue(c["checks"]["frame_temple_materials"]["pass"])
            self.assertTrue(c["checks"]["lens_materials_private"]["pass"])
            self.assertEqual(c["checks"]["lens_detection"]["value"]["runtime_lens_meshes"], 2)

    def test_contract_refuses_a_translucent_temple_without_canonical_lenses(self):
        parts, materials = bsa_export.synthetic_parts()
        materials["temple"] = mexport.material_spec("crystal_temple", TRANSLUCENT, lens=False)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "legacy_temple.glb"
            bsa_export.write_glb(parts, materials, out)
            c = contract.check(out)
            self.assertFalse(c["checks"]["frame_temple_materials"]["pass"])
            self.assertFalse(c["checks"]["frame_temple_materials"]["value"]["temple_R"])

    def test_contract_refuses_a_double_sided_translucent_temple(self):
        parts, materials = self._parts()
        materials["temple"] = mexport.material_spec("crystal_temple", TRANSLUCENT, lens=False)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "temples.glb"
            bsa_export.write_glb(parts, materials, out)
            self.assertTrue(contract.check(out)["checks"]["frame_temple_materials"]["pass"])

            def two_sided(doc):
                for m in doc["materials"]:
                    if m["name"] == "temple":
                        m["doubleSided"] = True
            bad = patch_glb(out, Path(tmp) / "temples_bad.glb", two_sided)
            c = contract.check(bad)
            self.assertFalse(c["checks"]["frame_temple_materials"]["pass"])
            self.assertIn("frame_temple_materials", c["failures"])

    def test_contract_refuses_a_translucent_front_without_canonical_lenses(self):
        # a legacy (transmission-only) lens gives the runtime no descriptor: the classification does not apply and the
        # front would become a third lens, so the contract refuses it
        parts, materials = bsa_export.synthetic_parts()
        materials["frame"] = mexport.material_spec("crystal", TRANSLUCENT, lens=False)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "legacy.glb"
            bsa_export.write_glb(parts, materials, out)
            c = contract.check(out)
            self.assertFalse(c["checks"]["frame_temple_materials"]["pass"])
            self.assertIn("frame_temple_materials", c["failures"])

    def test_contract_refuses_a_lens_material_shared_with_the_frame(self):
        parts, materials = self._parts()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "shared.glb"
            bsa_export.write_glb(parts, materials, out)

            def share(doc):
                lens_index = next(i for i, m in enumerate(doc["materials"]) if m["name"] == "lens")
                frame_mesh = doc["meshes"][next(n["mesh"] for n in doc["nodes"] if n["name"] == "frame")]
                for p in frame_mesh["primitives"]:
                    p["material"] = lens_index
            bad = patch_glb(out, Path(tmp) / "shared_bad.glb", share)
            c = contract.check(bad)
            self.assertFalse(c["checks"]["lens_materials_private"]["pass"])

    def test_contract_reads_the_roles_the_runtime_reads(self):
        # the runtime classifies by partRole EXTRAS, not by node names: a translucent front whose extras were stripped
        # would render as a third lens, so the contract must refuse it and name the missing roles
        parts, materials = self._parts()
        materials["frame"] = mexport.material_spec("crystal", TRANSLUCENT, lens=False)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "roles.glb"
            bsa_export.write_glb(parts, materials, out)
            good = contract.check(out)
            self.assertTrue(good["checks"]["part_roles"]["pass"], good["checks"]["part_roles"])

            def strip(doc):
                for n in doc["nodes"]:
                    n.pop("extras", None)
                for m in doc["meshes"]:
                    m.pop("extras", None)
            bad = patch_glb(out, Path(tmp) / "roles_bad.glb", strip)
            c = contract.check(bad)
            self.assertFalse(c["checks"]["part_roles"]["pass"])
            self.assertFalse(c["checks"]["frame_temple_materials"]["pass"])
            self.assertEqual(c["checks"]["lens_detection"]["value"]["runtime_lens_meshes"], 3, "without roles the runtime sees three lenses")


PROGRAM = '''
# a crystal front (translucent), opaque temples, canonical lenses
outer = gl.rounded_rect(140.0, 50.0, 10.0, n=160)
lensR = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(32.0, -2.0))
lensL = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(-32.0, -2.0))
front = gl.plate_with_holes(outer, [lensR, lensL], z_front=0.0, thickness=6.0, name="front_plate", part="frame", component="front")
crystal = gl.material_translucent("crystal", (250, 246, 236), thickness_mm=5.0)
gl.assign(front, crystal)
acetate = gl.material_acetate("acetate_dark", (40, 30, 24))
optics = gl.lens_optics(transmission_top_rgb=(0.35, 0.30, 0.24), transmission_bottom_rgb=(0.75, 0.70, 0.62))
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
        secs.append(gl.section_rect((sx * 67.0, y, z), w, h, (1, 0, 0), (0, 1, 0), radius=1.5, n=24))
    t = gl.loft(secs, f"temple_{side}", f"temple_{side}", "arm")
    gl.assign(t, TEMPLE_MATERIAL)
gl.set_bridge_underside((0.0, -7.0, -3.0))
'''


@unittest.skipIf(blender_executable() is None, "Blender not installed")
class TranslucentProgram(unittest.TestCase):
    def _build(self, program: str, root: Path) -> dict:
        (root / "p.py").write_text(program, encoding="utf-8")
        r = run_harness({"modules": [{"name": "frame", "path": str(root / "p.py")}], "export": True, "save_blend": False, "renders": []},
                        root / "out", time_limit_s=240)
        self.assertTrue(r["ok"], json.dumps(r.get("module_results"), indent=1)[:3000])
        return r

    def test_crystal_front_with_opaque_temples_exports_and_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = self._build(PROGRAM.replace("TEMPLE_MATERIAL", "acetate"), root)
            mats = json.loads(Path(r["materials_json"]).read_text(encoding="utf-8"))["materials"]
            self.assertEqual(mats["crystal"]["kind"], "translucent")
            self.assertAlmostEqual(mats["crystal"]["translucent"]["thickness_mm"], 5.0)
            # the tint is defined through a fixed 4 mm reference wall, so thickness_mm is a real density lever
            self.assertAlmostEqual(mats["crystal"]["translucent"]["attenuation_distance_mm"], 4.0)
            rec = mexport.export_glb(Path(r["parts_npz"]), Path(r["materials_json"]), root / "model.glb")
            self.assertTrue(rec["contract"]["ok"], rec["contract"]["failures"])
            self.assertIn("frame_material_translucent", rec["receipt"]["flags"])
            self.assertEqual(rec["contract"]["checks"]["lens_detection"]["value"]["runtime_lens_meshes"], 2)

    def test_crystal_temples_export_and_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = self._build(PROGRAM.replace("TEMPLE_MATERIAL", "crystal"), root)
            rec = mexport.export_glb(Path(r["parts_npz"]), Path(r["materials_json"]), root / "model.glb")
            self.assertTrue(rec["contract"]["ok"], rec["contract"]["failures"])
            for flag in ("frame_material_translucent", "temple_R_material_translucent", "temple_L_material_translucent"):
                self.assertIn(flag, rec["receipt"]["flags"])
            self.assertEqual(rec["contract"]["checks"]["lens_detection"]["value"]["runtime_lens_meshes"], 2)


def _perspective_front_render(width: int = 640, height: int = 480, camera_z_m: float = 0.30, fov_deg: float = 40.0,
                              yaw_deg: float = 0.0, view: str = "front") -> dict:
    """A fake actual-AR front render: identity mesh_to_world, a camera on +Z looking down -Z at the model (metres),
    a perspective clip matrix like the harness's, column-major lists as it records them. Perspective matters: the arms
    run toward -Z, so an orthographic front view would see them end-on, while perspective shows their inner faces."""
    f = 1.0 / np.tan(np.radians(fov_deg) / 2)
    near, far = 0.05, 2.0
    P = np.array([[f * height / width, 0, 0, 0], [0, f, 0, 0],
                  [0, 0, -(far + near) / (far - near), -2 * far * near / (far - near)], [0, 0, -1, 0]])
    V = np.eye(4)
    V[2, 3] = -camera_z_m
    c, s_ = np.cos(np.radians(yaw_deg)), np.sin(np.radians(yaw_deg))
    M = np.array([[c, 0, s_, 0], [0, 1, 0, 0], [-s_, 0, c, 0], [0, 0, 0, 1]])   # yaw about +Y: the arms swing into view
    m2w = M.T.ravel().tolist()
    return {"mode": "actual-ar", "view": view, "filename": f"{view}.png",
            "camera": {"projection_matrix": P.T.ravel().tolist(), "view_matrix": V.T.ravel().tolist(), "width": width, "height": height},
            "spatial": {"lenses": [{"name": "lens_R", "mesh_to_world": m2w}, {"name": "lens_L", "mesh_to_world": m2w}]}}


# the harness's requested_pose.matrix for the angled view (yaw_degrees 35, rotation order YXZ, column-major), as
# ar/qa/provider-comparison-ar.html recorded it in test-pilot-001 r0002 observe/ar/report.json: the synthetic render's
# yaw is the harness's, so the arm that swings toward the camera here is the one that does in the runtime
HARNESS_ANGLED_POSE_ROTATION = [0.8191520442889918, 0, -0.573576436351046, 0, 0, 1, 0, 0, 0.573576436351046, 0, 0.8191520442889918, 0]


def _paint(glb: Path, render: dict, shape: tuple[int, int], part: str, keep=lambda z: True) -> np.ndarray:
    """Pixel mask of one part's triangles whose three vertices satisfy ``keep(z)`` (mesh-local metres), projected the way
    the harness row says; an independent rasterisation for building fixture images, not the code under test."""
    from PIL import Image, ImageDraw
    from reconstruction.mesh import load_glb_bytes
    from modeler.lens_colour import _mat4, project_pixels
    mesh = load_glb_bytes(glb.read_bytes())
    cam = render["camera"]
    M = _mat4(cam["projection_matrix"]) @ _mat4(cam["view_matrix"]) @ _mat4(render["spatial"]["lenses"][0]["mesh_to_world"])
    px, ok = project_pixels(mesh.vertices, M, shape[1], shape[0])
    img = Image.new("L", (shape[1], shape[0]), 0)
    draw = ImageDraw.Draw(img)
    for p in mesh.parts:
        if p["name"] != part:
            continue
        for tri in mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]]:
            if ok[tri].all() and all(keep(float(z)) for z in mesh.vertices[tri, 2]):
                draw.polygon([tuple(px[i]) for i in tri], fill=255)
    return np.asarray(img) > 0


class TempleSeeThrough(unittest.TestCase):
    """see_through.frame_mask_in_render reaches the temples; the metric measures temple_see_through in the angled view
    beside the front's number, and asks the harness for that view only when the temples are translucent."""

    @classmethod
    def setUpClass(cls):
        from modeler import see_through
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        parts, materials = bsa_export.synthetic_parts()
        materials["lens"] = mexport.material_spec("lens", CANONICAL_LENS, lens=True)
        materials["frame"] = mexport.material_spec("frame", TRANSLUCENT, lens=False)
        materials["temple"] = mexport.material_spec("temple", TRANSLUCENT, lens=False)
        cls.glb = root / "crystal.glb"
        bsa_export.write_glb(parts, materials, cls.glb)
        cls.materials_json = root / "materials.json"
        cls.materials_json.write_text(json.dumps({"materials": {"frame": TRANSLUCENT, "temple": TRANSLUCENT, "lens": CANONICAL_LENS}}),
                                      encoding="utf-8")
        cls.renders = {"front": _perspective_front_render(),
                       "angled": _perspective_front_render(yaw_deg=see_through.TEMPLE_VIEW["yaw_degrees"], view="angled")}
        cls.render = cls.renders["front"]
        cls.shape = (480, 640)
        cls.width_mm = float(np.ptp(parts["frame"]["V"][:, 0]))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_mask_reaches_the_temples_by_part_name(self):
        from modeler import see_through
        shape = self.shape
        frame = see_through.frame_mask_in_render(self.glb, self.render, shape, {"frame"})
        self.assertIsNotNone(frame)
        self.assertGreater(int(frame.sum()), 0)
        # the frame's translucent primitives do not carry the temple material: the default (frame-only) call finds nothing
        self.assertIsNone(see_through.frame_mask_in_render(self.glb, self.render, shape, {"temple"}))
        temples = see_through.frame_mask_in_render(self.glb, self.render, shape, {"temple"}, part_names=see_through.TEMPLE_PARTS)
        self.assertIsNotNone(temples)
        ys, xs = np.nonzero(temples)
        self.assertGreater(len(xs), 0)
        self.assertTrue((xs > 320).any() and (xs < 320).any(), "both temples project, one on each side")
        # head-on the arms sit behind the front: nothing of them is exposed; in the angled view a full strip is
        self.assertEqual(int((temples & ~frame).sum() > 0), 1)
        self.assertLess(float((temples & ~frame).sum()), float(frame.sum()) * 0.05)
        ra = self.renders["angled"]
        temples_a = see_through.frame_mask_in_render(self.glb, ra, shape, {"temple"}, part_names=see_through.TEMPLE_PARTS)
        frame_a = see_through.frame_mask_in_render(self.glb, ra, shape)
        self.assertGreater(int((temples_a & ~frame_a).sum()), 10 * see_through.MIN_FRAME_PIXELS)

    def _images(self, fixtures):
        """Fixture renders per (fixture, view): the front passes half of the fixture colour, the exposed temples a quarter."""
        from modeler import see_through
        shape = self.shape
        images = {}
        for view, render in self.renders.items():
            frame = see_through.frame_mask_in_render(self.glb, render, shape)
            temples = see_through.frame_mask_in_render(self.glb, render, shape, {"temple"}, part_names=see_through.TEMPLE_PARTS)
            for key, colour in fixtures.items():
                c = see_through.hex_rgb(colour)
                img = np.tile(c, (shape[0], shape[1], 1)).astype(float)
                img[temples & ~frame] = 0.25 * c + 90.0
                img[frame] = 0.5 * c + 60.0
                images[key, view] = img
        return images

    def test_metric_reports_temple_see_through_beside_the_front(self):
        from unittest import mock
        from PIL import Image
        from modeler import see_through
        fixtures = {"skin": "#cba68d", "blue": "#3a4f6e"}
        images = self._images(fixtures)
        obs = Path(self.tmp.name) / "obs"
        requested = []

        def fake_run(models, out, **kw):
            requested.append([v["id"] for v in kw["ar_views"]])
            key = Path(out).name.rsplit("_", 1)[-1]
            Path(out).mkdir(parents=True, exist_ok=True)
            for view in ("front", "angled"):
                Image.fromarray(images[key, view].astype(np.uint8)).save(Path(out) / f"{view}.png")
            return {"models": {"candidate": {"runtime_compatible": True}}}

        def fake_front_render(out_dir, view="front"):
            key = Path(out_dir).name.rsplit("_", 1)[-1]
            return images[key, view], self.renders[view]
        with mock.patch.object(see_through.archeck, "run", fake_run), mock.patch.object(see_through, "_front_render", fake_front_render):
            r = see_through.frame_see_through_metric(self.glb, obs, self.materials_json, self.width_mm, fixtures=fixtures)
        self.assertEqual(requested, [["front", "angled"], ["front", "angled"]], "one harness run per fixture, both views in it")
        self.assertEqual(r["status"], "measured", r)
        self.assertAlmostEqual(r["see_through"], 0.5, places=2)
        t = r["temple_see_through"]
        self.assertEqual(t["status"], "measured", t)
        self.assertEqual(t["view"], "angled")
        self.assertAlmostEqual(t["see_through"], 0.25, places=2)
        self.assertGreater(t["temple_pixels"], see_through.MIN_FRAME_PIXELS)
        self.assertEqual(t["translucent_materials"], ["temple"])
        from bsa.tryon import HARNESS_CLIP_ZM
        self.assertEqual((t["near_temple"], t["clip_zm"]), ("temple_L", HARNESS_CLIP_ZM),
                         "+35 deg yaw brings the -X arm (temple_L) toward the camera; no recorded endpoint: the registered clip")
        self.assertEqual(sorted(t["renders"]), ["blue", "skin"])
        self.assertTrue(all(Path(v).name == "angled.png" for v in t["renders"].values()))
        summary = see_through.temple_summary(r)
        self.assertEqual((summary["view"], summary["near_temple"]), ("angled", "temple_L"))
        self.assertAlmostEqual(summary["see_through"], 0.25, places=2)
        self.assertNotIn("renders", summary)

    def _measure(self, images, renders, fixtures, obs_name):
        """frame_see_through_metric over prepared (fixture, view) images and harness rows, the harness faked."""
        from unittest import mock
        from modeler import see_through

        def fake_run(models, out, **kw):
            Path(out).mkdir(parents=True, exist_ok=True)
            return {"models": {"candidate": {"runtime_compatible": True}}}

        def fake_front_render(out_dir, view="front"):
            return images[Path(out_dir).name.rsplit("_", 1)[-1], view], renders[view]
        with mock.patch.object(see_through.archeck, "run", fake_run), mock.patch.object(see_through, "_front_render", fake_front_render):
            return see_through.frame_see_through_metric(self.glb, Path(self.tmp.name) / obs_name, self.materials_json, self.width_mm,
                                                        fixtures=fixtures)

    def test_the_synthetic_yaw_is_the_harness_yaw(self):
        from modeler.lens_colour import _mat4
        m2w = _mat4(self.renders["angled"]["spatial"]["lenses"][0]["mesh_to_world"])
        np.testing.assert_allclose(m2w.T.ravel()[:12], HARNESS_ANGLED_POSE_ROTATION, atol=1e-12)

    def test_the_near_temple_is_the_arm_on_the_camera_side(self):
        """+35 deg (the harness's angled view) turns the -X arm, temple_L (R = +X, glasses_lib), toward the camera; -35 deg
        the +X arm. Only the near arm is projected: the far one is behind the front and the head's occluders."""
        from modeler import see_through
        from reconstruction.mesh import load_glb_bytes
        mesh = load_glb_bytes(self.glb.read_bytes())
        side = {p["name"]: np.sign(mesh.vertices[mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]].ravel(), 0].mean())
                for p in mesh.parts}
        self.assertEqual((side["temple_R"], side["temple_L"]), (1.0, -1.0))
        for yaw, near, far in ((35, "temple_L", "temple_R"), (-35, "temple_R", "temple_L")):
            render = _perspective_front_render(yaw_deg=yaw, view="angled")
            mask, _, info = see_through.near_temple_masks(self.glb, render, self.shape, {"temple"})
            self.assertEqual(info["near_temple"], near, yaw)
            full_near = _paint(self.glb, render, self.shape, near)
            full_far = _paint(self.glb, render, self.shape, far)
            self.assertGreater(int(mask.sum()), 0)
            self.assertEqual(int((mask & ~full_near).sum()), 0, "nothing outside the near arm")
            self.assertEqual(int((mask & full_far & ~full_near).sum()), 0, "nothing of the far arm")

    def test_temple_clip_follows_the_recorded_endpoints(self):
        """renderer.ts applyTempleEndpoint: per side max(templeEndMaximumZM, templeEnd<side>ZM); the registered clip
        (bsa.tryon.HARNESS_CLIP_ZM) when the harness row records no endpoint."""
        from bsa.tryon import HARNESS_CLIP_ZM
        from modeler import see_through
        self.assertEqual(see_through.temple_clip_zm({}), {-1: HARNESS_CLIP_ZM, 1: HARNESS_CLIP_ZM})
        timing = {"templeEndMaximumZM": -0.115, "templeEndNegativeZM": -0.13, "templeEndPositiveZM": -0.09}
        self.assertEqual(see_through.temple_clip_zm({"timing": timing}), {-1: -0.115, 1: -0.09})
        self.assertEqual(see_through.temple_clip_zm({"timing": {"templeEndMaximumZM": None}}), {-1: HARNESS_CLIP_ZM, 1: HARNESS_CLIP_ZM})

    def test_clipped_occluded_and_far_temple_pixels_are_not_measured(self):
        """The verifier's real probe (a crystal-temple r0002): 577 of 1394 averaged pixels were bare fixture (hidden by the
        head's occluders or behind the rear clip), 0.887 read where the drawn pixels read 0.809. Here the drawn part of the
        near arm passes a quarter of the fixture difference; behind the recorded endpoint the pixels are background (bare,
        or darkened by the eyewear shadow), a band of the drawn arm is hidden by the head (bare fixture), and the far arm,
        were it projected, would read 0.9. Only the drawn, visible near-arm pixels count."""
        from modeler import see_through
        from reconstruction.mesh import load_glb_bytes
        fixtures = {"skin": "#cba68d", "blue": "#3a4f6e"}
        zs = np.unique(load_glb_bytes(self.glb.read_bytes()).vertices[:, 2])
        cutoff = float(zs[np.argmin(np.abs(zs + 0.10))])          # a vertex plane: the drawn triangles end exactly there
        angled = dict(self.renders["angled"], timing={"templeEndMaximumZM": cutoff, "templeEndNegativeZM": cutoff, "templeEndPositiveZM": cutoff})
        renders = {"front": self.renders["front"], "angled": angled}
        shape = self.shape
        near_all = _paint(self.glb, angled, shape, "temple_L")
        near_drawn = _paint(self.glb, angled, shape, "temple_L", keep=lambda z: z >= cutoff)
        far_all = _paint(self.glb, angled, shape, "temple_R")
        frame_a = see_through.frame_mask_in_render(self.glb, angled, shape)
        visible = near_drawn & ~frame_a
        ys, xs = np.nonzero(visible)
        lo, hi = np.percentile(xs, [40, 60])
        hidden = visible & (np.arange(shape[1])[None, :] >= lo) & (np.arange(shape[1])[None, :] <= hi)
        self.assertGreater(int(hidden.sum()), see_through.MIN_FRAME_PIXELS)
        self.assertGreater(int((near_all & ~near_drawn & ~frame_a).sum()), see_through.MIN_FRAME_PIXELS)
        self.assertGreater(int((far_all & ~frame_a).sum()), see_through.MIN_FRAME_PIXELS)
        for behind_clip in ("bare", "shadowed"):
            images = {}
            for key, colour in fixtures.items():
                c = see_through.hex_rgb(colour)
                front = np.tile(c, (shape[0], shape[1], 1)).astype(float)
                front[see_through.frame_mask_in_render(self.glb, self.renders["front"], shape)] = 0.5 * c + 60.0
                img = np.tile(c, (shape[0], shape[1], 1)).astype(float)
                img[near_all] = c if behind_clip == "bare" else 0.8 * c         # behind the endpoint: background, maybe in shadow
                img[near_drawn] = 0.25 * c + 90.0
                img[hidden] = c                                                 # the head's occluders hide this band
                img[far_all] = 0.9 * c + 12.0
                img[frame_a] = 0.5 * c + 60.0
                images[key, "front"], images[key, "angled"] = front, img
            r = self._measure(images, renders, fixtures, f"obs_hidden_{behind_clip}")
            self.assertEqual(r["status"], "measured", r)
            t = r["temple_see_through"]
            self.assertEqual(t["status"], "measured", (behind_clip, t))
            self.assertAlmostEqual(t["see_through"], 0.25, places=3, msg=behind_clip)
            self.assertEqual((t["near_temple"], t["clip_zm"]), ("temple_L", round(cutoff, 4)))
            self.assertGreaterEqual(t["bare_fixture_pixels"], int(hidden.sum()) // 2, behind_clip)
            self.assertLessEqual(t["temple_pixels"], int((visible & ~hidden).sum()))

    def test_a_near_arm_hidden_everywhere_has_no_temple_pixels(self):
        """When every projected near-arm pixel shows the bare fixture in both renders, nothing is measured."""
        from modeler import see_through
        fixtures = {"skin": "#cba68d", "blue": "#3a4f6e"}
        shape = self.shape
        far_all = _paint(self.glb, self.renders["angled"], shape, "temple_R")
        frame_a = see_through.frame_mask_in_render(self.glb, self.renders["angled"], shape)
        images = {}
        for key, colour in fixtures.items():
            c = see_through.hex_rgb(colour)
            img = np.tile(c, (shape[0], shape[1], 1)).astype(float)
            img[far_all] = 0.9 * c + 12.0
            img[frame_a] = 0.5 * c + 60.0
            front = np.tile(c, (shape[0], shape[1], 1)).astype(float)
            front[see_through.frame_mask_in_render(self.glb, self.renders["front"], shape)] = 0.5 * c + 60.0
            images[key, "front"], images[key, "angled"] = front, img
        r = self._measure(images, self.renders, fixtures, "obs_all_hidden")
        t = r["temple_see_through"]
        self.assertEqual(t["status"], "no_temple_pixels", t)
        self.assertEqual(t["temple_pixels"], 0)
        self.assertGreater(t["bare_fixture_pixels"], see_through.MIN_FRAME_PIXELS)
        # translucent but unmeasurable is not opaque: the author is told, the key is not left absent
        self.assertEqual(see_through.temple_summary(r), {"status": "no_temple_pixels"})

    def test_temple_summary_is_absent_only_for_opaque_temples(self):
        """The key is absent for opaque temples (not_applicable) or no temple result at all; translucent temples always
        report: their numbers when measured, else the status (and the error a failure recorded), so 'translucent but
        unmeasurable' never reads as 'opaque'."""
        from modeler import see_through
        for t in (None, {"status": "not_applicable"}):
            self.assertIsNone(see_through.temple_summary({"status": "measured", "temple_see_through": t}))
        self.assertIsNone(see_through.temple_summary(None))
        self.assertEqual(see_through.temple_summary({"temple_see_through": {"status": "no_temple_pixels", "temple_pixels": 3,
                                                                            "bare_fixture_pixels": 400}}),
                         {"status": "no_temple_pixels"})
        self.assertEqual(see_through.temple_summary({"temple_see_through": {"status": "failed", "error": "E: x"}}),
                         {"status": "failed", "error": "E: x"})
        for status in ("no_temple_render", "render_mismatch", "no_temple_projection"):
            self.assertEqual(see_through.temple_summary({"temple_see_through": {"status": status, "view": "angled"}}),
                             {"status": status})

    def test_temple_failure_is_recorded_not_fatal(self):
        from unittest import mock
        from PIL import Image
        from modeler import see_through
        fixtures = {"skin": "#cba68d", "blue": "#3a4f6e"}
        images = self._images(fixtures)

        def fake_run(models, out, **kw):
            Path(out).mkdir(parents=True, exist_ok=True)
            Image.fromarray(images["skin", "front"].astype(np.uint8)).save(Path(out) / "front.png")
            return {"models": {"candidate": {"runtime_compatible": True}}}

        def no_angled(out_dir, view="front"):
            key = Path(out_dir).name.rsplit("_", 1)[-1]
            return (images[key, "front"], self.renders["front"]) if view == "front" else None
        with mock.patch.object(see_through.archeck, "run", fake_run), mock.patch.object(see_through, "_front_render", no_angled):
            r = see_through.frame_see_through_metric(self.glb, Path(self.tmp.name) / "obs3", self.materials_json, self.width_mm, fixtures=fixtures)
        self.assertEqual(r["status"], "measured", r)
        self.assertAlmostEqual(r["see_through"], 0.5, places=2)
        self.assertEqual(r["temple_see_through"]["status"], "no_temple_render")

    def test_metric_without_translucent_temples_says_not_applicable(self):
        from unittest import mock
        from PIL import Image
        from modeler import see_through
        root = Path(self.tmp.name)
        parts, materials = bsa_export.synthetic_parts()
        materials["lens"] = mexport.material_spec("lens", CANONICAL_LENS, lens=True)
        materials["frame"] = mexport.material_spec("frame", TRANSLUCENT, lens=False)
        glb = root / "front_only.glb"
        bsa_export.write_glb(parts, materials, glb)
        mj = root / "front_only.json"
        mj.write_text(json.dumps({"materials": {"frame": TRANSLUCENT, "lens": CANONICAL_LENS}}), encoding="utf-8")
        img = np.full((480, 640, 3), 120.0)
        requested = []

        def fake_run(models, out, **kw):
            requested.append([v["id"] for v in kw["ar_views"]])
            Path(out).mkdir(parents=True, exist_ok=True)
            Image.fromarray(img.astype(np.uint8)).save(Path(out) / "front.png")
            return {"models": {"candidate": {"runtime_compatible": True}}}
        with mock.patch.object(see_through.archeck, "run", fake_run), \
                mock.patch.object(see_through, "_front_render", lambda out_dir, view="front": (img, self.render)):
            r = see_through.frame_see_through_metric(glb, root / "obs2", mj, self.width_mm)
        self.assertEqual(requested, [["front"], ["front"]], "opaque temples cost no angled render")
        self.assertEqual(r["status"], "measured", r)
        self.assertEqual(r["temple_see_through"], {"status": "not_applicable"})

    def test_the_fixture_renders_are_made_once_and_shared_with_the_lens_colour(self):
        # observe.py renders the two fixtures once per candidate (the lens colour needs them for every lens) and hands the
        # same runs to the see-through metric: no second pair of harness starts
        from unittest import mock
        from PIL import Image
        from modeler import see_through
        fixtures = {"skin": "#cba68d", "blue": "#3a4f6e"}
        images = self._images(fixtures)
        requested = []

        def fake_run(models, out, **kw):
            requested.append((Path(out).name, kw["background"], kw["background_color"], [v["id"] for v in kw["ar_views"]]))
            Path(out).mkdir(parents=True, exist_ok=True)
            return {"models": {"candidate": {"runtime_compatible": True}}}

        def fake_front_render(out_dir, view="front"):
            return images[Path(out_dir).name.rsplit("_", 1)[-1], view], self.renders[view]
        obs = Path(self.tmp.name) / "obs_shared"
        with mock.patch.object(see_through.archeck, "run", fake_run), mock.patch.object(see_through, "_front_render", fake_front_render):
            rendered = see_through.render_fixtures(self.glb, obs, self.materials_json, self.width_mm)
            self.assertEqual(requested, [("ar_see_through_skin", "solid", "#cba68d", ["front", "angled"]),
                                         ("ar_see_through_blue", "solid", "#3a4f6e", ["front", "angled"])])
            self.assertEqual(rendered["status"], "rendered")
            self.assertEqual(rendered["fixtures"], fixtures)
            r = see_through.frame_see_through_metric(self.glb, obs, self.materials_json, self.width_mm, rendered=rendered)
        self.assertEqual(len(requested), 2, "the metric reused the runs")
        self.assertEqual(r["status"], "measured", r)
        self.assertAlmostEqual(r["see_through"], 0.5, places=2)
        self.assertEqual(r["temple_see_through"]["status"], "measured")
        failed = see_through.frame_see_through_metric(self.glb, obs, self.materials_json, self.width_mm,
                                                      rendered={"status": "render_failed", "fixture": "blue", "error": "x"})
        self.assertEqual((failed["status"], failed["fixture"], failed["error"]), ("render_failed", "blue", "x"))

    def test_fixture_renders_run_for_a_lens_without_any_translucent_material(self):
        from unittest import mock
        from modeler import see_through
        root = Path(self.tmp.name)
        requested = []

        def fake_run(models, out, **kw):
            requested.append([v["id"] for v in kw["ar_views"]])
            Path(out).mkdir(parents=True, exist_ok=True)
            return {"models": {"candidate": {"runtime_compatible": True}}}
        lens_only = root / "lens_only.json"
        lens_only.write_text(json.dumps({"materials": {"lens": CANONICAL_LENS, "frame": {"kind": "acetate"}}}), encoding="utf-8")
        nothing = root / "nothing.json"
        nothing.write_text(json.dumps({"materials": {"frame": {"kind": "acetate"}}}), encoding="utf-8")
        with mock.patch.object(see_through.archeck, "run", fake_run):
            r = see_through.render_fixtures(self.glb, root / "obs_lens_only", lens_only, self.width_mm)
            self.assertEqual((r["status"], requested), ("rendered", [["front"], ["front"]]))
            self.assertEqual(see_through.render_fixtures(self.glb, root / "obs_nothing", nothing, self.width_mm), {"status": "not_applicable"})
            self.assertEqual(len(requested), 2)
            self.assertEqual(see_through.frame_see_through_metric(self.glb, root / "obs_lens_only", lens_only, self.width_mm, rendered=r),
                             {"status": "not_applicable"})


# The harness's angled actual-AR row for test-pilot-001 r0002's crystal-temple variant (720x480, identity view, the asset
# in AR centimetres: yaw 35 pose @ GLASSES_OFFSET_CM @ 98.6x): the synthetic registration test renders through it.
HARNESS_ANGLED_PROJECTION = [1.0879011247525263, 0, 0, 0, 0, 1.6318516871287896, 0, 0, 0, 0, -1.0002000200020003, -1, 0, 0, -2.000200020002, 0]
HARNESS_ANGLED_MESH_TO_WORLD = [80.77394525573784, 0, -56.558525359006055, 0, 0, 98.60677980221372, 0, 0, 56.558525359006055, 0,
                                80.77394525573784, 0, 3.7465777193734495, 3.271027, -17.64933249797239, 1]
# What the runtime itself (ar/src/render/rear-drop.ts createRearDrop(root, -0.14, 0), temple-terminal-fit.ts
# createTempleTerminalFitEvaluator at the row's fit scale 0.98607, maximumZM = the clip below) computes for the
# ``_hardware_temple_parts`` asset, run under node 26 over three.js meshes of the same primitives on 2026-09-28: the
# hinge plane the spread pivots on, and the posterior return's per-side inset.
RUNTIME_SPREAD_START_ZM = -0.016
RUNTIME_TERMINAL_INSET_M = (0.027321296218282068, 0.027321296218282068)
ARM_SPREAD_M = 0.018                   # renderer.ts ARM_SPREAD_M, recorded as timing.armSpreadM


def _hardware_temple_parts():
    """synthetic_parts with crystal temples and an opaque metal strip on each arm's outer face (the hardware a real
    temple carries: pale_champagne_gold on r0002), z -25..-95 mm, as a second primitive of the temple node."""
    parts, materials = bsa_export.synthetic_parts()
    materials["lens"] = mexport.material_spec("lens", CANONICAL_LENS, lens=True)
    materials["frame"] = mexport.material_spec("frame", TRANSLUCENT, lens=False)
    materials["temple"] = mexport.material_spec("temple", TRANSLUCENT, lens=False)
    materials["hinge_metal"] = {"base_color": [0.85, 0.75, 0.5, 1.0], "metallic": 1.0, "roughness": 0.3}
    for side, sx in (("R", 1.0), ("L", -1.0)):
        V, F = np.asarray(parts[f"temple_{side}"]["V"]), np.asarray(parts[f"temple_{side}"]["F"])
        outer = V[:, 0] >= V[:, 0].max() - 1e-9 if sx > 0 else V[:, 0] <= V[:, 0].min() + 1e-9
        strip = outer[F].all(1) & (V[F, 2].max(1) <= -25.0) & (V[F, 2].min(1) >= -95.0)
        parts[f"temple_{side}"]["material"] = ["temple", "hinge_metal"]
        parts[f"temple_{side}"]["face_material"] = strip.astype(int)
    return parts, materials


def _runtime_arm_x(x: np.ndarray, z: np.ndarray, cutoff: float, maximum: float) -> np.ndarray:
    """The test's own statement of the runtime's arm shape (face-width.ts spreadArmX/armSpreadCurve with the fixed 18 mm,
    temple-terminal-fit.ts terminalFitX with the runtime's pinned insets), for arm vertices (|x| > 45 mm, behind the
    hinge): an independent rasterisation input, not the code under test."""
    start, rnd = RUNTIME_SPREAD_START_ZM, 0.006
    span = start - cutoff
    run = np.clip(start - z, 0, span)
    slope = ARM_SPREAD_M / (span - rnd / 2)
    offset = np.where(run < rnd, slope * run ** 2 / (2 * rnd), slope * (run - rnd / 2))
    x1 = np.sign(x) * np.maximum(np.abs(x) + offset, np.minimum(np.abs(x), 0.0455))
    end_zm = maximum + 0.005
    t0 = min(-0.075, end_zm + 0.065)
    t = np.clip((t0 - z) / (t0 - end_zm), 0, 1)
    inset = np.where(x1 < 0, RUNTIME_TERMINAL_INSET_M[0], RUNTIME_TERMINAL_INSET_M[1])
    return x1 - np.sign(x1) * inset * t * t * (3 - 2 * t)


class TempleRegistration(unittest.TestCase):
    """The temple measurement registers the arm the runtime DRAWS: its geometry spread 18 mm outward from the hinge to
    the clip cap and returned inward over the terminal band (renderer.ts setShape/setTerminalFit), not the undeformed
    arm; and the temple's own opaque hardware drawn over the crystal is not counted as crystal. The verifier's ground
    truth (2026-09-28, r0002 crystal temples with the near arm painted green through the local harness): the drawn arm
    reads 0.886; the undeformed, hardware-inclusive measure read 0.796."""

    @classmethod
    def setUpClass(cls):
        from bsa.tryon import HARNESS_CLIP_ZM
        from reconstruction.mesh import load_glb_bytes
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        parts, materials = _hardware_temple_parts()
        cls.glb = root / "crystal_hardware.glb"
        bsa_export.write_glb(parts, materials, cls.glb)
        cls.materials_json = root / "materials.json"
        cls.materials_json.write_text(json.dumps({"materials": {"frame": TRANSLUCENT, "temple": TRANSLUCENT, "lens": CANONICAL_LENS,
                                                                "hinge_metal": {"kind": "metal"}}}), encoding="utf-8")
        cls.mesh = load_glb_bytes(cls.glb.read_bytes())
        zs = np.unique(cls.mesh.vertices[:, 2])
        cls.clip = float(zs[np.argmin(np.abs(zs + 0.115))])        # a vertex plane: the drawn triangles end exactly there
        cls.cutoff = HARNESS_CLIP_ZM
        timing = {"templeEndMaximumZM": cls.clip, "templeEndNegativeZM": cls.clip, "templeEndPositiveZM": cls.clip,
                  "armSpreadM": ARM_SPREAD_M, "armSpreadStartZM": RUNTIME_SPREAD_START_ZM}
        cls.angled = {"mode": "actual-ar", "view": "angled", "filename": "angled.png", "timing": timing,
                      "camera": {"projection_matrix": HARNESS_ANGLED_PROJECTION, "view_matrix": np.eye(4).ravel().tolist(),
                                 "width": 720, "height": 480},
                      "spatial": {"lenses": [{"name": n, "mesh_to_world": HARNESS_ANGLED_MESH_TO_WORLD} for n in ("lens_R", "lens_L")]}}
        cls.front = _perspective_front_render()
        cls.shape = (480, 720)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _paint(self, part: str, material: str | None, deformed: bool, faces_of=None) -> np.ndarray:
        """temple_L / frame primitives (``material``: that material only, None: every one) drawn in front of the clip,
        with the runtime's arm shape or undeformed."""
        from PIL import Image, ImageDraw
        from modeler.lens_colour import _mat4, project_pixels
        V = self.mesh.vertices.astype(float).copy()
        if deformed:
            arm = (np.abs(V[:, 0]) > 0.045) & (V[:, 2] < RUNTIME_SPREAD_START_ZM)
            V[arm, 0] = _runtime_arm_x(V[arm, 0], V[arm, 2], self.cutoff, self.clip)
        M = _mat4(HARNESS_ANGLED_PROJECTION) @ _mat4(HARNESS_ANGLED_MESH_TO_WORLD)
        px, ok = project_pixels(V, M, self.shape[1], self.shape[0])
        img = Image.new("L", (self.shape[1], self.shape[0]), 0)
        draw = ImageDraw.Draw(img)
        for p in self.mesh.parts:
            if p["name"] != part or (material is not None and p["material"] != material):
                continue
            for tri in self.mesh.faces[p["face_start"]:p["face_start"] + p["face_count"]]:
                if ok[tri].all() and (V[tri, 2] >= self.clip - 1e-9).all():
                    draw.polygon([tuple(px[i]) for i in tri], fill=255)
        return np.asarray(img) > 0

    def _scene(self, fixtures):
        """The angled fixture renders of the runtime's drawn near arm: crystal passing a quarter of the fixture, the metal
        strip over it opaque, the eyewear's shadow darkening the skin around the arm (so pixels beside the drawn arm are
        not bare fixture), the front half see-through."""
        from scipy import ndimage
        from modeler import see_through
        crystal, metal = self._paint("temple_L", "temple", True), self._paint("temple_L", "hinge_metal", True)
        arm = crystal | metal
        shadow = ndimage.binary_dilation(arm, iterations=14) & ~arm
        frame_a = see_through.frame_mask_in_render(self.glb, self.angled, self.shape)
        images = {}
        for key, colour in fixtures.items():
            c = see_through.hex_rgb(colour)
            img = np.tile(c, (self.shape[0], self.shape[1], 1)).astype(float)
            img[shadow] = 0.8 * c
            img[crystal] = 0.25 * c + 90.0
            img[metal] = [170.0, 150.0, 100.0]
            img[frame_a] = 0.5 * c + 60.0
            front = np.tile(c, (480, 640, 1)).astype(float)
            front[see_through.frame_mask_in_render(self.glb, self.front, (480, 640))] = 0.5 * c + 60.0
            images[key, "front"], images[key, "angled"] = front, img
        return images, frame_a

    def test_the_drawn_arm_is_measured_not_the_undeformed_one_nor_its_hardware(self):
        from unittest import mock
        from scipy import ndimage
        from modeler import see_through
        fixtures = {"skin": "#cba68d", "blue": "#3a4f6e"}
        images, frame_a = self._scene(fixtures)
        fa, fb = see_through.hex_rgb(fixtures["skin"]), see_through.hex_rgb(fixtures["blue"])

        def reads(mask):
            core = ndimage.binary_erosion(mask & ~frame_a, iterations=1)
            return see_through._see_through_of(images["skin", "angled"], images["blue", "angled"], core, fa, fb)[0]
        # the scene discriminates: the undeformed arm and the hardware-inclusive drawn arm both read off the crystal
        self.assertGreater(reads(self._paint("temple_L", "temple", False)) - 0.25, 0.05, "undeformed: shadowed skin counted")
        self.assertLess(reads(self._paint("temple_L", "temple", True)) - 0.25, -0.05, "hardware counted as crystal")
        renders = {"front": self.front, "angled": self.angled}

        def fake_run(models, out, **kw):
            Path(out).mkdir(parents=True, exist_ok=True)
            return {"models": {"candidate": {"runtime_compatible": True}}}
        with mock.patch.object(see_through.archeck, "run", fake_run), \
                mock.patch.object(see_through, "_front_render", lambda out_dir, view="front": (images[Path(out_dir).name.rsplit("_", 1)[-1], view], renders[view])):
            r = see_through.frame_see_through_metric(self.glb, Path(self.tmp.name) / "obs", self.materials_json, 140.0, fixtures=fixtures)
        t = r["temple_see_through"]
        self.assertEqual(t["status"], "measured", t)
        self.assertEqual(t["near_temple"], "temple_L")
        self.assertAlmostEqual(t["see_through"], 0.25, places=3, msg=t)
        self.assertGreater(t["temple_pixels"], 4 * see_through.MIN_FRAME_PIXELS)
        self.assertGreater(t["hardware_pixels"], see_through.MIN_FRAME_PIXELS)
        self.assertEqual(t["arm_shape"], {"spread_m": ARM_SPREAD_M, "spread_start_zm": RUNTIME_SPREAD_START_ZM,
                                          "terminal_inset_m": [round(v, 4) for v in RUNTIME_TERMINAL_INSET_M]})

    def test_the_port_moves_the_arm_where_the_runtime_does(self):
        """runtime_arm_vertices against the runtime's own numbers: the arm vertices land on the test's statement of the
        shape (pinned runtime insets), the front, the lenses and the arm in front of the hinge do not move."""
        from modeler import see_through
        V, info = see_through.runtime_arm_vertices(self.mesh, self.glb.read_bytes(), self.angled)
        self.assertEqual(info["terminal_inset_m"], [round(v, 4) for v in RUNTIME_TERMINAL_INSET_M])
        O = self.mesh.vertices.astype(float)
        arm = (np.abs(O[:, 0]) > 0.045) & (O[:, 2] < RUNTIME_SPREAD_START_ZM)
        np.testing.assert_allclose(V[arm, 0], _runtime_arm_x(O[arm, 0], O[arm, 2], self.cutoff, self.clip), atol=2e-7)
        np.testing.assert_array_equal(V[~arm], O[~arm])
        np.testing.assert_array_equal(V[:, 1:], O[:, 1:], "the runtime's vertical drop is 0: nothing moves in y or z")
        self.assertGreater(float(np.abs(V[arm, 0] - O[arm, 0]).max()), 0.005)
        # a harness row that records no spread (older harness): the arm is projected as authored
        V0, info0 = see_through.runtime_arm_vertices(self.mesh, self.glb.read_bytes(), dict(self.angled, timing={}))
        self.assertIsNone(info0)
        np.testing.assert_array_equal(V0, O)

    def test_the_spread_curve_is_the_runtimes(self):
        """face-width.ts armSpreadCurve / spreadArmX at pivot -0.008, cap -0.14, 18 mm, x = -68.5 mm, as node evaluated
        the runtime module on 2026-09-28."""
        from modeler import see_through
        z = np.array([-0.01, -0.02, -0.05, -0.08, -0.1, -0.12, -0.135, -0.15])
        curve = [4.651162790697675e-05, 0.0012558139534883722, 0.005441860465116279, 0.009627906976744188,
                 0.01241860465116279, 0.015209302325581393, 0.01730232558139535, 0.018]
        np.testing.assert_allclose(see_through.arm_spread_curve(z, -0.008, -0.14, ARM_SPREAD_M), curve, rtol=1e-12, atol=1e-15)
        np.testing.assert_allclose(see_through.spread_arm_x(np.full(len(z), -0.0685), z, -0.008, -0.14, ARM_SPREAD_M),
                                   -0.0685 - np.array(curve), rtol=1e-12)
        self.assertEqual(float(see_through.spread_arm_x(np.array([0.03]), np.array([-0.1]), -0.008, -0.14, ARM_SPREAD_M)[0]), 0.03)
        with self.assertRaises(ValueError):
            see_through.arm_spread_curve(z, -0.136, -0.14, ARM_SPREAD_M)


if __name__ == "__main__":
    unittest.main()
