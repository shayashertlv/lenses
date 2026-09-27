"""bsa.export: GLB writer, normals, lens front-sheet rule, origin, textures (synthetic fixtures)."""
import io
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from bsa import export
from bsa.contract import read_glb


def cube(size=10.0, center=(0, 0, 0)):
    V = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float) * size / 2 + center
    F = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return V, F


def uv_sphere(r=20.0, n=24):
    th = np.linspace(0, np.pi, n + 1)[1:-1]
    ph = np.linspace(0, 2 * np.pi, 2 * n, endpoint=False)
    T, P = np.meshgrid(th, ph, indexing="ij")
    V = np.c_[(r * np.sin(T) * np.cos(P)).ravel(), (r * np.cos(T)).ravel(), (r * np.sin(T) * np.sin(P)).ravel()]
    V = np.vstack([V, [0, r, 0], [0, -r, 0]])
    m, k = len(th), len(ph)
    F = []
    for i in range(m - 1):
        for j in range(k):
            a, b, c, d = i * k + j, i * k + (j + 1) % k, (i + 1) * k + j, (i + 1) * k + (j + 1) % k
            F += [[a, b, d], [a, d, c]]
    top, bot = len(V) - 2, len(V) - 1
    for j in range(k):
        F += [[top, (j + 1) % k, j], [bot, (m - 1) * k + j, (m - 1) * k + (j + 1) % k]]
    F = np.array(F)
    fn, _ = export._face_normals(V, F)
    flip = np.einsum("ij,ij->i", fn, V[F].mean(1)) < 0
    F[flip] = F[flip][:, ::-1]
    return V, F


class NormalsTest(unittest.TestCase):
    def test_cube_creases_stay_flat(self):
        V, F = cube()
        N = export.crease_normals(V, F, 40.0)
        fn, _ = export._face_normals(V, F)
        self.assertTrue(np.allclose(N, fn[:, None, :], atol=1e-9))

    def test_sphere_is_smooth_radial(self):
        V, F = uv_sphere()
        N = export.crease_normals(V, F, 40.0)
        radial = V[F] / np.linalg.norm(V[F], axis=-1, keepdims=True)
        ang = np.degrees(np.arccos(np.clip((N * radial).sum(-1), -1, 1)))
        self.assertLess(ang.max(), 3.0)

    def test_unwelded_input_is_welded_for_incidence(self):
        V, F = uv_sphere()
        Vu = V[F].reshape(-1, 3)                  # every corner its own vertex (probe exporter style)
        Fu = np.arange(len(Vu)).reshape(-1, 3)
        self.assertTrue(np.allclose(export.crease_normals(Vu, Fu, 40.0), export.crease_normals(V, F, 40.0), atol=1e-9))

    def test_height_field_fit_exact_quadratic(self):
        rng = np.random.default_rng(3)
        xy = rng.uniform(-25, 25, (400, 2))
        z = -(xy[:, 0] ** 2 + 0.5 * xy[:, 1] ** 2) / 180.0 + 0.01 * xy[:, 0]
        fit = export.fit_height_field(np.c_[xy, z])
        self.assertLessEqual(fit["degree"], 2)
        self.assertLess(fit["rms_mm"], 1e-6)
        n = fit["normal"](np.c_[xy, z])
        ref = np.c_[2 * xy[:, 0] / 180 - 0.01, xy[:, 1] / 180, np.ones(len(xy))]
        ref /= np.linalg.norm(ref, axis=1, keepdims=True)
        self.assertTrue(np.allclose(n, ref, atol=1e-6))


class LensSheetTest(unittest.TestCase):
    def test_front_sheet_of_slab_is_its_front_cap(self):
        from shapely.geometry import Point
        from shapely import affinity
        poly = affinity.scale(Point(0, 0).buffer(1.0, 32), 20, 15)
        V, F, reg = export.extrude_polygon(poly, lambda x, y: -(x ** 2) / 300, lambda x, y: np.full_like(x, 1.5))
        mask, info = export.lens_front_sheet(V, F)
        self.assertTrue(np.array_equal(mask, reg == 0), info)

    def test_sheet_input_kept_whole(self):
        from shapely.geometry import Point
        V, F, reg = export.extrude_polygon(Point(0, 0).buffer(10, 16), lambda x, y: 0 * x, lambda x, y: 0 * x + 1)
        Fs = F[reg == 0]
        mask, _ = export.lens_front_sheet(V, Fs)
        self.assertTrue(mask.all())


class OriginTest(unittest.TestCase):
    def test_synthetic_bridge_underside(self):
        parts, _ = export.synthetic_parts()
        o, info = export.bridge_underside_mm(parts)
        # nose notch top at y = -6; plate front z = 0 at x = 0, 4 mm thick
        self.assertTrue(np.allclose(o, [0.0, -6.0, -2.0], atol=1e-6), info)


class WriteGlbTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.parts, cls.mats = export.synthetic_parts()
        cls.path = Path(cls.tmp.name) / "synthetic.glb"
        cls.receipt = export.write_glb(cls.parts, cls.mats, cls.path)
        cls.g = read_glb(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def node(self, name):
        return next(n for n in self.g["nodes"] if n["name"] == name)

    def test_nodes_identity_and_roles(self):
        names = [n["name"] for n in self.g["nodes"]]
        self.assertEqual(names, ["frame", "temple_R", "temple_L", "lens_R", "lens_L"])
        self.assertTrue(all(n["transform_identity"] for n in self.g["nodes"]))
        self.assertEqual(self.node("lens_R")["extras"]["partRole"], "lens")

    def test_positions_are_metres_about_the_origin(self):
        o = np.array(self.receipt["origin"]["origin_mm"])
        P = self.node("frame")["primitives"][0]["P"]
        self.assertAlmostEqual(float(np.ptp(P[:, 0])), 0.140, places=6)
        V = self.parts["frame"]["V"]
        expect = (V - o) / 1000.0
        # every exported position is one of the source vertices (float32)
        d = np.abs(P[:, None, :2] - expect[None, ::7, :2]).sum(-1).min(1)
        self.assertLess(np.median(d), 1e-3)
        self.assertAlmostEqual(float(P[:, 0].min() + P[:, 0].max()), 0.0, places=6)

    def test_lens_front_sheet_rule(self):
        for name in ("lens_R", "lens_L"):
            prims = self.node(name)["primitives"]
            self.assertEqual(len(prims), 1)
            p = prims[0]
            m = p["material"]
            self.assertGreater(m["extensions"]["KHR_materials_transmission"]["transmissionFactor"], 0)
            self.assertIn("KHR_materials_ior", m["extensions"])
            self.assertNotIn("KHR_materials_volume", m["extensions"])
            self.assertFalse(m.get("doubleSided", False))
            self.assertTrue((p["N"][:, 2] > 0).all())
            F = p["I"].reshape(-1, 3)
            fn, _ = export._face_normals(p["P"], F)
            self.assertTrue((fn[:, 2] > 0).all())
            self.assertEqual(len(F), self.receipt["parts"][name]["faces_out"])
            self.assertLess(len(F), self.receipt["parts"][name]["faces_in"] / 2)
            self.assertIn("TEXCOORD_0", self.g["doc"]["meshes"][self.node(name)["index"]]["primitives"][0]["attributes"])

    def test_opaque_frame_and_colours(self):
        fm = self.node("frame")["primitives"][0]["material"]
        self.assertNotIn("KHR_materials_transmission", fm.get("extensions", {}))
        self.assertIsNotNone(self.node("temple_R")["primitives"][0]["COLOR"])
        c = self.node("temple_R")["primitives"][0]["COLOR"][0]
        self.assertTrue(np.allclose(c[:3], export._srgb_to_linear(np.array([70, 45, 35]) / 255.0), atol=1e-6))

    def test_textures_jpeg(self):
        self.assertEqual({im["format"] for im in self.g["images"]}, {"JPEG"})

    def test_deterministic_bytes(self):
        p2 = Path(self.tmp.name) / "again.glb"
        r2 = export.write_glb(*export.synthetic_parts(), p2)
        self.assertEqual(r2["sha256"], self.receipt["sha256"])

    def test_reconstruction_loader_reads_it(self):
        from reconstruction.mesh import load_glb_bytes
        m = load_glb_bytes(self.path.read_bytes())
        self.assertEqual(len(m.faces), self.receipt["triangles"])

    def test_solid_profile_keeps_closed_lens(self):
        p = Path(self.tmp.name) / "solid.glb"
        r = export.write_glb(self.parts, self.mats, p, lens_profile="solid")
        self.assertEqual(r["parts"]["lens_R"]["faces_out"], r["parts"]["lens_R"]["faces_in"])


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self.parts, self.mats = export.synthetic_parts()
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "x.glb"

    def tearDown(self):
        self.tmp.cleanup()

    def test_lens_needs_transmission(self):
        self.mats["lens"]["transmission"] = 0.0
        with self.assertRaises(ValueError):
            export.write_glb(self.parts, self.mats, self.out)

    def test_frame_must_be_opaque(self):
        self.mats["frame"]["transmission"] = 0.5
        with self.assertRaises(ValueError):
            export.write_glb(self.parts, self.mats, self.out)

    def test_lens_names(self):
        self.parts["lens_C"] = self.parts.pop("lens_L")
        with self.assertRaises(ValueError):
            export.write_glb(self.parts, self.mats, self.out)
        self.parts["foo"] = self.parts.pop("lens_C")
        with self.assertRaises(ValueError):
            export.write_glb(self.parts, self.mats, self.out)

    def test_nan_rejected(self):
        self.parts["frame"]["V"] = self.parts["frame"]["V"].copy()
        self.parts["frame"]["V"][0, 0] = np.nan
        with self.assertRaises(ValueError):
            export.write_glb(self.parts, self.mats, self.out)

    def test_lens_rule_enforced_on_front_sheet(self):
        self.mats["lens"]["double_sided"] = True
        self.mats["lens"]["gltf"] = {"extensions": {"KHR_materials_volume": {"thicknessFactor": 0.002}}}
        r = export.write_glb(self.parts, self.mats, self.out)
        self.assertIn("lens_R_lens_material_forced_single_sided", r["flags"])
        self.assertIn("lens_R_lens_material_removed_KHR_materials_volume", r["flags"])
        doc = read_glb(self.out)["doc"]
        lens = next(m for m in doc["materials"] if m["name"] == "lens")
        self.assertFalse(lens.get("doubleSided", False))
        self.assertNotIn("KHR_materials_volume", lens["extensions"])
        self.assertNotIn("KHR_materials_volume", doc.get("extensionsUsed", []))

    def test_foreign_texture_index_rejected(self):
        self.mats["frame"] = {"gltf": {"pbrMetallicRoughness": {"baseColorTexture": {"index": 3}}}}
        with self.assertRaises(ValueError):
            export.write_glb(self.parts, self.mats, self.out)

    def test_single_lens_and_missing_temples_flagged(self):
        parts = {"frame": self.parts["frame"], "lens_C": self.parts["lens_R"]}
        r = export.write_glb(parts, self.mats, self.out)
        self.assertIn("missing_temple_R", r["flags"])
        self.assertEqual(r["nodes"], ["frame", "lens_C"])


class TextureTest(unittest.TestCase):
    def test_downscale_to_2048_jpeg(self):
        data, mime, info = export.encode_texture(np.zeros((100, 3000, 3), np.uint8) + 128)
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(info["size"], [2048, 68])
        self.assertEqual(Image.open(io.BytesIO(data)).size, (2048, 68))

    def test_alpha_keeps_png(self):
        a = np.zeros((8, 8, 4), np.uint8)
        a[..., 3] = 100
        self.assertEqual(export.encode_texture(a)[1], "image/png")

    def test_gradient_top_row_is_top_colour(self):
        g = export.gradient_texture([0.05, 0.05, 0.05], [0.8, 0.6, 0.4])
        self.assertEqual(g.shape, (64, 4, 3))
        self.assertLess(int(g[0, 0].sum()), int(g[-1, 0].sum()))
        self.assertTrue(np.allclose(g[-1, 0] / 255.0, export._linear_to_srgb(np.array([0.8, 0.6, 0.4])), atol=1 / 255))


if __name__ == "__main__":
    unittest.main()


class StageTest(unittest.TestCase):
    """S9 run() on fabricated upstream artifacts (S2/S5/S6/S7/S8 shaped as DESIGN.md describes)."""
    RUN = "_unittest_s9"

    @classmethod
    def setUpClass(cls):
        from unittest import mock
        from bsa import core
        from bsa.core import run_dir, stage_dir
        cls._tmp = tempfile.TemporaryDirectory()
        cls._patch = mock.patch.object(core, "BSA_DATA", Path(cls._tmp.name))   # hermetic: never automation/data
        cls._patch.start()
        cls.root = run_dir(cls.RUN, "vb")
        parts, _ = export.synthetic_parts()
        stage_dir(cls.RUN, "vb", "s2_front").save({"lenses": [{"side": "R"}, {"side": "L"}]})
        stage_dir(cls.RUN, "vb", "s5_temples").save(
            {"R": {"accepted": True}, "L": {"accepted": True}},
            {f"temple_{s}_{k}": parts[f"temple_{s}"][k] for s in "RL" for k in ("V", "F")})
        V, F = parts["frame"]["V"], parts["frame"]["F"]
        region = np.full(len(F), 2, np.int8)
        fn, _ = export._face_normals(V, F)
        region[fn[:, 2] > 0.5] = 0
        region[fn[:, 2] < -0.5] = 1
        uvpx = np.c_[(V[:, 0] + 70) * 10, (24 - V[:, 1]) * 10]
        a6 = {"frame_V": V, "frame_F": F, "frame_region": region, "frame_uv_px": uvpx}
        for i, s in ((1, "R"), (2, "L")):
            a6[f"lens{i}_V"], a6[f"lens{i}_F"], a6[f"lens{i}_uv"] = parts[f"lens_{s}"]["V"], parts[f"lens_{s}"]["F"], parts[f"lens_{s}"]["UV"]
        stage_dir(cls.RUN, "vb", "s6_assembly").save({"bridge_underside_mm": [0.0, -6.0, -2.0]}, a6)
        s7 = stage_dir(cls.RUN, "vb", "s7_texture")
        Image.fromarray(np.full((480, 1400, 3), (40, 50, 90), np.uint8)).save(s7.root / "front_cap.jpg")
        s7.save({"materials": {
            "frame_front": {"part": "frame", "region": [0], "texture": "front_cap.jpg", "uv": "frame_uv_px",
                            "factors": {"roughness": 0.35}},
            "frame_rest": {"part": "frame", "region": [1, 2], "factors": {"base_color": [0.02, 0.03, 0.08, 1.0]}},
            "temple_R": {"part": "temple_R", "factors": {"base_color": [0.02, 0.03, 0.08, 1.0]}}}})
        stage_dir(cls.RUN, "vb", "s8_lens").save({
            "class": "gradient", "gradient": {"top_linear_rgb": [0.05, 0.03, 0.02], "bottom_linear_rgb": [0.4, 0.3, 0.2]},
            "gltf_material": {"pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "roughnessFactor": 0.05, "metallicFactor": 0},
                              "extensions": {"KHR_materials_transmission": {"transmissionFactor": 1.0},
                                             "KHR_materials_ior": {"ior": 1.5},
                                             "KHR_materials_volume": {"thicknessFactor": 0.002}}}})
        cls.result = export.run("vb", cls.RUN, force=True, ar=False)

    @classmethod
    def tearDownClass(cls):
        cls._patch.stop()
        cls._tmp.cleanup()

    def test_exported_and_contract_ok(self):
        r = self.result
        self.assertEqual(r["status"], "exported")
        self.assertTrue(r["contract"]["ok"], r["contract"]["failures"])
        self.assertEqual(r["export"]["origin"]["method"], "supplied")
        self.assertTrue((self.root / "s9_export" / "result.json").exists())

    def test_materials_from_plan(self):
        g = read_glb(self.root / "s9_export" / "model.glb")
        frame = next(n for n in g["nodes"] if n["name"] == "frame")
        self.assertEqual(sorted(p["material"]["name"] for p in frame["primitives"]), ["frame_front", "frame_rest"])
        lens = next(n for n in g["nodes"] if n["name"] == "lens_R")["primitives"][0]["material"]
        self.assertNotIn("KHR_materials_volume", lens["extensions"])
        self.assertIn("baseColorTexture", lens["pbrMetallicRoughness"])
        self.assertTrue(any("temple_L" in n for n in self.result["notes"]))

    def test_run_skips_when_done_and_blocks_without_upstream(self):
        again = export.run("vb", self.RUN)
        self.assertEqual(again["export"]["sha256"], self.result["export"]["sha256"])
        self.assertEqual(export.run("miu", self.RUN)["status"], "blocked")
        self.assertFalse((self.root.parent / "miu" / "s9_export").exists())


def _descriptor(T=0.3, R=0.04, angular=None):
    from bsa import lens
    return lens.lens_appearance_descriptor(np.tile(np.linspace(T / 2, T, 8)[:, None], (1, 3)), np.full(3, R), angular)


class CanonicalLensTest(unittest.TestCase):
    """The runtime's canonical LENSES_lens_appearance lens (no blurred background): extension, per-mesh metadata and
    the lens-local height v exactly as ar/src/render/lens-material.ts validates them."""

    def write(self, ring=False):
        parts, mats = export.synthetic_parts()
        mats["lens"]["lens_appearance"] = _descriptor()
        if ring:
            mats["ring"] = {"base_color": [0.9, 0.9, 0.9, 1.0], "roughness": 0.5, "transmission": 0.5, "ior": 1.5,
                            "lens_appearance": _descriptor(0.5, 0.3)}
            for s in ("lens_R", "lens_L"):
                parts[s]["edge_ring"] = {"width_mm": 0.5, "material": "ring"}
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "c.glb"
        rec = export.write_glb(parts, mats, path)
        return read_glb(path), rec, path

    def test_descriptor_mesh_metadata_and_height_v(self):
        from bsa import contract
        g, rec, path = self.write()
        self.assertIn(export.LENS_APPEARANCE_EXTENSION, g["doc"]["extensionsUsed"])
        for n in g["nodes"]:
            if not n["name"].startswith("lens"):
                continue
            mesh = g["doc"]["meshes"][g["doc"]["nodes"][n["index"]]["mesh"]]
            self.assertEqual(mesh["extras"]["partRole"], "lens")
            self.assertEqual(mesh["extras"]["lensSurfaceProfile"], export.CANONICAL_SURFACE_PROFILE)
            for pr in n["primitives"]:
                m = pr["material"]
                ext = m["extensions"][export.LENS_APPEARANCE_EXTENSION]
                self.assertEqual((ext["schema_version"], ext["texcoord"]), (1, 0))
                self.assertNotIn("baseColorTexture", m["pbrMetallicRoughness"])         # flat fallback only
                self.assertFalse(m.get("doubleSided", False))
                v, y = pr["UV"][:, 1], pr["P"][:, 1]
                self.assertEqual(float(v.min()), 0.0)                                   # bottom exactly 0 ...
                self.assertEqual(float(v.max()), 1.0)                                   # ... top exactly 1
                np.testing.assert_allclose(v, (y - y.min()) / (y.max() - y.min()), atol=1e-6)   # the lens height
                self.assertTrue((pr["N"][:, 2] > 0).all())                              # normals toward +Z
                T = pr["I"].reshape(-1, 3)
                P = pr["P"]
                cz = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])[:, 2]
                self.assertTrue((cz > 0).all())                                         # +Z-wound sheet
        chk = contract.check(path)
        self.assertTrue(chk["ok"], chk["failures"])
        self.assertTrue(chk["checks"]["lens_detection"]["pass"])

    def test_edge_ring_is_a_second_canonical_primitive(self):
        g, rec, _ = self.write(ring=True)
        n = next(n for n in g["nodes"] if n["name"] == "lens_R")
        self.assertEqual(len(n["primitives"]), 2)
        for pr in n["primitives"]:                                                      # each Mesh covers v 0..1
            self.assertIn(export.LENS_APPEARANCE_EXTENSION, pr["material"]["extensions"])
            self.assertEqual((float(pr["UV"][:, 1].min()), float(pr["UV"][:, 1].max())), (0.0, 1.0))

    def test_mixed_canonical_and_legacy_optics_are_refused(self):
        parts, mats = export.synthetic_parts()
        mats["lens"]["lens_appearance"] = _descriptor()
        mats["legacy"] = {"base_color": [1, 1, 1, 1], "transmission": 1.0, "ior": 1.5}
        parts["lens_R"]["edge_ring"] = {"width_mm": 0.5, "material": "legacy"}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                export.write_glb(parts, mats, Path(td) / "x.glb")

    def test_canonical_sheet_drops_slivers_and_lifts_normals(self):
        P = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 0, 0], [1, 0, 0], [2, 0, 0]]], float)   # 2nd: zero area
        N = np.array([[[0, 0, 1]] * 3, [[1, 0, 0]] * 3], float)
        Pc, Nc, UV, info = export.canonical_sheet(P, N)
        self.assertEqual(info["dropped_faces"], 1)
        self.assertEqual(len(Pc), 1)
        P2 = P[:1].copy()
        N2 = np.array([[[1, 0, 0], [0, 0, 1], [0, 0, 1]]], float)
        _, Nc2, _, info2 = export.canonical_sheet(P2, N2)
        self.assertEqual(info2["normals_lifted"], 1)
        self.assertTrue((Nc2[..., 2] >= export.CANONICAL_MIN_NZ - 1e-12).all())
        np.testing.assert_allclose(np.linalg.norm(Nc2, axis=-1), 1.0)


class TempleAndCriterionTest(unittest.TestCase):
    def test_rejected_arm_keeps_its_donors(self):
        V = np.arange(30, dtype=float).reshape(10, 3)
        F = np.array([[0, 1, 2], [2, 3, 4], [5, 6, 7], [7, 8, 9]])
        UV = np.zeros((10, 2))
        a5 = {"temple_R_V": V, "temple_R_F": F, "temple_R_UV": UV, "donor_R_faces": np.array([2, 4])}
        full = export.temple_arrays(a5, {"R": {"accepted": True}}, "R")
        self.assertEqual(full[3], "full")
        self.assertEqual(len(full[1]), 4)
        don = export.temple_arrays(a5, {"R": {"accepted": False}}, "R")
        self.assertEqual(don[3], "donors_only")
        np.testing.assert_allclose(don[0], V[5:10])
        self.assertEqual(don[1].tolist(), [[0, 1, 2], [2, 3, 4]])
        self.assertEqual(len(don[2]), 5)
        a5.pop("donor_R_faces")
        self.assertIsNone(export.temple_arrays(a5, {"R": {"accepted": False}}, "R"))

    def test_c1_needs_every_lens_node(self):
        r = {"contract": {"ok": True}, "export": {"nodes": ["frame", "temple_R", "temple_L", "lens_R", "lens_L"]},
             "archeck": {"status": "runtime_compatible", "optical_meshes_detected": 2}}
        self.assertTrue(export.m1_criterion_1(r))
        r["archeck"]["optical_meshes_detected"] = 1                   # a pair with one lens found
        self.assertFalse(export.m1_criterion_1(r))
        r["export"]["nodes"] = ["frame", "lens_C"]
        self.assertTrue(export.m1_criterion_1(r))


class EdgeRingTest(unittest.TestCase):
    """Clear-lens frosted edge ring: the sheet is cut in place (no vertex leaves the original triangles)."""

    @staticmethod
    def disc_sheet(r=20.0, n_ring=160, bulge=0.8):
        """A CDT-like lens sheet: 0.8 mm ring spacing, a sparse interior, a gentle bulge toward +Z."""
        from shapely.geometry import Point
        poly = Point(0, 0).buffer(r, 64)
        V, F, region = export.extrude_polygon(poly, lambda x, y: bulge * (1 - (x ** 2 + y ** 2) / r ** 2),
                                              lambda x, y: np.full_like(x, 1.4), spacing_mm=0.8)
        keep = region == 0
        UV = np.c_[(V[:, 0] + r) / (2 * r), (r - V[:, 1]) / (2 * r)]
        return V, F[keep], UV

    def test_split_tiles_the_sheet_and_band_width(self):
        V, F, UV = self.disc_sheet()
        sp = export.split_edge_ring(V, F, 0.4, UV)
        _, a0 = export._face_normals(V, F)
        _, a1 = export._face_normals(sp["V"], sp["F"])
        self.assertAlmostEqual(a1.sum(), a0.sum(), delta=1e-6 * a0.sum())       # same surface, no gap/overlap
        ring_area = a1[sp["ring"]].sum()
        outline_mm = sp["info"]["outline_mm"]
        self.assertAlmostEqual(ring_area, np.pi * (20 ** 2 - 19.6 ** 2), delta=0.02 * ring_area)   # a 0.4 mm band
        # every new vertex lies on its parent triangle's plane (the surface did not move)
        n0, _ = export._face_normals(V, F)
        for k in range(0, len(sp["F"]), 7):
            pf = sp["parent"][k]
            d = (sp["V"][sp["F"][k]] - V[F[pf, 0]]) @ n0[pf]
            self.assertLess(np.abs(d).max(), 1e-9)
        # ring faces are within the band, interior faces outside it (centroids, xy distance to the outline)
        import shapely
        outline = shapely.geometry.Polygon(V[export._sheet_boundary(F)][:, 0, :2]).convex_hull.exterior
        dist = np.array([outline.distance(shapely.Point(c)) for c in sp["V"][sp["F"]].mean(1)[:, :2]])
        self.assertLess(dist[sp["ring"]].max(), 0.4 + 1e-6)
        self.assertGreater(dist[~sp["ring"]].min(), 0.0)
        self.assertEqual(sp["info"]["area_change_mm2"], 0.0)
        # outline unchanged: boundary length identical
        b2 = export._sheet_boundary(sp["F"])
        b0 = export._sheet_boundary(F)
        self.assertAlmostEqual(np.linalg.norm(sp["V"][b2[:, 0]] - sp["V"][b2[:, 1]], axis=1).sum(),
                               np.linalg.norm(V[b0[:, 0]] - V[b0[:, 1]], axis=1).sum(), places=6)
        # UV interpolated linearly: u follows x on this planar map
        np.testing.assert_allclose(sp["UV"][:, 0], (sp["V"][:, 0] + 20) / 40, atol=1e-9)
        # orientation kept (+Z facing)
        n1, _ = export._face_normals(sp["V"], sp["F"])
        self.assertTrue((n1[:, 2] > 0).all())

    def test_triangles_spanning_the_band_are_clipped(self):
        # a square sheet of two triangles: every vertex is on the outline, the diagonal crosses the interior
        V = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0]], float)
        F = np.array([[0, 1, 2], [0, 2, 3]])
        sp = export.split_edge_ring(V, F, 0.5)
        _, a1 = export._face_normals(sp["V"], sp["F"])
        self.assertAlmostEqual(a1[sp["ring"]].sum(), 100 - 9.0 ** 2, delta=1.0)
        self.assertAlmostEqual(a1.sum(), 100.0, places=6)

    def test_write_glb_ring_primitive(self):
        parts, mats = export.synthetic_parts()
        mats["lens_edge_ring"] = {"base_color": [0.86, 0.86, 0.86, 1.0], "metallic": 0.0, "roughness": 0.5,
                                  "transmission": 0.5, "ior": 1.5}
        for s in ("R", "L"):
            parts[f"lens_{s}"]["edge_ring"] = {"width_mm": 0.4, "material": "lens_edge_ring"}
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ring.glb"
            rec = export.write_glb(parts, mats, p)
            g = read_glb(p)
            from bsa import contract
            chk = contract.check(p)
        self.assertTrue(chk["ok"], chk.get("failures"))
        lens = next(n for n in g["nodes"] if n["name"] == "lens_R")
        names = sorted(pr["material"]["name"] for pr in lens["primitives"])
        self.assertEqual(names, ["lens", "lens_edge_ring"])
        for pr in lens["primitives"]:
            self.assertGreater(pr["material"]["extensions"]["KHR_materials_transmission"]["transmissionFactor"], 0)
        info = rec["parts"]["lens_R"]["edge_ring"]
        self.assertAlmostEqual(info["ring_area_mm2"] / info["outline_mm"], 0.4, delta=0.04)
        # the ring must not change the lens silhouette: same bbox as without it
        parts2, mats2 = export.synthetic_parts()
        with tempfile.TemporaryDirectory() as td:
            rec2 = export.write_glb(parts2, mats2, Path(td) / "plain.glb")
        self.assertEqual(rec["parts"]["lens_R"]["bbox_m"], rec2["parts"]["lens_R"]["bbox_m"])

    def test_opaque_ring_material_rejected(self):
        parts, mats = export.synthetic_parts()
        mats["lens_edge_ring"] = {"base_color": [1, 1, 1, 1], "roughness": 0.5}
        parts["lens_R"]["edge_ring"] = {"width_mm": 0.4, "material": "lens_edge_ring"}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                export.write_glb(parts, mats, Path(td) / "x.glb")
