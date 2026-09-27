"""Adversarial correspondence checks, independent tiny GLB construction."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from qa.part_probe_audit import inventory, compare_hashes, bounded_correspondence, recentered_correspondence
from qa.part_probe_rays import inspect as inspect_rays


def glb(triangles, uv, translation=None):
    arrays = [np.asarray(triangles, dtype="<f4").reshape((-1, 3)), np.asarray(uv, dtype="<f4").reshape((-1, 2))]
    data, views, accessors = bytearray(), [], []
    for array, kind in zip(arrays, ("VEC3", "VEC2")):
        data.extend(b"\0" * (-len(data) % 4))
        views.append({"buffer": 0, "byteOffset": len(data), "byteLength": array.nbytes})
        data.extend(array.tobytes())
        accessors.append({"bufferView": len(views) - 1, "componentType": 5126, "type": kind, "count": len(array)})
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(data)}], "bufferViews": views,
           "accessors": accessors, "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}}]}],
           "nodes": [{"mesh": 0, **({"translation": translation} if translation is not None else {})}],
           "scenes": [{"nodes": [0]}], "scene": 0}
    encoded = json.dumps(doc).encode()
    encoded += b" " * (-len(encoded) % 4)
    data.extend(b"\0" * (-len(data) % 4))
    return (struct.pack("<4sII", b"glTF", 2, 28 + len(encoded) + len(data)) + struct.pack("<II", len(encoded), 0x4E4F534A)
            + encoded + struct.pack("<II", len(data), 0x004E4942) + data)


class PartAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.triangles = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                                   [[3, 0, 0], [4, 0, 0], [3, 1, 0]],
                                   [[6, 0, 0], [7, 0, 0], [6, 1, 0]]], dtype=np.float32)
        self.uv = self.triangles[:, :, :2] / 8
        self.original = self.write("original", self.triangles, self.uv)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, triangles, uv, translation=None):
        path = self.root / f"{name}.glb"
        path.write_bytes(glb(triangles, uv, translation))
        return path

    def compare(self, candidate):
        _, a = inventory(self.original, self.root / "cache")
        _, b = inventory(candidate, self.root / "cache")
        return {key: compare_hashes(a[key], b[key], self.root, key)[0] for key in a}

    def test_reordered_faces_and_cyclic_corners_preserve_all_channels(self):
        candidate = self.write("reordered", self.triangles[::-1, [1, 2, 0]], self.uv[::-1, [1, 2, 0]])
        results = self.compare(candidate)
        self.assertTrue(all(row["exact_multiset_equal"] for row in results.values()))
        self.assertFalse(results["positions"]["same_face_order"])
        report = bounded_correspondence(self.original, candidate, self.root)
        self.assertTrue(report["complete_bijection"])
        self.assertEqual(report["maximum_corner_error_world"], 0)

    def test_uv_change_does_not_pass_attribute_preservation(self):
        uv = self.uv.copy()
        uv[0, 0, 0] += .1
        results = self.compare(self.write("uv", self.triangles, uv))
        self.assertTrue(results["positions"]["exact_multiset_equal"])
        self.assertFalse(results["uv"]["exact_multiset_equal"])
        self.assertFalse(results["attributes"]["exact_multiset_equal"])

    def test_reversed_winding_is_distinguished(self):
        results = self.compare(self.write("reversed", self.triangles[:, [0, 2, 1]], self.uv[:, [0, 2, 1]]))
        self.assertTrue(results["positions"]["exact_multiset_equal"])
        self.assertFalse(results["winding"]["exact_multiset_equal"])

    def test_same_centroid_changed_corners_fail_bounded_match(self):
        changed = self.triangles.copy()
        changed[0, 0, 2] += .1
        changed[0, 1, 2] -= .1
        report = bounded_correspondence(self.original, self.write("same-center", changed, self.uv), self.root)
        self.assertFalse(report["complete_bijection"])
        self.assertEqual(report["unmatched_candidate_faces"], 1)

    def test_repeated_candidate_cannot_pass_bijection(self):
        candidate = self.write("repeat", self.triangles[[0, 0, 2]], self.uv[[0, 0, 2]])
        report = bounded_correspondence(self.original, candidate, self.root)
        self.assertFalse(report["complete_bijection"])
        self.assertEqual(report["repeated_original_faces"], 1)
        self.assertFalse(self.compare(candidate)["positions"]["exact_multiset_equal"])

    def test_rounding_match_is_reported_separately_from_exact(self):
        translation = np.array([.1234, -.314, .01], dtype=np.float32)
        local = (self.triangles.astype(np.float64) - translation).astype(np.float32)
        candidate = self.write("recenter", local, self.uv, translation.tolist())
        self.assertFalse(self.compare(candidate)["positions"]["exact_multiset_equal"])
        report = recentered_correspondence(self.original, candidate, self.root)
        self.assertTrue(report["complete_bijection"])
        self.assertGreater(report["maximum_corner_error_world"], 0)

    def test_known_front_pixel_hits_expected_triangle_and_depth(self):
        path = self.root / "render.json"
        path.write_text(json.dumps({"renderer": {"width": 100, "height": 100}, "cases": [{"id": "probe",
            "orthographic_vertical_span": 2, "normalization": {"rotation_degrees": [0, 0, 0], "translation": [0, 0, 1], "uniform_scale": 1}}]}))
        result = inspect_rays(self.original, path, "probe", [[60, 40]])
        hits = result["rays"][0]["hits"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["part_face_index"], 0)
        self.assertEqual(hits[0]["normalized_depth_z"], 1)
        np.testing.assert_allclose(hits[0]["world_xyz"], [.21, .19, 0])


if __name__ == "__main__":
    unittest.main()
