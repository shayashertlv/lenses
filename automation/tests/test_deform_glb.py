import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from reconstruction.deform_glb import deform_glb, _read, _float_accessor
from reconstruction.mesh import load_glb, _node_matrix


def fixture(path, mutate=None, points=None):
    # Two instances share every source accessor, with non-uniform transforms.
    doc = {"asset": {"version": "2.0"}, "buffers": [{"byteLength": 0}], "bufferViews": [], "accessors": [],
           "materials": [{"name": "lens", "extras": {"keep": "material metadata"}, "normalTexture": {"index": 0}}],
           "images": [], "textures": [{"source": 0}],
           "nodes": [{"translation": [0.1, 0.2, 0.3], "children": [1, 2]},
                     {"mesh": 0, "scale": [2, 3, 4], "extras": {"partRole": "lens"}},
                     {"mesh": 0, "translation": [3, 0, 0], "rotation": [0, 0, 2**-.5, 2**-.5]}],
           "scenes": [{"nodes": [0]}], "scene": 0, "extras": {"keep": [1, 2, 3]}}
    binary = bytearray()

    def add(values, type_name, component=5126):
        values = np.asarray(values, dtype="<f4" if component == 5126 else "<u2")
        binary.extend(b"\0" * (-len(binary) % 4))
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": values.nbytes})
        binary.extend(values.tobytes())
        doc["accessors"].append({"bufferView": len(doc["bufferViews"])-1, "componentType": component, "count": len(values), "type": type_name})
        return len(doc["accessors"])-1

    p = add(points if points is not None else [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "VEC3")
    n = add([[0, 0, 1]]*3, "VEC3")
    t = add([[1, 0, 0, -1]]*3, "VEC4")
    uv = add([[0, 0], [1, 0], [0, 1]], "VEC2")
    idx = add([0, 1, 2], "SCALAR", 5123)
    doc["meshes"] = [{"name": "shared", "primitives": [{"attributes": {"POSITION": p, "NORMAL": n, "TANGENT": t, "TEXCOORD_0": uv}, "indices": idx, "material": 0}]}]
    image_bytes = b"test texture bytes preserved exactly"
    doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": len(image_bytes)})
    doc["images"].append({"bufferView": len(doc["bufferViews"])-1, "mimeType": "image/png"})
    binary.extend(image_bytes)
    doc["buffers"][0]["byteLength"] = len(binary)
    if mutate:
        mutate(doc)
    payload = json.dumps(doc).encode()
    payload += b" "*(-len(payload)%4)
    binary.extend(b"\0"*(-len(binary)%4))
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 28+len(payload)+len(binary))
                     + struct.pack("<II", len(payload), 0x4E4F534A)+payload
                     + struct.pack("<II", len(binary), 0x004E4942)+binary)


def nonlinear(points):
    moved = points.copy()
    moved[:, 2] += .07*points[:, 0]**2 + .11*points[:, 1]
    jacobian = np.broadcast_to(np.eye(3), (len(points), 3, 3)).copy()
    jacobian[:, 2, 0] = .14*points[:, 0]
    jacobian[:, 2, 1] = .11
    return moved, jacobian


class DeformGlbTests(unittest.TestCase):
    def test_world_deformation_preserves_shared_instances_and_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"source.glb", Path(folder)/"candidate.glb"
            fixture(source)
            raw, original, old_bin = _read(source)
            before = load_glb(source)
            report = deform_glb(source, output, nonlinear)
            _, result, binary = _read(output)
            after = load_glb(output)
            np.testing.assert_allclose(after.vertices, nonlinear(before.vertices)[0], atol=2e-7)
            np.testing.assert_array_equal(before.faces, after.faces)
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(binary[:len(old_bin)], old_bin)
            for field in ("materials", "textures", "images", "extras", "scenes"):
                self.assertEqual(original[field], result[field])
            self.assertEqual(result["meshes"][0], original["meshes"][0])
            self.assertNotEqual(result["nodes"][1]["mesh"], result["nodes"][2]["mesh"])
            new_positions = []
            for node_index in (1, 2):
                old_node, node = original["nodes"][node_index], result["nodes"][node_index]
                self.assertEqual({k:v for k,v in node.items() if k != "mesh"}, {k:v for k,v in old_node.items() if k != "mesh"})
                primitive = result["meshes"][node["mesh"]]["primitives"][0]
                old_primitive = original["meshes"][0]["primitives"][0]
                for key in ("indices", "material"):
                    self.assertEqual(primitive[key], old_primitive[key])
                self.assertEqual(primitive["attributes"]["TEXCOORD_0"], old_primitive["attributes"]["TEXCOORD_0"])
                new_positions.append(primitive["attributes"]["POSITION"])
            self.assertEqual(len(set(new_positions)), 2)
            self.assertEqual(report["new_mesh_instances"], 2)
            self.assertTrue(report["original_binary_prefix_preserved"])
            self.assertEqual(report["source_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(report["discrete_geometry"]["triangles_checked"], 2)
            self.assertEqual(report["discrete_geometry"]["midpoint_samples"], 6)
            self.assertGreater(report["discrete_geometry"]["max_midpoint_discrepancy_world_units"], .01)
            self.assertFalse(report["discrete_geometry"]["global_self_intersections_checked"])

    def test_normal_and_tangent_match_independent_surface_derivatives(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"source.glb", Path(folder)/"candidate.glb"
            fixture(source)
            _, original, old_bin = _read(source)
            deform_glb(source, output, nonlinear)
            _, result, binary = _read(output)
            for index in (1, 2):
                world = _node_matrix(original["nodes"][0]) @ _node_matrix(original["nodes"][index])
                linear = world[:3, :3]
                pos = _float_accessor(original, old_bin, 0, 3)
                wp = pos @ linear.T + world[:3, 3]
                eps = 1e-5
                # Finite-difference transformed original surface, independent of returned J.
                dx = (nonlinear(wp + eps*linear[:, 0])[0] - nonlinear(wp - eps*linear[:, 0])[0])/(2*eps)
                dy = (nonlinear(wp + eps*linear[:, 1])[0] - nonlinear(wp - eps*linear[:, 1])[0])/(2*eps)
                expected_world_normal = np.cross(dx, dy)
                expected_world_normal /= np.linalg.norm(expected_world_normal, axis=1)[:, None]
                attrs = result["meshes"][result["nodes"][index]["mesh"]]["primitives"][0]["attributes"]
                nl = _float_accessor(result, binary, attrs["NORMAL"], 3)
                nw = nl @ np.linalg.inv(linear)
                nw /= np.linalg.norm(nw, axis=1)[:, None]
                np.testing.assert_allclose(nw, expected_world_normal, atol=1e-7)
                tangent = _float_accessor(result, binary, attrs["TANGENT"], 4)
                tw = tangent[:, :3] @ linear.T
                tw /= np.linalg.norm(tw, axis=1)[:, None]
                dx /= np.linalg.norm(dx, axis=1)[:, None]
                np.testing.assert_allclose(tw, dx, atol=1e-7)
                np.testing.assert_array_equal(tangent[:, 3], [-1, -1, -1])

    def test_refuses_bad_fields_without_writing(self):
        fields = [lambda p: (p, np.zeros((len(p), 3, 3))),
                  lambda p: (p, np.broadcast_to(np.diag([-1., 1, 1]), (len(p), 3, 3))),
                  lambda p: (p*np.nan, np.broadcast_to(np.eye(3), (len(p), 3, 3))),
                  lambda p: (p, np.eye(3))]
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"source.glb", Path(folder)/"candidate.glb"
            fixture(source)
            for field in fields:
                with self.assertRaises(ValueError):
                    deform_glb(source, output, field)
                self.assertFalse(output.exists())
            with self.assertRaises(ValueError):
                deform_glb(source, source, nonlinear)

    def test_refuses_unsupported_or_invalid_geometry(self):
        mutations = [lambda d: d.update(animations=[{}]),
                     lambda d: d["meshes"][0]["primitives"][0].update(targets=[{}]),
                     lambda d: d["accessors"][0].update(count=99999),
                     lambda d: d["accessors"][0].update(sparse={}),
                     lambda d: d["nodes"][1].update(children=[0]),
                     lambda d: d["nodes"][2].update(children=[1]),
                     lambda d: d["meshes"][0]["primitives"][0].update(extensions={"KHR_draco_mesh_compression": {}})]
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"source.glb", Path(folder)/"candidate.glb"
            for mutation in mutations:
                fixture(source, mutation)
                with self.assertRaises(ValueError):
                    deform_glb(source, output, nonlinear)
                self.assertFalse(output.exists())

    def test_positive_jacobians_do_not_excuse_inverted_or_collapsed_chord_triangle(self):
        def one_node(doc):
            doc["nodes"] = [{"mesh": 0}]

        def field(amplitude):
            def deform(points):
                moved = points.copy()
                moved[:, 1] -= amplitude*(1-np.abs(points[:, 0]))
                jacobian = np.broadcast_to(np.eye(3), (len(points), 3, 3)).copy()
                jacobian[:, 1, 0] = amplitude*np.sign(points[:, 0])
                return moved, jacobian
            return deform

        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/"source.glb", Path(folder)/"candidate.glb"
            fixture(source, one_node, [[-1, 0, 0], [1, 0, 0], [0, .01, 0]])
            old = load_glb(source).vertices
            _, jacobian = field(.1)(old)
            np.testing.assert_allclose(np.linalg.det(jacobian), 1)
            self.assertLessEqual(np.linalg.norm(jacobian-np.eye(3), axis=(1, 2)).max(), .1)
            with self.assertRaisesRegex(ValueError, "0 collapsed, 1 inverted"):
                deform_glb(source, output, field(.1))
            self.assertFalse(output.exists())
            # The float32 source height is the exact collapse amplitude.
            with self.assertRaisesRegex(ValueError, "1 collapsed, 0 inverted"):
                deform_glb(source, output, field(float(old[2, 1])))
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
