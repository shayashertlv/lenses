"""Surface transfer: UVs and the textured material move from the generation onto the split parts, one island per face."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.mesh import load_glb_bytes
from reconstruction.mesh_components import component_face_labels
from reconstruction.surface_transfer import run_surface_transfer_stage, transfer_surface


def pack(doc, binary):
    payload = json.dumps(doc).encode()
    payload += b' ' * (-len(payload) % 4)
    binary = binary + b'\0' * (-len(binary) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 28 + len(payload) + len(binary))
            + struct.pack('<II', len(payload), 0x4E4F534A) + payload + struct.pack('<II', len(binary), 0x004E4942) + binary)


def unpack(raw):
    length = struct.unpack_from('<I', raw, 12)[0]
    return json.loads(raw[20:20 + length]), raw[28 + length:]


def build_generation(positions, uv, faces):
    positions, uv, faces = np.asarray(positions, np.float32), np.asarray(uv, np.float32), np.asarray(faces, np.uint32)
    png = io.BytesIO(); Image.new('RGB', (2, 2), (200, 30, 30)).save(png, format='PNG'); png = png.getvalue()
    parts = [positions.tobytes(), uv.tobytes(), faces.tobytes(), png]
    offsets = np.cumsum([0] + [len(p) for p in parts])
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': int(offsets[-1])}],
           'bufferViews': [{'buffer': 0, 'byteOffset': int(offsets[i]), 'byteLength': len(parts[i])} for i in range(4)],
           'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': len(positions), 'type': 'VEC3'},
                         {'bufferView': 1, 'componentType': 5126, 'count': len(uv), 'type': 'VEC2'},
                         {'bufferView': 2, 'componentType': 5125, 'count': int(faces.size), 'type': 'SCALAR'}],
           'images': [{'mimeType': 'image/png', 'bufferView': 3}], 'samplers': [{'magFilter': 9729}],
           'textures': [{'sampler': 0, 'source': 0}],
           'materials': [{'name': 'bake', 'pbrMetallicRoughness': {'baseColorTexture': {'index': 0, 'texCoord': 0}}, 'doubleSided': True}],
           'meshes': [{'primitives': [{'attributes': {'POSITION': 0, 'TEXCOORD_0': 1}, 'indices': 2, 'material': 0}]}],
           'nodes': [{'mesh': 0}], 'scenes': [{'nodes': [0]}], 'scene': 0}
    return pack(doc, b''.join(parts))


def generation_glb():
    """One quad, one island: UV = (x/2, y)."""
    return build_generation([[0, 0, 0], [2, 0, 0], [2, 1, 0], [0, 1, 0]], [[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 1, 2], [0, 2, 3]])


def strips_glb(count, gap=0.1):
    """``count`` unit quads along x; each is its own island, u in [(i+gap)/count, (i+1-gap)/count], padding between."""
    positions, uv, faces = [], [], []
    for i in range(count):
        base = 4 * i
        positions += [[i, 0, 0], [i + 1, 0, 0], [i + 1, 1, 0], [i, 1, 0]]
        u0, u1 = (i + gap) / count, (i + 1 - gap) / count
        uv += [[u0, 0], [u1, 0], [u1, 1], [u0, 1]]
        faces += [[base, base + 1, base + 2], [base, base + 2, base + 3]]
    return build_generation(positions, uv, faces)


def split_glb(jitter=0.0):
    # Two parts covering the same quad, slightly lifted, with vertex colours and no material.
    left = np.array([[0, 0, jitter], [1, 0, jitter], [1, 1, jitter], [0, 1, jitter]], np.float32)
    right = np.array([[1, 0, -jitter], [2, 0, -jitter], [2, 1, -jitter], [1, 1, -jitter]], np.float32)
    colors = np.tile(np.array([[0.2, 0.9, 0.2, 1.0]], np.float32), (4, 1))
    faces = np.array([[0, 1, 2], [0, 2, 3]], np.uint32)
    binary = left.tobytes() + colors.tobytes() + faces.tobytes() + right.tobytes()
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(binary)}],
           'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 48}, {'buffer': 0, 'byteOffset': 48, 'byteLength': 64},
                           {'buffer': 0, 'byteOffset': 112, 'byteLength': 24}, {'buffer': 0, 'byteOffset': 136, 'byteLength': 48}],
           'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 4, 'type': 'VEC3'},
                         {'bufferView': 1, 'componentType': 5126, 'count': 4, 'type': 'VEC4'},
                         {'bufferView': 2, 'componentType': 5125, 'count': 6, 'type': 'SCALAR'},
                         {'bufferView': 3, 'componentType': 5126, 'count': 4, 'type': 'VEC3'}],
           'meshes': [{'name': 'model_part0', 'primitives': [{'attributes': {'POSITION': 0, 'COLOR_0': 1}, 'indices': 2}]},
                      {'name': 'model_part1', 'primitives': [{'attributes': {'POSITION': 3, 'COLOR_0': 1}, 'indices': 2}]}],
           'nodes': [{'mesh': 0, 'name': 'model_part0'}, {'mesh': 1, 'name': 'model_part1'}], 'scenes': [{'nodes': [0, 1]}], 'scene': 0,
           'extras': {'keep': 1}}
    return pack(doc, binary)


def one_part_glb(positions, faces, normals=None):
    """One split part with positions, optional normals and vertex colours."""
    positions, faces = np.asarray(positions, np.float32), np.asarray(faces, np.uint32)
    colors = np.tile(np.array([[0.2, 0.9, 0.2, 1.0]], np.float32), (len(positions), 1))
    parts = [positions.tobytes(), colors.tobytes(), faces.tobytes()]
    attributes = {'POSITION': 0, 'COLOR_0': 1}
    accessors = [{'bufferView': 0, 'componentType': 5126, 'count': len(positions), 'type': 'VEC3'},
                 {'bufferView': 1, 'componentType': 5126, 'count': len(positions), 'type': 'VEC4'},
                 {'bufferView': 2, 'componentType': 5125, 'count': int(faces.size), 'type': 'SCALAR'}]
    if normals is not None:
        parts.append(np.asarray(normals, np.float32).tobytes())
        accessors.append({'bufferView': 3, 'componentType': 5126, 'count': len(positions), 'type': 'VEC3'})
        attributes['NORMAL'] = 3
    offsets = np.cumsum([0] + [len(p) for p in parts])
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': int(offsets[-1])}],
           'bufferViews': [{'buffer': 0, 'byteOffset': int(offsets[i]), 'byteLength': len(parts[i])} for i in range(len(parts))],
           'accessors': accessors,
           'meshes': [{'name': 'model_part0', 'primitives': [{'attributes': attributes, 'indices': 2}]}],
           'nodes': [{'mesh': 0, 'name': 'model_part0'}], 'scenes': [{'nodes': [0]}], 'scene': 0}
    return pack(doc, b''.join(parts))


def primitive_arrays(raw):
    """(positions, uv, faces, normals) of every primitive of an output GLB, in document order."""
    doc, binary = unpack(raw)
    result = []
    for mesh in doc['meshes']:
        for primitive in mesh['primitives']:
            def read(index):
                acc = doc['accessors'][index]; view = doc['bufferViews'][acc['bufferView']]
                dtype = {5126: '<f4', 5125: '<u4'}[acc['componentType']]
                width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[acc['type']]
                return np.frombuffer(binary[view['byteOffset']:view['byteOffset'] + view['byteLength']], dtype=dtype).reshape(-1, width)
            attrs = primitive['attributes']
            result.append((read(attrs['POSITION']), read(attrs['TEXCOORD_0']), read(primitive['indices']).reshape(-1, 3),
                           read(attrs['NORMAL']) if 'NORMAL' in attrs else None))
    return doc, result


class SurfaceTransferTests(unittest.TestCase):
    def test_generation_vertex_colors_preserve_authored_pattern_instead_of_split_bake(self):
        from reconstruction.surface_transfer import _rows
        doc,binary = unpack(generation_glb())
        colors = np.array([[1,0,0,1],[0,0,1,1],[0,0,1,1],[1,0,0,1]],'<f4')
        binary = bytearray(binary); binary.extend(b'\0'*(-len(binary)%4))
        doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':colors.nbytes}); binary.extend(colors.tobytes())
        doc['accessors'].append({'bufferView':len(doc['bufferViews'])-1,'componentType':5126,'count':4,'type':'VEC4'})
        doc['meshes'][0]['primitives'][0]['attributes']['COLOR_0']=len(doc['accessors'])-1
        doc['buffers'][0]['byteLength']=len(binary)
        out,receipt=transfer_surface(split_glb(),pack(doc,bytes(binary)))
        actual,blob=unpack(out)
        self.assertEqual(receipt['generation_vertex_colors'],'interpolated alongside transferred UVs')
        for mesh in actual['meshes']:
            attrs=mesh['primitives'][0]['attributes']; positions=_rows(actual,blob,attrs['POSITION'])[1]
            color=_rows(actual,blob,attrs['COLOR_0'])[1]
            np.testing.assert_allclose(color[:,0],1-positions[:,0]/2,atol=1e-6)
            np.testing.assert_allclose(color[:,2],positions[:,0]/2,atol=1e-6)
            np.testing.assert_array_equal(color[:,1],np.zeros(len(color)))

    def test_uvs_material_and_images_transfer_and_vertex_colors_drop(self):
        out, receipt = transfer_surface(split_glb(jitter=0.01), generation_glb())
        doc, arrays = primitive_arrays(out)
        self.assertEqual(len(doc['materials']), 1)
        self.assertEqual(doc['materials'][0]['name'], 'bake (transferred)')
        self.assertEqual(len(doc['images']), 1)
        self.assertEqual(len(doc['textures']), 1)
        self.assertEqual(doc['extras']['keep'], 1)
        for mesh in doc['meshes']:
            primitive = mesh['primitives'][0]
            self.assertEqual(set(primitive['attributes']), {'POSITION', 'TEXCOORD_0'})
            self.assertEqual(primitive['material'], 0)
        # The generation quad maps UV = (x/2, y): the left part spans u 0..0.5, the right 0.5..1; one island, nothing splits.
        expected = {0: [[0, 0], [.5, 0], [.5, 1], [0, 1]], 1: [[.5, 0], [1, 0], [1, 1], [.5, 1]]}
        mesh = load_glb_bytes(out)
        self.assertEqual(len(mesh.parts), 2)
        for mi, (positions, uv, faces, _) in enumerate(arrays):
            np.testing.assert_allclose(uv, expected[mi], atol=1e-5)
            self.assertEqual(len(positions), 4)
            np.testing.assert_array_equal(faces, [[0, 1, 2], [0, 2, 3]])
        self.assertEqual((receipt['vertices'], receipt['output_vertices'], receipt['faces']), (8, 8, 4))
        self.assertEqual((receipt['straddling_faces'], receipt['extrapolated_corners'], receipt['uv_islands']), (0, 0, 1))
        self.assertIsNone(receipt['barycentric_excursion'])
        self.assertEqual(receipt['far_vertices'], 0)
        self.assertAlmostEqual(receipt['maximum_distance_units'], .01, places=5)
        self.assertEqual(receipt['copied'], {'images': 1, 'textures': 1, 'materials': 1})
        image_view = doc['bufferViews'][doc['images'][0]['bufferView']]
        _, binary = unpack(out)
        self.assertTrue(binary[image_view['byteOffset']:image_view['byteOffset'] + 8].startswith(b'\x89PNG'))
        self.assertEqual(len(doc['bufferViews']), 1 + 2 * 3, 'only the image and the re-emitted attributes remain in the buffer')
        self.assertFalse(receipt['accepted'])
        json.dumps(receipt, allow_nan=False)

    def test_a_face_across_a_seam_samples_one_island_and_splits_the_vertex(self):
        # Two islands side by side; one split quad whose two faces both cross the seam at x = 1.
        out, receipt = transfer_surface(one_part_glb([[0, 0, 0], [2, 0, 0], [2, 1, 0], [0, 1, 0]], [[0, 1, 2], [0, 2, 3]],
                                                     normals=[[0, 0, 1]] * 4), strips_glb(2))
        _, [(positions, uv, faces, normals)] = primitive_arrays(out)
        self.assertEqual(receipt['uv_islands'], 2)
        self.assertEqual((receipt['vertices'], receipt['output_vertices'], receipt['faces']), (4, 6, 2))
        self.assertEqual((receipt['straddling_faces'], receipt['extrapolated_corners']), (2, 2))
        self.assertGreater(receipt['barycentric_excursion']['maximum'], 0)
        # Face 0 has two corners on the right island: its x=0 corner extrapolates through the right map u = .55 + .4 (x-1).
        # Face 1 has two corners on the left island: its x=2 corner extrapolates through the left map u = .05 + .4 x.
        by_vertex = {tuple(np.round(p, 6)): [] for p in positions}
        for p, t in zip(positions, uv):
            by_vertex[tuple(np.round(p, 6))].append(tuple(np.round(t, 6)))
        self.assertEqual(sorted(by_vertex[(0., 0., 0.)]), [(.05, 0.), (.15, 0.)])
        self.assertEqual(sorted(by_vertex[(2., 1., 0.)]), [(.85, 1.), (.95, 1.)])
        self.assertEqual(by_vertex[(2., 0., 0.)], [(.95, 0.)])
        self.assertEqual(by_vertex[(0., 1., 0.)], [(.05, 1.)])
        for face in faces:
            # One island's affine map scales x by .4 and y by 1: a face that sampled two islands could not keep that ratio.
            p, t = positions[face].astype(float), uv[face].astype(float)
            area3 = .5 * np.linalg.norm(np.cross(p[1] - p[0], p[2] - p[0]))
            area2 = .5 * abs((t[1, 0] - t[0, 0]) * (t[2, 1] - t[0, 1]) - (t[1, 1] - t[0, 1]) * (t[2, 0] - t[0, 0]))
            self.assertAlmostEqual(area2 / area3, .4, places=5, msg='every face samples one island')
        np.testing.assert_array_equal(normals, [[0, 0, 1]] * 6, 'other attributes are gathered along')
        labels = component_face_labels(positions.astype(np.float64), faces)
        self.assertEqual(int(labels.max()), 0, 'position-based connectivity keeps the seam vertices in one component')
        mesh = load_glb_bytes(out)
        self.assertEqual((len(mesh.vertices), len(mesh.faces)), (6, 2))

    def test_three_different_corners_take_the_centroid_island(self):
        out, receipt = transfer_surface(one_part_glb([[.5, .2, 0], [2.5, .2, 0], [1.5, .8, 0]], [[0, 1, 2]]), strips_glb(3))
        _, [(positions, uv, faces, _)] = primitive_arrays(out)
        self.assertEqual((receipt['straddling_faces'], receipt['extrapolated_corners'], receipt['output_vertices']), (1, 2, 3))
        # The centroid at x = 1.5 lies on the middle island: u = (1+.1)/3 + (.8/3)(x-1).
        expected = {(.5, .2): 1.1 / 3 - .8 / 3 * .5, (2.5, .2): 1.1 / 3 + .8 / 3 * 1.5, (1.5, .8): .5}
        for p, t in zip(positions, uv):
            self.assertAlmostEqual(float(t[0]), expected[(round(float(p[0]), 3), round(float(p[1]), 3))], places=5)
            self.assertAlmostEqual(float(t[1]), float(p[1]), places=5)

    def test_far_vertices_are_counted_and_inputs_validated(self):
        out, receipt = transfer_surface(split_glb(jitter=0.5), generation_glb(), far_fraction=.1)
        self.assertEqual(receipt['far_vertices'], 8, 'half a unit off a two-unit quad is far at a tenth')
        with self.assertRaises(ValueError):
            transfer_surface(split_glb(), generation_glb(), far_fraction=1.5)
        doc, binary = unpack(generation_glb())
        del doc['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_0']
        with self.assertRaises(ValueError):
            transfer_surface(split_glb(), pack(doc, binary))

    def test_stage_without_generation_applies_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            split = Path(folder) / 'initial.glb'; split.write_bytes(split_glb())
            generation = Path(folder) / 'generated.glb'; generation.write_bytes(generation_glb())
            none = run_surface_transfer_stage(split, None, Path(folder) / 'none')
            self.assertEqual((none['status'], none['model']), ('not_applicable', None))
            done = run_surface_transfer_stage(split, generation, Path(folder) / 'done')
            self.assertEqual(done['status'], 'transferred')
            written = (Path(folder) / 'done' / 'transferred.glb').read_bytes()
            self.assertEqual(done['model']['sha256'], __import__('hashlib').sha256(written).hexdigest())
            self.assertEqual(done['receipt']['generation_sha256'], __import__('hashlib').sha256(generation.read_bytes()).hexdigest())
            self.assertEqual(split.read_bytes(), split_glb(), 'the split keeps its bytes')


if __name__ == '__main__':
    unittest.main()
