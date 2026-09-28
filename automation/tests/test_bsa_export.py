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

    def test_translucent_temple_exports_single_sided_with_a_flag(self):
        # a transmissive TEMPLE is a translucent temple (crystal products, 2026-09-28): the writer forces it single-sided
        # (back faces would enter the transmission pre-pass twice) and flags it, like a translucent front; whether the
        # runtime can classify it is the contract check frame_temple_materials
        self.mats["temple"]["transmission"] = 0.5
        self.mats["temple"]["double_sided"] = True
        r = export.write_glb(self.parts, self.mats, self.out)
        self.assertIn("temple_R_material_translucent", r["flags"])
        self.assertIn("temple_L_material_translucent", r["flags"])
        self.assertIn("temple_R_translucent_forced_single_sided", r["flags"])
        doc = read_glb(self.out)["doc"]
        temple = next(m for m in doc["materials"] if m["name"] == "temple")
        self.assertFalse(temple.get("doubleSided", False))
        self.assertGreater(temple["extensions"]["KHR_materials_transmission"]["transmissionFactor"], 0)

    def test_lens_descriptor_off_the_lenses_is_refused(self):
        # the relaxed temple rule keeps this guard: a canonical lens descriptor on a frame or temple material
        self.mats["temple"]["transmission"] = 0.5
        self.mats["temple"]["gltf"] = {"extensions": {export.LENS_APPEARANCE_EXTENSION: {"version": 1}}}
        with self.assertRaisesRegex(ValueError, "lens descriptor"):
            export.write_glb(self.parts, self.mats, self.out)

    def test_lens_descriptor_on_an_opaque_frame_part_is_refused(self):
        # the runtime treats any material carrying the descriptor as optical, transmission or not: an opaque temple or
        # frame with a descriptor would render as a lens, so the writer refuses it too (not only on translucent parts)
        for key in ("temple", "frame"):
            mats = {k: dict(v) for k, v in self.mats.items()}
            mats[key]["transmission"] = 0.0
            mats[key]["gltf"] = {"extensions": {export.LENS_APPEARANCE_EXTENSION: {"version": 1}}}
            with self.subTest(part=key), self.assertRaisesRegex(ValueError, "lens descriptor"):
                export.write_glb(self.parts, mats, self.out)

    def test_translucent_front_exports_with_a_flag(self):
        # a transmissive FRAME is a translucent front (2026-09-27); whether the runtime can classify it (canonical lenses
        # present, single-sided) is the contract check frame_temple_materials, not the writer's business
        self.mats["frame"]["transmission"] = 0.5
        r = export.write_glb(self.parts, self.mats, self.out)
        self.assertIn("frame_material_translucent", r["flags"])

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
        bare = {"contract": {"ok": True}, "export": {"nodes": ["frame", "temple_R", "temple_L", "lens_R", "lens_L"]},
                "archeck": {"status": "runtime_compatible", "optical_meshes_detected": 2}}
        # a row without the harness validation (a pre-2026-09-27 record) is legacy/unverified: never compatible
        self.assertFalse(export.m1_criterion_1(bare))
        r = dict(bare, archeck=dict(bare["archeck"], validation={"ok": True, "harness_ok": True, "model": {"ok": True}}))
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


# --------------------------------------------------------------------------- facets (test-pilot-002 r0006, 2026-09-28)
def bevelled_disc(radius=10.0, bevel=1.0, segments=4, k=96, thickness=3.0, spacing=1.8, seed=0):
    """A flat plate (a disc, irregular Delaunay top like ``plate_with_holes``) whose top edge rolls over in a
    ``segments``-step round bevel (22.5 deg steps at 4), a vertical wall and a fan bottom. Returns V, F and a per-face
    role: 0 top plate, 1 bevel, 2 wall, 3 bottom."""
    from scipy.spatial import Delaunay
    a = np.linspace(0, 2 * np.pi, k, endpoint=False)
    rng = np.random.default_rng(seed)
    g = np.arange(-radius, radius + 1e-9, spacing)
    xy = np.stack(np.meshgrid(g, g), -1).reshape(-1, 2) + rng.uniform(-0.3, 0.3, (len(g) ** 2, 2)) * spacing
    xy = xy[np.linalg.norm(xy, axis=1) < radius - 0.6 * spacing]
    ring = np.c_[radius * np.cos(a), radius * np.sin(a)]
    P2 = np.vstack([ring, xy])
    top = Delaunay(P2).simplices
    V = [np.c_[P2, np.zeros(len(P2))]]
    rings = [np.arange(k)]
    n = len(P2)
    for j in range(1, segments + 1):                                  # bevel rings, quarter circle about (R, -b)
        t = np.pi / 2 - j * (np.pi / 2) / segments
        rr, z = radius + bevel * np.cos(t), -bevel + bevel * np.sin(t)
        V.append(np.c_[rr * np.cos(a), rr * np.sin(a), np.full(k, z)])
        rings.append(np.arange(n, n + k))
        n += k
    V.append(np.c_[(radius + bevel) * np.cos(a), (radius + bevel) * np.sin(a), np.full(k, -thickness)])
    rings.append(np.arange(n, n + k))
    n += k
    V.append([[0.0, 0.0, -thickness]])
    V = np.vstack(V)
    F, role = [top], [np.zeros(len(top), int)]
    for j in range(len(rings) - 1):
        r0, r1 = rings[j], rings[j + 1]
        q = np.c_[r0, np.roll(r0, -1), np.roll(r1, -1), r1]
        F.append(np.r_[q[:, [0, 1, 2]], q[:, [0, 2, 3]]])
        role.append(np.full(2 * k, 1 if j < segments else 2))
    last = rings[-1]
    F.append(np.c_[last, np.full(k, n), np.roll(last, -1)])
    role.append(np.full(k, 3))
    F, role = np.vstack(F), np.concatenate(role)
    fn, _ = export._face_normals(V, F)
    out = V[F].mean(1) - np.array([0.0, 0.0, -thickness / 2])        # convex: outward = away from the centre
    flip = np.einsum("ij,ij->i", fn, out) < 0
    F[flip] = F[flip][:, ::-1]
    return V, F, role


def rounded_tube(width=3.3, height=5.8, radius=0.85, per_corner=8, length=130.0, step=1.5, x0=68.0, y0=8.0):
    """A straight closed tube along -Z (a temple) with a rounded-rectangle section, ``per_corner`` points per 90 deg
    corner; ``radius=None`` gives an octagon (a coarse section: 45 deg turns when width == height)."""
    if radius is None:
        a = np.linspace(0, 2 * np.pi, 8, endpoint=False) + np.pi / 8
        sec = np.c_[width / 2 * np.cos(a), height / 2 * np.sin(a)]
    else:
        pts = []
        for c, (cx, cy) in enumerate([(width / 2 - radius, height / 2 - radius), (-width / 2 + radius, height / 2 - radius),
                                      (-width / 2 + radius, -height / 2 + radius), (width / 2 - radius, -height / 2 + radius)]):
            for t in np.linspace(c * np.pi / 2, (c + 1) * np.pi / 2, per_corner, endpoint=False):
                pts.append((cx + radius * np.cos(t), cy + radius * np.sin(t)))
        sec = np.asarray(pts)
    zs = np.linspace(-2.0, -2.0 - length, int(round(length / step)) + 1)
    K = len(sec)
    V = np.vstack([np.c_[sec[:, 0] + x0, sec[:, 1] + y0, np.full(K, z)] for z in zs])
    F = []
    for s in range(len(zs) - 1):
        a0, b0 = s * K, (s + 1) * K
        for i in range(K):
            j = (i + 1) % K
            F += [[a0 + i, b0 + i, b0 + j], [a0 + i, b0 + j, a0 + j]]
    F = np.asarray(F)
    V = np.vstack([V, [[x0, y0, zs[0]], [x0, y0, zs[-1]]]])
    c0, c1 = len(V) - 2, len(V) - 1
    last = (len(zs) - 1) * K
    F = np.vstack([F, [[c0, (i + 1) % K, i] for i in range(K)], [[c1, last + i, last + (i + 1) % K] for i in range(K)]])
    fn, _ = export._face_normals(V, F)
    cen = V[F].mean(1)
    out = cen - np.c_[np.full(len(F), x0), np.full(len(F), y0), cen[:, 2]]
    out[-2 * K:-K] = [0, 0, 1]
    out[-K:] = [0, 0, -1]
    flip = np.einsum("ij,ij->i", fn, out) < 0
    F[flip] = F[flip][:, ::-1]
    return V, F


def lens_sheet(cx, base_curve_radius=None):
    """A lens solid (ellipse 50 x 38 mm about x = cx) whose front is flat (None) or a sphere of that radius (mm)."""
    from shapely.geometry import Point
    from shapely import affinity
    poly = affinity.scale(Point(cx, 0).buffer(1.0, 64), 25, 19)
    if base_curve_radius is None:
        zf = lambda x, y: 0.0 * x - 1.0
    else:
        Rs = float(base_curve_radius)
        zf = lambda x, y: np.sqrt(Rs ** 2 - (x - cx) ** 2 - y ** 2) - Rs - 1.0
    V, F, _ = export.extrude_polygon(poly, zf, lambda x, y: np.full_like(x, 1.2), spacing_mm=1.0)
    return V, F


def facet_parts(frame=None, temple=None, lens_curve=None, reflectance=0.04):
    """frame + temples + a canonical lens pair; ``frame``/``temple`` = (V, F) or (V, F, N) replace the defaults."""
    V, F, _ = bevelled_disc()
    parts = {"frame": {"V": V, "F": F, "material": "frame"}}
    if frame is not None:
        parts["frame"] = {"V": frame[0], "F": frame[1], "material": "frame"}
        if len(frame) > 2:
            parts["frame"]["N"] = frame[2]
    tV, tF = temple if temple is not None else rounded_tube()
    for side, sx in (("R", 1.0), ("L", -1.0)):
        parts[f"temple_{side}"] = {"V": tV * [sx, 1, 1], "F": tF if sx > 0 else tF[:, ::-1], "material": "temple"}
    for side, cx in (("R", 32.0), ("L", -32.0)):
        lV, lF = lens_sheet(cx, lens_curve)
        parts[f"lens_{side}"] = {"V": lV, "F": lF, "material": "lens"}
    mats = {"frame": {"base_color": [0.9, 0.9, 0.9, 1], "roughness": 0.06},
            "temple": {"base_color": [0.9, 0.9, 0.9, 1], "roughness": 0.06},
            "lens": {"base_color": [0.5, 0.5, 0.5, 1], "roughness": 0.05, "transmission": 1.0, "ior": 1.5,
                     "lens_appearance": _descriptor(0.3, reflectance)}}
    return parts, mats


class FacetNormalsTest(unittest.TestCase):
    """The exporter's frame/temple normals: a flat plate stays flat next to its bevel (the frame-front staircase the owner
    saw in r0006: angle-weighted crease normals bled the 22.5 deg bevel steps into the plate's coarse triangles), the
    curvature stays in the bevel, and curved surfaces stay smooth."""

    def test_flat_plate_with_a_bevel_stays_flat_in_the_glb(self):
        parts, mats = facet_parts()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "disc.glb"
            export.write_glb(parts, mats, p)
            g = read_glb(p)
        pr = next(n for n in g["nodes"] if n["name"] == "frame")["primitives"][0]
        P, N, T = pr["P"].astype(float), pr["N"].astype(float), pr["I"].reshape(-1, 3)
        top_z = P[:, 2].max()
        fn, _ = export._face_normals(P, T)
        top = (fn[:, 2] > np.cos(np.radians(0.1))) & (np.abs(P[T][:, :, 2] - top_z).max(1) < 1e-7)
        self.assertGreater(top.sum(), 50)
        dev = np.degrees(np.arccos(np.clip(N[T[top]] @ np.array([0.0, 0.0, 1.0]), -1, 1)))
        self.assertLess(dev.max(), 0.5, "the plate's corner normals must stay within 0.5 deg of the plate normal")
        # the plate/bevel boundary stays smooth: a plate position carries one normal (not split between plate and bevel)
        key = np.round(P / 1e-8).astype(np.int64)
        _, inv = np.unique(key, axis=0, return_inverse=True)
        inv = inv.ravel()
        on_top = np.flatnonzero(np.abs(P[:, 2] - top_z) < 1e-7)
        self.assertEqual(len(np.unique(inv[on_top])), len(on_top), "a plate vertex is split between the plate and its bevel")
        # ... and the bevel carries the curvature: its middle ring leans about 45 deg
        tilt = np.degrees(np.arccos(np.clip(N[:, 2], -1, 1)))
        mid = np.abs(P[:, 2] - (top_z - 1e-3 * (1 - np.sqrt(0.5)))) < 2e-6
        self.assertTrue(mid.any())
        self.assertTrue(np.all(np.abs(tilt[mid] - 45.0) < 6.0), tilt[mid][:8])

    def test_part_normals_versus_the_angle_weighted_auto_smooth(self):
        V, F, role = bevelled_disc()
        fn, _ = export._face_normals(V, F)
        N = export.part_normals(V, F)
        dev = np.degrees(np.arccos(np.clip(np.einsum("fij,fj->fi", N[role == 0], fn[role == 0]), -1, 1)))
        self.assertLess(dev.max(), 0.5)
        # the angle-weighted auto-smooth (still crease_normals' default, used for the lens fallbacks) is what bled
        old = export.crease_normals(V, F, 40.0)
        dev_old = np.degrees(np.arccos(np.clip(np.einsum("fij,fj->fi", old[role == 0], fn[role == 0]), -1, 1)))
        self.assertGreater(dev_old.max(), 3.0)

    def test_curved_surfaces_stay_smooth(self):
        V, F = uv_sphere()
        N = export.part_normals(V, F)
        radial = V[F] / np.linalg.norm(V[F], axis=-1, keepdims=True)
        # a coarse UV sphere (24 rings): only its pole cap (a shallow 48-face cone, 3.75 deg) shades as one flat region
        self.assertLess(np.degrees(np.arccos(np.clip((N * radial).sum(-1), -1, 1))).max(), 4.0)
        # a 24-sided barrel subdivided along its length: its flat strips are not plates (they would shade as facets)
        a = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        B = np.vstack([np.c_[5 * np.cos(a), 5 * np.sin(a), np.full(24, z)] for z in np.linspace(0, -30, 21)])
        BF = np.array([[s * 24 + i, s * 24 + (i + 1) % 24, (s + 1) * 24 + (i + 1) % 24] for s in range(20) for i in range(24)]
                      + [[s * 24 + i, (s + 1) * 24 + (i + 1) % 24, (s + 1) * 24 + i] for s in range(20) for i in range(24)])
        self.assertEqual(int(export.flat_regions(B, BF).max()), -1)
        # an irregular triangulation of a sphere (a random hull): the area weighting must not break it into blotches
        from scipy.spatial import ConvexHull
        rng = np.random.default_rng(0)
        P = rng.normal(size=(4000, 3))
        P = 10.0 * P / np.linalg.norm(P, axis=1, keepdims=True)
        Fh = ConvexHull(P).simplices.copy()
        fn, _ = export._face_normals(P, Fh)
        flip = np.einsum("ij,ij->i", fn, P[Fh].mean(1)) < 0
        Fh[flip] = Fh[flip][:, ::-1]
        err = np.degrees(np.arccos(np.clip((export.part_normals(P, Fh) * (P[Fh] / 10.0)).sum(-1), -1, 1)))
        self.assertLess(err.mean(), 1.8)
        self.assertLess(err.max(), 6.0)

    def test_rounded_sweep_strips_are_not_plates(self):
        # the long thin strips of a large rounded corner are near-planar along a straight run: taking them for plates
        # would split the corner into hard facets
        V, F = rounded_tube(width=6.0, height=9.0, radius=2.0, length=130.0)
        a = export.surface_audit(V, F, export.part_normals(V, F))
        self.assertLess(a["hard_crease_mm"], 1.0, a)
        self.assertLess(a["normal_bleed_fraction"], 0.05, a)

    def test_boxes_keep_their_corners(self):
        V, F = cube()
        N = export.part_normals(V, F)
        fn, _ = export._face_normals(V, F)
        self.assertTrue(np.allclose(N, fn[:, None, :], atol=1e-9))


class FacetAuditTest(unittest.TestCase):
    """receipt['audit']: measures of the delivered normals fed back to the author, with a flag and a one-line note."""

    def write(self, parts, mats):
        with tempfile.TemporaryDirectory() as td:
            return export.write_glb(parts, mats, Path(td) / "a.glb")

    def test_audit_block_shape(self):
        a = self.write(*facet_parts())["audit"]
        self.assertEqual(set(a), {"flags", "parts", "notes"})
        self.assertEqual(a["flags"], [])
        self.assertEqual(a["notes"], [])
        for name in ("frame", "temple_R", "temple_L", "lens_R", "lens_L"):
            self.assertIn(name, a["parts"])
            for k, v in a["parts"][name].items():
                self.assertIsInstance(v, (int, float), (name, k))
        for k in ("plane_area_mm2", "normal_bleed_mm2", "normal_bleed_fraction", "hard_crease_mm"):
            self.assertIn(k, a["parts"]["frame"])
        for k in ("normal_span_h_deg", "normal_span_v_deg", "reflectance"):
            self.assertIn(k, a["parts"]["lens_R"])

    def test_coarse_sweep_is_flagged_smooth_sweep_and_box_are_not(self):
        coarse = self.write(*facet_parts(temple=rounded_tube(4.5, 4.5, radius=None)))["audit"]   # 45 deg turns
        self.assertIn("faceted_sweep", coarse["flags"])
        self.assertGreater(coarse["parts"]["temple_R"]["hard_crease_mm"], 500.0)
        self.assertTrue(any(n.startswith("temple_R") and "tube_along_path" in n for n in coarse["notes"]), coarse["notes"])
        smooth = self.write(*facet_parts())["audit"]
        self.assertNotIn("faceted_sweep", smooth["flags"])
        self.assertLess(smooth["parts"]["temple_R"]["hard_crease_mm"], 1.0)
        box = self.write(*facet_parts(temple=cube(4.0, (68.0, 8.0, -20.0))))["audit"]
        self.assertNotIn("faceted_sweep", box["flags"])
        self.assertEqual(box["parts"]["temple_R"]["hard_crease_mm"], 0.0)

    def test_flat_plane_normal_bleed(self):
        V, F, _ = bevelled_disc()
        bled = self.write(*facet_parts(frame=(V, F, export.crease_normals(V, F, 40.0))))["audit"]   # the old normals
        self.assertIn("flat_plane_normal_bleed", bled["flags"])
        self.assertGreater(bled["parts"]["frame"]["normal_bleed_fraction"], 0.05)
        self.assertTrue(any(n.startswith("frame") for n in bled["notes"]), bled["notes"])
        own = self.write(*facet_parts())["audit"]
        self.assertNotIn("flat_plane_normal_bleed", own["flags"])
        self.assertLess(own["parts"]["frame"]["normal_bleed_fraction"], 0.05)
        self.assertGreater(own["parts"]["frame"]["plane_area_mm2"], 250.0)

    def test_planar_mirror_lens(self):
        flat = self.write(*facet_parts(reflectance=0.23))["audit"]
        self.assertIn("planar_mirror_lens", flat["flags"])
        self.assertLess(flat["parts"]["lens_R"]["normal_span_h_deg"], 1.0)
        note = next(n for n in flat["notes"] if n.startswith("lens_R"))
        self.assertIn("base curve", note)
        # a base-4 lens (sphere radius 530/4 mm) is curved enough; a flat lens without a coating mirrors little
        curved = self.write(*facet_parts(lens_curve=132.5, reflectance=0.23))["audit"]
        self.assertNotIn("planar_mirror_lens", curved["flags"])
        self.assertGreater(curved["parts"]["lens_R"]["normal_span_h_deg"], 8.0)
        plain = self.write(*facet_parts(reflectance=0.04))["audit"]
        self.assertNotIn("planar_mirror_lens", plain["flags"])


# --------------------------------------------------------------------------- the faceted_sweep audit measures sweeps only (2026-09-28)
def folded_plates(angle_deg=30.0, length=80.0, width=10.0, n_len=41, n_wid=11):
    """Two flat plates of ``width`` mm meeting along a straight ``length`` mm fold, turning ``angle_deg`` (a designed
    bend: a folded bridge or endpiece); plates narrower than the flat-region width are two strips on one crease."""
    xs, ys = np.linspace(0.0, width, n_wid), np.linspace(0.0, length, n_len)
    a = np.radians(angle_deg)
    V, F = [], []
    for side in (0, 1):
        base = len(V)
        for y in ys:
            for x in xs:
                V.append((-x, y, 0.0) if side == 0 else (x * np.cos(a), y, x * np.sin(a)))
        for j in range(n_len - 1):
            for i in range(n_wid - 1):
                q = base + j * n_wid + i
                f = [(q, q + 1, q + n_wid + 1), (q, q + n_wid + 1, q + n_wid)]
                F += f if side == 1 else [t[::-1] for t in f]
    return np.array(V, float), np.array(F)        # the two copies of the fold line x = 0 weld by position


def noisy_sphere(nu=180, nv=90, r=10.0, sigma=0.002, seed=0):
    """A dense UV sphere with scan-like vertex noise (the bsa pipeline's reconstructed meshes)."""
    th = np.linspace(0, np.pi, nv + 1)
    ph = np.linspace(0, 2 * np.pi, nu, endpoint=False)
    V = np.array([(r * np.sin(t) * np.cos(p), r * np.sin(t) * np.sin(p), r * np.cos(t)) for t in th for p in ph])
    F = []
    for i in range(nv):
        for j in range(nu):
            a, b, c, d = i * nu + j, i * nu + (j + 1) % nu, (i + 1) * nu + (j + 1) % nu, (i + 1) * nu + j
            F += [(a, d, c), (a, c, b)]
    return V + np.random.default_rng(seed).normal(0, sigma, V.shape), np.array(F)


def audit_of(V, F):
    return export.surface_audit(V, F, export.part_normals(V, F))


class SweepFacetAuditTest(unittest.TestCase):
    """faceted_sweep names hard creases running ALONG a swept section (parallel facet lines each at least
    AUDIT_SWEEP_CHAIN_MM long), not every hard shading edge: test-pilot-001 r0002's frame read 62 mm of short hard edges
    (a bevelled plate, stretched nose-pad spheres), a designed fold between two plates read as a facet line and a
    scan-noise sphere read hundreds of mm, and each note told the author to rebuild with gl.tube_along_path."""

    def test_a_designed_fold_between_two_plates_is_not_a_sweep(self):
        V, F = folded_plates(30.0, length=80.0, width=10.0)
        a = audit_of(V, F)
        self.assertGreater(a["hard_crease_mm"], 70.0, a)                  # the fold is one hard 80 mm line ...
        self.assertEqual(a["sweep_facet_mm"], 0.0, a)                     # ... between two flat regions
        flags, notes = export.audit_findings({"frame": a})
        self.assertNotIn("faceted_sweep", flags, notes)

    def test_a_single_crease_between_two_strips_is_not_a_sweep(self):
        # strips too narrow to be flat regions, one 50 deg crease: a single line has no parallel partner
        V, F = folded_plates(50.0, length=80.0, width=1.5, n_wid=3)
        a = audit_of(V, F)
        self.assertGreater(a["hard_crease_mm"], 70.0, a)
        self.assertEqual(a["sweep_facet_mm"], 0.0, a)
        self.assertNotIn("faceted_sweep", export.audit_findings({"frame": a})[0])

    def test_scan_noise_is_not_a_sweep(self):
        V, F = noisy_sphere()
        a = audit_of(V, F)
        self.assertGreater(a["hard_crease_mm"], export.AUDIT_FACETED_SWEEP_MM, a)   # short random hard edges ...
        self.assertEqual(a["sweep_facet_mm"], 0.0, a)                                # ... in no long line
        self.assertNotIn("faceted_sweep", export.audit_findings({"frame": a})[0])

    def test_a_coarse_sweep_is_measured_as_parallel_lines(self):
        V, F = rounded_tube(4.5, 4.5, radius=None)                       # an octagon: 8 facet lines of 130 mm
        a = audit_of(V, F)
        self.assertGreater(a["sweep_facet_mm"], 900.0, a)
        self.assertGreaterEqual(a["sweep_facet_lines"], 8, a)
        flags, notes = export.audit_findings({"temple_R": a})
        self.assertIn("faceted_sweep", flags)
        note = next(n for n in notes if n.startswith("temple_R"))
        self.assertIn("tube_along_path", note)
        self.assertIn("along", note)
        smooth = audit_of(*rounded_tube())
        self.assertEqual(smooth["sweep_facet_mm"], 0.0, smooth)

    def test_notes_past_the_cap_end_in_a_truncation_marker(self):
        from modeler.agentic import tools
        self.assertEqual(export.AUDIT_MAX_NOTES, tools.MAX_AUDIT_ITEMS, "the exporter and the build reply cap in step")
        bad = {"plane_area_mm2": 100.0, "normal_bleed_mm2": 50.0, "normal_bleed_fraction": 0.5, "hard_crease_mm": 900.0,
               "sweep_facet_mm": 900.0, "sweep_facet_lines": 8}
        flags, notes = export.audit_findings({f"part_{i:02d}": dict(bad) for i in range(30)})     # 60 notes
        self.assertEqual(len(notes), export.AUDIT_MAX_NOTES)
        self.assertIn("37 more", notes[-1])
        self.assertIn("export.json", notes[-1])
        self.assertEqual(flags, ["flat_plane_normal_bleed", "faceted_sweep"])
        shown = tools.export_audit({"receipt": {"audit": {"flags": flags, "notes": notes}}})
        self.assertEqual(shown["notes"][-1], notes[-1][:400], "the marker survives the build reply's cap")
        exact = export.audit_findings({f"part_{i:02d}": dict(bad) for i in range(12)})[1]         # 24 notes: no marker
        self.assertEqual(len(exact), 24)
        self.assertFalse(any("more audit notes" in n for n in exact))


# --------------------------------------------------------------------------- the sweep audit's cost is bounded (2026-09-28)
def reference_sweep_facet_lines(a, b, pa, pb):
    """sweep_facet_lines as first written (a ball query per edge; quadratic in the local density of hard edges): the
    oracle the bounded search must reproduce exactly."""
    import math
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    n = len(a)
    if not n:
        return 0.0, 0
    L = np.linalg.norm(pb - pa, axis=1)
    ends = np.r_[a, b]
    eid = np.r_[np.arange(n), np.arange(n)]
    deg = np.bincount(ends)
    order = np.argsort(ends, kind="stable")
    se, sid = ends[order], eid[order]
    joint = np.flatnonzero((se[1:] == se[:-1]) & (deg[se[1:]] == 2))
    G = coo_matrix((np.ones(len(joint)), (sid[joint], sid[joint + 1])), shape=(n, n))
    k, lab = connected_components(G, directed=False)
    clen = np.bincount(lab, weights=L, minlength=k)
    idx = np.flatnonzero((clen[lab] >= export.AUDIT_SWEEP_CHAIN_MM) & (L > 0))
    if not len(idx):
        return 0.0, 0
    mid = (pa[idx] + pb[idx]) / 2
    d = (pb[idx] - pa[idx]) / L[idx, None]
    cos_par = math.cos(math.radians(export.AUDIT_SWEEP_PARALLEL_DEG))
    partnered = np.zeros(len(idx), bool)
    for i, near in enumerate(cKDTree(mid).query_ball_point(mid, export.AUDIT_SWEEP_PARTNER_MM)):
        near = np.asarray(near, np.int64)
        near = near[lab[idx[near]] != lab[idx[i]]]
        partnered[i] = bool(len(near)) and bool((np.abs(d[near] @ d[i]) >= cos_par).any())
    beside = np.bincount(lab[idx], weights=L[idx] * partnered, minlength=k)
    line = (clen >= export.AUDIT_SWEEP_CHAIN_MM) & (beside >= 0.5 * clen)
    return float(clen[line].sum()), int(line.sum())


def hard_edges(V, F):
    """The hard, non-designed edges surface_audit hands to sweep_facet_lines (its own selection, repeated here)."""
    import math
    N = export.part_normals(V, F)
    N = N / np.maximum(np.linalg.norm(N, axis=-1, keepdims=True), 1e-12)
    fn, _ = export._face_normals(V, F)
    Fw = export.weld(V, F)
    a, b = Fw.ravel(), np.roll(Fw, -1, axis=1).ravel()
    na, nb = N.reshape(-1, 3), np.roll(N, -1, axis=1).reshape(-1, 3)
    pa, pb = V[F].reshape(-1, 3), np.roll(V[F], -1, axis=1).reshape(-1, 3)
    face = np.repeat(np.arange(len(F)), 3)
    swap = a > b
    a, b = np.where(swap, b, a), np.where(swap, a, b)
    na, nb = np.where(swap[:, None], nb, na), np.where(swap[:, None], na, nb)
    ok = a != b
    key = a * (int(Fw.max()) + 1) + b
    order = np.argsort(np.where(ok, key, -1), kind="stable")
    order = order[ok[order]]
    ks = key[order]
    start = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    cnt = np.diff(np.r_[start, len(ks)])
    two = start[cnt == 2]
    e1, e2 = order[two], order[two + 1]
    split = np.minimum(np.einsum("ij,ij->i", na[e1], na[e2]), np.einsum("ij,ij->i", nb[e1], nb[e2]))
    dih = np.einsum("ij,ij->i", fn[face[e1]], fn[face[e2]])
    hard = (split < math.cos(math.radians(export.AUDIT_HARD_SPLIT_DEG))) & \
        (dih > math.cos(math.radians(export.AUDIT_DESIGNED_CORNER_DEG)))
    reg = export.flat_regions(V, F) if hard.any() else np.full(len(F), -1)
    sweep = hard & ~((reg[face[e1]] >= 0) & (reg[face[e2]] >= 0))
    return a[e1[sweep]], b[e1[sweep]], pa[e1[sweep]], pb[e1[sweep]]


class BoundedSweepAuditTest(unittest.TestCase):
    """The faceted_sweep partner search ran one ball query per hard edge and held every neighbour list at once: a
    69k-face coarse tube (edges 0.03 mm long, thousands of neighbours within 6 mm) took 8.2 s / 4.7 GB. The bounded
    search asks for a few nearest neighbours and widens only the edges still undecided, in memory-capped batches; its
    answers equal the ball query's."""

    def test_the_bounded_search_equals_the_ball_query(self):
        cases = {"coarse tube": rounded_tube(4.5, 4.5, radius=None), "dense coarse tube": rounded_tube(4.5, 4.5, radius=None, step=0.3),
                 "rounded tube": rounded_tube(), "fold": folded_plates(30.0), "single crease": folded_plates(50.0, width=1.5, n_wid=3),
                 "noisy sphere": noisy_sphere(nu=90, nv=45)}
        for name, (V, F) in cases.items():
            a, b, pa, pb = hard_edges(V, F)
            self.assertEqual(export.sweep_facet_lines(a, b, pa, pb), reference_sweep_facet_lines(a, b, pa, pb), name)
        # and through surface_audit: the coarse tube's eight lines along 130 mm
        self.assertEqual(audit_of(*cases["dense coarse tube"])["sweep_facet_lines"], 8)

    def test_a_69k_face_coarse_tube_is_audited_quickly_in_bounded_memory(self):
        import time
        import tracemalloc
        V, F = rounded_tube(4.5, 4.5, radius=None, step=0.03)
        self.assertGreater(len(F), 69000)
        N = export.part_normals(V, F)
        tracemalloc.start()
        t0 = time.perf_counter()
        a = export.surface_audit(V, F, N)
        seconds = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertGreater(a["sweep_facet_mm"], 1000.0, a)
        self.assertEqual(a["sweep_facet_lines"], 8, a)
        self.assertLess(peak, 600e6, f"peak {peak / 1e6:.0f} MB")
        self.assertLess(seconds, 6.0)


# --------------------------------------------------------------------------- planar_front (2026-09-28)
def wrapped_front(radius=None, width=136.0, height=40.0, thickness=4.0, nx=69, ny=21):
    """A front plate (two sheets, front toward +Z) bent about the vertical axis on a cylinder of ``radius`` mm (None:
    flat): the shape gl.wrap_cylinder gives a plate_with_holes front."""
    xs, ys = np.linspace(-width / 2, width / 2, nx), np.linspace(-height / 2, height / 2, ny)
    X, Y = np.meshgrid(xs, ys)
    V, F = [], []
    for side, dz in ((0, 0.0), (1, -thickness)):
        if radius is None:
            P = np.c_[X.ravel(), Y.ravel(), np.full(X.size, dz)]
        else:
            r = radius + dz                                         # the back sheet: the inner cylinder
            t = X.ravel() / radius
            P = np.c_[r * np.sin(t), Y.ravel(), r * np.cos(t) - radius]
        base = side * X.size
        V.append(P)
        for j in range(ny - 1):
            for i in range(nx - 1):
                q = base + j * nx + i
                f = [(q, q + 1, q + nx + 1), (q, q + nx + 1, q + nx)]
                F += f if side == 0 else [tr[::-1] for tr in f]
    return np.vstack(V), np.asarray(F)


class PlanarFrontAuditTest(unittest.TestCase):
    """planar_front: a near-flat frame front reflects the AR room panel as one slab sliding across it (test-pilot-002's
    Tom Ford front, wrapped at 700 mm). The audit fits the front's wrap radius (z = a + b x + c x^2 + d y + e y^2 over
    the front-facing faces, radius -1 / 2c) and flags it above AUDIT_PLANAR_FRONT_RADIUS_MM."""

    def write(self, parts, mats):
        with tempfile.TemporaryDirectory() as td:
            return export.write_glb(parts, mats, Path(td) / "a.glb")

    def test_the_fitted_radius_recovers_the_wrap(self):
        for radius in (150.0, 300.0, 700.0):
            m = export.front_wrap(*wrapped_front(radius))
            self.assertAlmostEqual(m["front_wrap_radius_mm"], radius, delta=0.05 * radius)     # 150 reads 143.5
            self.assertTrue(125.0 < m["front_width_mm"] < 137.0, m)     # the projected width of the 136 mm plate
        # a tight wrap (the ends turn 39 deg) reads tighter through the quadratic: 90.5 mm for 100
        self.assertAlmostEqual(export.front_wrap(*wrapped_front(100.0))["front_wrap_radius_mm"], 100.0, delta=12.0)
        flat = export.front_wrap(*wrapped_front(None))
        self.assertEqual(abs(flat["front_wrap_radius_mm"]), export.AUDIT_FRONT_RADIUS_CAP_MM)

    def test_a_near_flat_front_is_flagged_with_a_note(self):
        for radius in (700.0, None):
            a = self.write(*facet_parts(frame=wrapped_front(radius)))["audit"]
            self.assertIn("planar_front", a["flags"], radius)
            self.assertGreater(abs(a["parts"]["frame"]["front_wrap_radius_mm"]), export.AUDIT_PLANAR_FRONT_RADIUS_MM)
            note = next(n for n in a["notes"] if n.startswith("frame") and "flat" in n)
            self.assertIn("wrap_cylinder", note)
            self.assertIn("top photo", note)

    def test_a_wrapped_front_is_not(self):
        for radius in (120.0, 300.0):
            a = self.write(*facet_parts(frame=wrapped_front(radius)))["audit"]
            self.assertNotIn("planar_front", a["flags"], radius)
            self.assertFalse([n for n in a["notes"] if n.startswith("frame")], a["notes"])

    def test_a_small_or_absent_front_is_not_measured(self):
        a = self.write(*facet_parts())["audit"]                     # the 22 mm disc: no front to fit
        self.assertNotIn("front_wrap_radius_mm", a["parts"]["frame"])
        self.assertNotIn("planar_front", a["flags"])
        self.assertEqual(export.front_wrap(*rounded_tube()), {})   # a temple-like part faces sideways
        flags, _ = export.audit_findings({"frame": {"plane_area_mm2": 0.0, "normal_bleed_mm2": 0.0,
                                                    "normal_bleed_fraction": 0.0, "hard_crease_mm": 0.0}})
        self.assertEqual(flags, [])
