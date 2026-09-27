import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from reconstruction.camera import Camera, fit_camera, project, render_mask
from reconstruction.mesh import TriangleMesh, load_glb


def boxes_mesh(boxes):
    vertices, faces = [], []
    triangles = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5],
                          [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                          [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    for lo, hi in boxes:
        points = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        faces.append(triangles + len(vertices)*8)
        vertices.append(points)
    return TriangleMesh(np.vstack(vertices).astype(float), np.vstack(faces), [])


def make_glb(path, mutate=None):
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype="<f4").tobytes()
    indices = np.array([0, 1, 2], dtype="<u2").tobytes()
    binary = points + indices
    document = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": len(binary)}],
                "bufferViews": [{"buffer": 0, "byteLength": len(points)},
                                {"buffer": 0, "byteOffset": len(points), "byteLength": len(indices)}],
                "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                              {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"}],
                "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
                "nodes": [{"translation": [2, 3, 4], "children": [1]}, {"mesh": 0, "scale": [2, 2, 2]}],
                "scenes": [{"nodes": [0]}], "scene": 0}
    if mutate:
        mutate(document)
    payload = json.dumps(document).encode()
    payload += b" " * (-len(payload) % 4)
    binary += b"\0" * (-len(binary) % 4)
    length = 12 + 8 + len(payload) + 8 + len(binary)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, length)
                     + struct.pack("<II", len(payload), 0x4E4F534A) + payload
                     + struct.pack("<II", len(binary), 0x004E4942) + binary)


class MeshTests(unittest.TestCase):
    def test_load_applies_scene_hierarchy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"triangle.glb"
            make_glb(path)
            mesh = load_glb(path)
            np.testing.assert_allclose(mesh.vertices, [[2, 3, 4], [4, 3, 4], [2, 5, 4]])
            np.testing.assert_array_equal(mesh.faces, [[0, 1, 2]])

    def test_rejects_invalid_accessor_and_cycles(self):
        mutations = [lambda doc: doc["accessors"][1].update(byteOffset=-2),
                     lambda doc: doc["accessors"][0].update(count=999),
                     lambda doc: doc["nodes"][1].update(children=[0]),
                     lambda doc: doc.update(animations=[{}])]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"bad.glb"
            for mutation in mutations:
                make_glb(path, mutation)
                with self.assertRaises(ValueError):
                    load_glb(path)


class CameraTests(unittest.TestCase):
    def test_front_projection_axes_and_roll(self):
        points = np.array([[1, 0, 0], [0, 1, 0]], float)
        np.testing.assert_allclose(project(points, Camera(0, 0, 0, 0, 10, 30, 40)), [[40, 40], [30, 30]])
        np.testing.assert_allclose(project(points, Camera(0, 0, 90, 0, 10, 30, 40)), [[30, 30], [20, 40]])

    def test_overlapping_triangles_are_union_and_holes_stay_open(self):
        ring = boxes_mesh([([-.5, -.3, 0], [-.4, .3, .03]), ([.4, -.3, 0], [.5, .3, .03]),
                           ([-.5, .2, 0], [.5, .3, .03]), ([-.5, -.3, 0], [.5, -.2, .03])])
        mask = render_mask(ring, Camera(0, 0, 0, 0, 80, 48, 48), (96, 96))
        self.assertFalse(mask[48, 48])
        self.assertTrue(mask[25, 10])
        duplicate = TriangleMesh(ring.vertices, np.vstack([ring.faces, ring.faces]), [])
        np.testing.assert_array_equal(mask, render_mask(duplicate, Camera(0, 0, 0, 0, 80, 48, 48), (96, 96)))

    def test_fitter_improves_known_camera_without_changing_mesh(self):
        mesh = boxes_mesh([([-.5, -.18, 0], [-.08, .18, .04]), ([.08, -.18, 0], [.5, .18, .04]),
                           ([-.12, .05, 0], [.12, .1, .04]), ([-.5, .08, -.65], [-.47, .14, .02])]).normalized()
        original = mesh.vertices.copy()
        target = render_mask(mesh, Camera(18, 13, 8, .2, 115, 64, 61), (128, 128))
        result = fit_camera(mesh, target, max_evaluations=260)
        self.assertLess(result["final_loss"], result["initial_loss"]*.65)
        self.assertLessEqual(result["evaluations"], 260)
        self.assertFalse(result["independent_validation"])
        np.testing.assert_array_equal(original, mesh.vertices)

    def test_empty_evidence_and_behind_camera_are_rejected(self):
        with self.assertRaises(ValueError):
            fit_camera(boxes_mesh([([0, 0, 0], [1, 1, 1])]), np.zeros((30, 30), bool))
        with self.assertRaises(ValueError):
            project(np.array([[0, 0, 2.0]]), Camera(0, 0, 0, 1, 1, 0, 0))

    def test_unknown_view_uses_full_yaw_seed_set_without_claiming_identification(self):
        mesh = boxes_mesh([([-.5, -.18, 0], [-.08, .18, .04]), ([.08, -.18, 0], [.5, .18, .04]),
                           ([-.5, .08, -.65], [-.47, .14, .02])]).normalized()
        target = render_mask(mesh, Camera(90, 0, 0, 0, 90, 60, 60), (128, 128))
        result = fit_camera(mesh, target, view='unknown', max_evaluations=20)
        self.assertEqual({row['camera']['yaw'] for row in result['seed_hypotheses']},
                         {0, 45, -45, 90, -90, 135, -135, 180})
        self.assertEqual(result['view_label_prior'], 'unknown')
        self.assertFalse(result['independent_validation'])
        self.assertLessEqual(result['evaluations'], 20)
        self.assertLessEqual(result['final_loss'], result['initial_loss'])


if __name__ == "__main__":
    unittest.main()
