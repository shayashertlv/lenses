"""bsa.generator (S1): GLB reading, canonical orientation, front-piece scale, decimation, artifacts."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
import open3d as o3d
from PIL import Image

from bsa import generator as g
from bsa import raster
from bsa.core import PRODUCTS, NormFrame, stage_dir


# --------------------------------------------------------------------------- synthetic glasses
def _box(lo, hi, subdiv=3):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    m = o3d.geometry.TriangleMesh.create_box(*(hi - lo))
    m.translate(lo)
    m = m.subdivide_midpoint(number_of_iterations=subdiv)
    return np.asarray(m.vertices), np.asarray(m.triangles)


def _merge(parts):
    V, F, n = [], [], 0
    for v, f in parts:
        V.append(v)
        F.append(f + n)
        n += len(v)
    return np.concatenate(V), np.concatenate(F).astype(np.int64)


def flat_glasses():
    """MODEL mm: 140 mm front plate (z -4..0) with a high bridge, temples back to z = -140."""
    return _merge([
        _box((-70, 15, -4), (70, 25, 0)),                                   # top bar
        _box((-70, -25, -4), (-12, 15, 0)), _box((12, -25, -4), (70, 15, 0)),   # lens blocks
        _box((-12, 5, -4), (12, 15, 0)),                                    # bridge
        _box((66, 8, -140), (70, 14, -4)), _box((-70, 8, -140), (-66, 14, -4)),  # temples
    ])


def wrapped_glasses(sagitta=45.0, half_width=70.0, n=120):
    """MODEL mm: a single-surface circular wrap (endpieces `sagitta` mm behind the front-most point)
    with straight parallel temples; the true front-piece width is 2 * half_width."""
    R = (half_width ** 2 + sagitta ** 2) / (2 * sagitta)
    phi0 = math.asin(half_width / R)
    phi = np.linspace(-phi0, phi0, n)
    ys = np.linspace(-20, 20, 9)
    P = np.array([[R * math.sin(a), y, R * math.cos(a) - R] for a in phi for y in ys])
    F = []
    for i in range(n - 1):
        for j in range(len(ys) - 1):
            a, b, c, d = i * len(ys) + j, i * len(ys) + j + 1, (i + 1) * len(ys) + j, (i + 1) * len(ys) + j + 1
            F += [[a, c, b], [b, c, d]]
    parts = [(P, np.asarray(F))]
    z_end = R * math.cos(phi0) - R
    parts += [_box((half_width - 0.01, 5, z_end - 100), (half_width, 10, z_end), 2),
              _box((-half_width, 5, z_end - 100), (-half_width + 0.01, 10, z_end), 2)]
    return _merge(parts)


def to_raw(V_model, scale=145.0, flipped=False):
    """Inverse of the canonical map: raw = R^T V / s (R = yaw -90, optionally after the 180 flip)."""
    R = g.R_FLIP @ g.R_YAW_M90 if flipped else g.R_YAW_M90
    return (V_model @ R) / scale


def write_glb(path: Path, P, F, UV=None, jpeg: bytes | None = None):
    """Minimal GLB writer for fixtures (one primitive, optional UV + JPEG base colour)."""
    P = np.asarray(P, np.float32)
    F = np.asarray(F, np.uint32)
    blobs, views, accessors = [], [], []

    def add(data: bytes, target=None):
        off = sum(len(b) for b in blobs)
        pad = (-len(data)) % 4
        blobs.append(data + b"\0" * pad)
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            v["target"] = target
        views.append(v)
        return len(views) - 1

    accessors.append({"bufferView": add(F.tobytes(), 34963), "componentType": 5125, "count": F.size, "type": "SCALAR"})
    accessors.append({"bufferView": add(P.tobytes(), 34962), "componentType": 5126, "count": len(P), "type": "VEC3",
                      "min": P.min(0).tolist(), "max": P.max(0).tolist()})
    attrs = {"POSITION": 1}
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
           "nodes": [{"mesh": 0}], "meshes": [{"primitives": [{"attributes": attrs, "indices": 0, "mode": 4}]}]}
    if UV is not None:
        accessors.append({"bufferView": add(np.asarray(UV, np.float32).tobytes(), 34962), "componentType": 5126,
                          "count": len(UV), "type": "VEC2"})
        attrs["TEXCOORD_0"] = 2
    if jpeg is not None:
        doc["images"] = [{"mimeType": "image/jpeg", "bufferView": add(jpeg)}]
        doc["textures"] = [{"source": 0}]
        doc["materials"] = [{"name": "m", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
        doc["meshes"][0]["primitives"][0]["material"] = 0
    doc["accessors"] = accessors
    doc["bufferViews"] = views
    binary = b"".join(blobs)
    doc["buffers"] = [{"byteLength": len(binary)}]
    js = json.dumps(doc).encode()
    js += b" " * ((-len(js)) % 4)
    out = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(binary))
    out += struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binary), 0x004E4942) + binary
    Path(path).write_bytes(out)


class ReadGLB(unittest.TestCase):
    def test_roundtrip_positions_faces_uv_and_native_texture_bytes(self):
        V, F = flat_glasses()
        UV = np.random.default_rng(0).random((len(V), 2)).astype(np.float32)
        buf = io.BytesIO()
        Image.fromarray(np.full((8, 8, 3), 90, np.uint8)).save(buf, "JPEG")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.glb"
            write_glb(p, V, F, UV, buf.getvalue())
            glb = g.read_glb(p)
        np.testing.assert_allclose(glb.positions, V.astype(np.float32), atol=0)
        np.testing.assert_array_equal(glb.faces, F)
        np.testing.assert_array_equal(glb.uv, UV)
        self.assertEqual(glb.textures["basecolor"], ("image/jpeg", buf.getvalue()))
        self.assertEqual(glb.primitives, 1)


class Canonicalize(unittest.TestCase):
    def test_flat_glasses_not_flipped_and_scaled_to_the_front_piece(self):
        V, F = flat_glasses()
        c = g.canonicalize(to_raw(V), F, 140.0)
        self.assertFalse(c["flipped"])
        self.assertTrue(c["checks_before_flip"]["unanimous"])
        self.assertTrue(c["checks_final"]["width_axis_is_x"])
        self.assertAlmostEqual(c["scale"], 145.0, places=6)
        self.assertFalse(c["front_piece"]["extended_past_20mm"])
        self.assertAlmostEqual(c["front_piece"]["depth_mm"], 20.0, places=6)
        np.testing.assert_allclose(to_raw(V) @ (c["scale"] * c["R"]).T, V, atol=1e-9)
        self.assertTrue(c["up_check"]["passed"])
        self.assertGreater(c["up_check"]["bridge_height_q"], 0.6)

    def test_flipped_generation_is_flipped_back(self):
        V, F = flat_glasses()
        c = g.canonicalize(to_raw(V, flipped=True), F, 140.0)
        self.assertTrue(c["flipped"])
        self.assertTrue(c["checks_before_flip"]["unanimous"])
        np.testing.assert_allclose(to_raw(V, flipped=True) @ (c["scale"] * c["R"]).T, V, atol=1e-9)
        self.assertAlmostEqual(np.linalg.det(c["R"]), 1.0)       # a rotation, never a mirror

    def test_up_check_reports_an_upside_down_generation(self):
        V, F = flat_glasses()
        V = V * [1, -1, 1]
        F = F[:, ::-1]
        c = g.canonicalize(to_raw(V), F, 140.0)
        self.assertFalse(c["up_check"]["passed"])

    def test_temples_wider_than_the_front_do_not_set_the_width(self):
        V, F = flat_glasses()
        splay = V.copy()
        behind = splay[:, 2] < -30
        splay[behind, 0] *= 1.0 + 0.1 * (-splay[behind, 2] - 30) / 110       # tips 10 % wider
        c = g.canonicalize(to_raw(splay), F, 140.0)
        self.assertAlmostEqual(c["scale"], 145.0, places=6)
        self.assertGreater(c["front_piece"]["total_width_mm"], 150.0)

    def test_wrapped_front_extends_the_slab_and_converges(self):
        V, F = wrapped_glasses(sagitta=45.0)
        c = g.canonicalize(to_raw(V), F, 140.0)
        fp = c["front_piece"]
        self.assertTrue(fp["extended_past_20mm"])
        self.assertLess(fp["width_at_20mm_mm"], 115.0)        # the literal rule sees only the centre
        self.assertGreater(fp["depth_mm"], 35.0)
        # the knee stops at most 2 tan(10 deg) * 10 mm = 3.5 mm (2.5 %) short of an abrupt endpiece
        self.assertLess(abs(c["scale"] / 145.0 - 1.0), 0.026)
        self.assertLess(fp["total_width_mm"], 145.0)

    def test_literal_twenty_mm_rule_would_run_away_on_a_wrap(self):
        """Documents why the rule is extended: at 20 mm the fixed point has no sane solution."""
        V, F = wrapped_glasses(sagitta=45.0)
        raw = to_raw(V) @ g.R_YAW_M90.T
        s = 140.0 / np.ptp(raw[:, 0])
        for _ in range(60):
            sel = raw[:, 2] >= raw[:, 2].max() - 20.0 / s
            s = 140.0 / np.ptp(raw[sel, 0])
        self.assertGreater(s * np.ptp(raw[:, 0]), 170.0)    # the whole frame would be > 170 mm wide


class TextureAndDecimation(unittest.TestCase):
    def test_sample_texture_bilinear_at_texel_centres(self):
        tex = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
        uv = np.array([[(2 + 0.5) / 6, (1 + 0.5) / 4], [(2 + 1.0) / 6, (1 + 0.5) / 4]])
        out = g.sample_texture(tex, uv)
        np.testing.assert_allclose(out[0], tex[1, 2])
        np.testing.assert_allclose(out[1], (tex[1, 2].astype(float) + tex[1, 3]) / 2)

    def test_interpolate_uv_uses_open3d_barycentrics(self):
        UV = np.array([[0, 0], [1, 0], [0, 1]], np.float32)
        F = np.array([[0, 1, 2]])
        uv = g.interpolate_uv(UV, F, np.array([0]), np.array([[0.25, 0.5]]))
        np.testing.assert_allclose(uv, [[0.25, 0.5]])

    def test_decimation_is_deterministic_and_close(self):
        m = o3d.geometry.TriangleMesh.create_torus(torus_radius=30, tube_radius=6, radial_resolution=120,
                                                   tubular_resolution=60)
        V, F = np.asarray(m.vertices), np.asarray(m.triangles)
        a = g.decimate(V, F, 2000)
        b = g.decimate(V, F, 2000)
        np.testing.assert_array_equal(a[0], b[0])
        np.testing.assert_array_equal(a[1], b[1])
        self.assertLessEqual(a[2]["faces"], 2000)
        self.assertGreater(a[2]["faces"], 1800)
        lo, hi = V.min(0), V.max(0)
        frame = NormFrame(tuple((lo + hi) / 2), float((hi - lo).max()))
        cam = raster.view_camera(frame, 20, 30, 5.0, (400, 400))
        full = raster.render(V, F, cam, frame, (400, 400))["mask"]
        dec = raster.render(a[0], a[1], cam, frame, (400, 400))["mask"]
        self.assertGreater(np.count_nonzero(full & dec) / np.count_nonzero(full | dec), 0.97)


class RealArtifacts(unittest.TestCase):
    """Checks on the saved m1 artifacts (skips products whose S1 has not run)."""

    def test_all_products(self):
        ran = 0
        for pid, prod in PRODUCTS.items():
            sd = stage_dir("m1", pid, g.STAGE)
            if not sd.done():
                continue
            ran += 1
            with self.subTest(product=pid):
                gen = g.load(pid, "m1")
                r = gen.result
                self.assertEqual(gen.V.dtype, np.float32)
                self.assertEqual(gen.F.dtype, np.int32)
                self.assertEqual(gen.UV.shape, (len(gen.V), 2))
                self.assertTrue(20_000 <= len(gen.Fd) <= 24_000)
                self.assertAlmostEqual(r["front_width_mm"], prod.front_width_mm, delta=1e-3)
                lo, hi = gen.V.astype(float).min(0), gen.V.astype(float).max(0)
                self.assertEqual(gen.frame, NormFrame(tuple(float(c) for c in (lo + hi) / 2), float((hi - lo).max())))
                self.assertAlmostEqual(r["front_z_mm"], float(hi[2]), places=4)
                for name in ("basecolor.jpg", "metallic_roughness.png", "normal.png", "sheet.png"):
                    self.assertTrue((sd.root / name).exists(), name)
                self.assertNotIn("plate_not_at_plus_z_after_flip", r["flags"])
                self.assertTrue(r["orientation_checks"]["up"]["passed"])
                M = np.asarray(r["raw_to_model"])
                self.assertAlmostEqual(abs(np.linalg.det(M[:3, :3])) ** (1 / 3), r["scale_mm_per_raw"], places=6)
                # the lens plate is what a front camera sees first: the first-hit depth is near front_z
                cam = raster.view_camera(gen.frame, 0, 0, 2.0, (400, 400))
                hit = gen.scene(True).render(cam, (400, 400), want_points=True)
                z = hit["points"][hit["mask"]][:, 2]
                self.assertGreater(float(np.median(z)), r["front_z_mm"] - 25.0)
                back = raster.view_camera(gen.frame, 180, 0, 2.0, (400, 400))
                zb = gen.scene(True).render(back, (400, 400), want_points=True)["points"]
                self.assertLess(float(np.nanmedian(zb[..., 2])), float(np.median(z)))
        if ran == 0:
            self.skipTest("no S1 m1 artifacts")

    def test_raw_to_model_reproduces_V(self):
        pid = "vb"
        sd = stage_dir("m1", pid, g.STAGE)
        if not sd.done() or not PRODUCTS[pid].generation_glb.exists():
            self.skipTest("vb artifacts or GLB missing")
        gen = g.load(pid, "m1")
        glb = g.read_glb(PRODUCTS[pid].generation_glb)
        M = np.asarray(gen.result["raw_to_model"])
        V = (glb.positions @ M[:3, :3].T + M[:3, 3]).astype(np.float32)
        np.testing.assert_array_equal(V, gen.V)
        self.assertEqual(gen.result["source_sha256"], __import__("bsa.core", fromlist=["x"]).sha256_file(PRODUCTS[pid].generation_glb))


if __name__ == "__main__":
    unittest.main()
