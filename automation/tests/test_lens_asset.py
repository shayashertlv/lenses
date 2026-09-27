"""Fixture transport tests, not rendered appearance acceptance tests."""

import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from reconstruction.lens_appearance import DensityKeyframe, LensAppearance, VERTICAL_COORDINATE
from reconstruction.lens_asset import (
    BIN_CHUNK, EXTENSION, JSON_CHUNK, build_lens_fixture_glb, capability_report,
    conformance_cases, read_lens_fixture_glb, write_conformance_bundle,
)


def unpack(data):
    size, kind = struct.unpack_from("<II", data, 12)
    assert kind == JSON_CHUNK
    document = json.loads(data[20:20 + size])
    binary_size, binary_kind = struct.unpack_from("<II", data, 20 + size)
    assert binary_kind == BIN_CHUNK
    return document, data[28 + size:28 + size + binary_size]


def repack(document, binary):
    encoded = json.dumps(document, allow_nan=False).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary += b"\0" * (-len(binary) % 4)
    length = 12 + 8 + len(encoded) + 8 + len(binary)
    return (struct.pack("<4sII", b"glTF", 2, length) + struct.pack("<II", len(encoded), JSON_CHUNK) + encoded
            + struct.pack("<II", len(binary), BIN_CHUNK) + binary)


class LensAssetTests(unittest.TestCase):
    def setUp(self):
        self.lens = conformance_cases()["mirrored_gradient"]
        self.glb = build_lens_fixture_glb(self.lens, columns=8, rows=4)

    def test_glb_has_embedded_aligned_geometry_and_optional_project_extension(self):
        magic, version, length = struct.unpack_from("<4sII", self.glb)
        self.assertEqual((magic, version, length), (b"glTF", 2, len(self.glb)))
        document, binary = unpack(self.glb)
        self.assertEqual(document["extensionsUsed"], [EXTENSION])
        self.assertNotIn(EXTENSION, document.get("extensionsRequired", []))
        self.assertEqual(document["buffers"], [{"byteLength": len(binary)}])
        for view in document["bufferViews"]:
            self.assertEqual(view["byteOffset"] % 4, 0)
            self.assertLessEqual(view["byteOffset"] + view["byteLength"], len(binary))

    def test_high_mirror_gradient_descriptor_is_preserved_exactly(self):
        asset = read_lens_fixture_glb(self.glb)
        self.assertEqual(asset.appearance.to_dict(), self.lens.to_dict())
        self.assertEqual(asset.appearance.normal_reflectance_rgb, (0.8, 0.55, 0.15))
        self.assertEqual(len(asset.appearance.optical_density_keyframes), 2)
        extension = asset.document["materials"][0]["extensions"][EXTENSION]
        self.assertEqual(set(extension), {"schema_version", "texcoord", "appearance"})
        self.assertEqual(extension["texcoord"], 0)
        self.assertEqual(extension["appearance"], self.lens.to_dict())

    def test_float_precision_survives_json_extension_roundtrip(self):
        lens = LensAppearance((DensityKeyframe(0, (0.01234567890123456, 1.2345678901234567, 5.67890123456789)),),
                              (0.9375123456789012, 0.6789123456789012, 0.2345678901234567),
                              1.5123456789012346, 0.09876543210987654)
        restored = read_lens_fixture_glb(build_lens_fixture_glb(lens)).appearance
        self.assertEqual(restored.to_dict(), lens.to_dict())
        np.testing.assert_array_equal(restored.evaluate(0.5, 75).transmission_rgb, lens.evaluate(0.5, 75).transmission_rgb)

    def test_uv_bottom_top_and_part_role_survive_export(self):
        asset = read_lens_fixture_glb(self.glb)
        extras = asset.document["nodes"][0]["extras"]
        self.assertEqual(extras["partRole"], "lens")
        self.assertEqual(extras["lensUVConvention"], VERTICAL_COORDINATE)
        np.testing.assert_allclose(asset.uv[asset.positions[:, 1].argmin(), 1], 0)
        np.testing.assert_allclose(asset.uv[asset.positions[:, 1].argmax(), 1], 1)
        np.testing.assert_allclose(asset.uv[:, 1], (asset.positions[:, 1] + 0.0225) / 0.045, atol=1e-7)
        self.assertEqual(asset.positions.shape, (45, 3))
        self.assertEqual(asset.indices.shape, (64, 3))

    def test_asymmetric_rear_descriptor_survives_glb_transport(self):
        appearance=LensAppearance.from_dict({**self.lens.to_dict(),'rear_reflection_fraction_rgb':[.1,.25,.8]})
        restored=read_lens_fixture_glb(build_lens_fixture_glb(appearance)).appearance
        self.assertEqual(restored,appearance)
        np.testing.assert_array_equal(restored.evaluate(.4,70,side='rear').reflectance_rgb,
                                      appearance.evaluate(.4,70,side='rear').reflectance_rgb)

    def test_normals_match_curvature_and_triangle_winding(self):
        asset = read_lens_fixture_glb(self.glb)
        p = asset.positions.astype(float)
        expected = np.column_stack((2 * 0.006 * p[:, 0] / 0.030**2,
                                     2 * 0.006 * p[:, 1] / 0.0225**2, np.ones(len(p))))
        expected /= np.linalg.norm(expected, axis=1, keepdims=True)
        np.testing.assert_allclose(asset.normals, expected, atol=1e-7)
        np.testing.assert_allclose(p[:, 2], 0.006 * (1 - (p[:, 0] / 0.030)**2 - (p[:, 1] / 0.0225)**2), atol=1e-9)
        face = p[asset.indices]
        cross = np.cross(face[:, 1] - face[:, 0], face[:, 2] - face[:, 0])
        self.assertTrue(np.all(cross[:, 2] > 0))

    def test_generic_fallback_explicitly_cannot_establish_fidelity(self):
        asset = read_lens_fixture_glb(self.glb)
        material = asset.document["materials"][0]
        self.assertTrue(material["extras"]["fallbackIsApproximate"])
        self.assertFalse(material["extras"]["fallbackEstablishesFidelity"])
        report = capability_report()
        self.assertEqual(report["extension_registration"], "unregistered_project_extension")
        self.assertFalse(report["fallback_establishes_fidelity"])
        self.assertEqual(report["production_renderer_integration"], "unmeasured")

    def test_total_mirror_keeps_lens_role_despite_zero_transmission(self):
        lens = conformance_cases()["total_mirror"]
        asset = read_lens_fixture_glb(build_lens_fixture_glb(lens))
        self.assertEqual(asset.document["nodes"][0]["extras"]["partRole"], "lens")
        np.testing.assert_array_equal(asset.appearance.evaluate(0.5).transmission_rgb, (0, 0, 0))
        np.testing.assert_array_equal(asset.appearance.evaluate(0.5).reflectance_rgb, (1, 1, 1))

    def test_canonical_field_loss_rejected(self):
        original, binary = unpack(self.glb)
        for field in self.lens.to_dict():
            document = copy.deepcopy(original)
            del document["materials"][0]["extensions"][EXTENSION]["appearance"][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                read_lens_fixture_glb(repack(document, binary))

    def test_valid_looking_mirror_reduction_detected_by_descriptor_digest(self):
        document, binary = unpack(self.glb)
        document["materials"][0]["extensions"][EXTENSION]["appearance"]["normal_reflectance_rgb"] = [0.3, 0.3, 0.3]
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            read_lens_fixture_glb(repack(document, binary))

    def test_role_extension_and_uv_field_loss_rejected(self):
        original, binary = unpack(self.glb)
        mutations = (
            lambda d: d["nodes"][0]["extras"].pop("partRole"),
            lambda d: d["nodes"][0]["extras"].pop("lensUVConvention"),
            lambda d: d["materials"][0]["extensions"].pop(EXTENSION),
            lambda d: d["meshes"][0]["primitives"][0]["attributes"].pop("TEXCOORD_0"),
            lambda d: d["extensionsUsed"].clear(),
            lambda d: d["materials"][0]["extensions"][EXTENSION].update(texcoord=1),
        )
        for mutation in mutations:
            document = copy.deepcopy(original)
            mutation(document)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                read_lens_fixture_glb(repack(document, binary))

    def test_vertical_uv_flip_rejected_even_with_unchanged_descriptor(self):
        document, original = unpack(self.glb)
        binary = bytearray(original)
        view = document["bufferViews"][2]
        count = document["accessors"][2]["count"]
        uv = np.frombuffer(binary, dtype="<f4", count=count * 2, offset=view["byteOffset"]).reshape(-1, 2)
        uv[:, 1] = 1 - uv[:, 1]
        with self.assertRaisesRegex(ValueError, "UVs"):
            read_lens_fixture_glb(repack(document, bytes(binary)))

    def test_buffer_overflow_and_truncation_rejected(self):
        document, binary = unpack(self.glb)
        document["accessors"][0]["count"] *= 1000
        with self.assertRaisesRegex(ValueError, "embedded buffer"):
            read_lens_fixture_glb(repack(document, binary))
        for invalid in (b"", self.glb[:-1], self.glb + b"junk"):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                read_lens_fixture_glb(invalid)

    def test_bundle_cases_cpu_samples_hashes_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_conformance_bundle(root)
            original = manifest_path.read_bytes()
            manifest = json.loads(original)
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(len(manifest["cases"]), 10)
            environments = {environment["id"]: environment for environment in manifest["environments"]}
            self.assertIn("transmission_only", environments)
            for case in manifest["cases"]:
                glb_path = root / case["model"]
                self.assertEqual(hashlib.sha256(glb_path.read_bytes()).hexdigest(), case["model_sha256"])
                asset = read_lens_fixture_glb(glb_path)
                self.assertEqual(asset.appearance.to_dict(), case["appearance"])
                self.assertEqual(len(case["samples"]), 63)
                for sample in case["samples"]:
                    np.testing.assert_allclose(np.array(sample["R"]) + sample["T"] + sample["A"], 1, atol=1e-15)
                    np.testing.assert_array_equal(sample["compositions"]["transmission_only"], sample["T"])
                    np.testing.assert_array_equal(sample["compositions"]["dark_mirror"], sample["R"])
                    environment = environments["colored_studio"]
                    expected = np.array(sample["T"]) * environment["background_linear_rgb"] + np.array(sample["R"]) * environment["reflected_linear_rgb"]
                    np.testing.assert_array_equal(sample["compositions"]["colored_studio"], expected)
            write_conformance_bundle(root)
            self.assertEqual(manifest_path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
