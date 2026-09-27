"""Real byte-preserving edits, independent transport checks and guard failures."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from reconstruction.deform_glb import _read, _float_accessor
from reconstruction.mesh import load_glb
from reconstruction.segmented_astra_geometry import (inspect_parts, apply_geometry_edit,
                                                       set_frame_material)


def plane(x0=-.02, x1=.02, y0=-.02, y1=.02, z=0., count=7):
    xx, yy = np.meshgrid(np.linspace(x0, x1, count), np.linspace(y0, y1, count))
    points = np.stack((xx.ravel(), yy.ravel(), np.full(xx.size, z)), axis=1)
    faces = []
    for y in range(count-1):
        for x in range(count-1):
            a = y*count+x
            faces.extend([[a, a+1, a+count], [a+1, a+count+1, a+count]])
    return points, np.asarray(faces)


def fixture(path, parts=None, mutate=None):
    parts = parts if parts is not None else [plane(), plane(.045, .065, -.01, .01)]
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': list(range(len(parts)))}],
           'buffers': [{'byteLength': 0}], 'bufferViews': [], 'accessors': [],
           'nodes': [], 'meshes': [], 'extras': {'retained': 'canonical caller metadata'},
           'materials': [{'name': 'shared', 'extras': {'keep': 123},
                          'pbrMetallicRoughness': {'baseColorFactor': [.8, .7, .6, 1],
                              'baseColorTexture': {'index': 0}, 'roughnessFactor': .4, 'metallicFactor': .1}}],
           'textures': [{'source': 0}], 'images': []}
    binary = bytearray()

    def add(values, kind, dtype='<f4', component=5126):
        a = np.asarray(values, dtype=dtype)
        binary.extend(b'\0'*(-len(binary) % 4))
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': a.nbytes})
        binary.extend(a.tobytes())
        doc['accessors'].append({'bufferView': len(doc['bufferViews'])-1, 'componentType': component,
                                 'type': kind, 'count': len(a)})
        return len(doc['accessors'])-1

    for i, (points, faces) in enumerate(parts):
        attrs = {'POSITION': add(points, 'VEC3'),
                 'NORMAL': add(np.tile([0, 0, 1], (len(points), 1)), 'VEC3'),
                 'TANGENT': add(np.tile([1, 0, 0, -1], (len(points), 1)), 'VEC4'),
                 'TEXCOORD_0': add(np.asarray(points)[:, :2]*10+.5, 'VEC2')}
        idx = add(np.asarray(faces).ravel(), 'SCALAR', '<u4', 5125)
        doc['meshes'].append({'name': f'part{i}', 'extras': {'retain': i},
                              'primitives': [{'attributes': attrs, 'indices': idx, 'material': 0}]})
        doc['nodes'].append({'mesh': i, 'name': f'part{i}', 'extras': {'retain': i}})
    image = b'pinned image payload; no image rebake'
    doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(image)})
    doc['images'].append({'bufferView': len(doc['bufferViews'])-1, 'mimeType': 'image/png'})
    binary.extend(image)
    doc['buffers'][0]['byteLength'] = len(binary)
    if mutate:
        mutate(doc)
    payload = json.dumps(doc).encode(); payload += b' '*(-len(payload) % 4)
    binary.extend(b'\0'*(-len(binary) % 4))
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 28+len(payload)+len(binary)) +
                     struct.pack('<II', len(payload), 0x4E4F534A)+payload +
                     struct.pack('<II', len(binary), 0x004E4942)+binary)
    return path


def attrs(path, part_id=0):
    _, doc, binary = _read(path)
    part = load_glb(path).parts[part_id]
    a = doc['meshes'][part['mesh_index']]['primitives'][part['primitive_index']]['attributes']
    return {k: _float_accessor(doc, binary, v, 4 if k == 'TANGENT' else 2 if k == 'TEXCOORD_0' else 3)
            for k, v in a.items()}


class SegmentedAstraGeometryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name); self.source = self.root/'source.glb'; self.output = self.root/'edited.glb'
        self.groups = {'lens': [0]}
        fixture(self.source)

    def test_inspection_and_translation_keep_identity_records_and_unselected_bytes(self):
        before = inspect_parts(self.source, self.groups)
        self.assertEqual([r['part_id'] for r in before['parts']], [0, 1])
        self.assertEqual([r['role'] for r in before['parts']], ['optical', 'opaque_candidate'])
        raw, doc, binary = _read(self.source)
        proof = apply_geometry_edit(self.source, self.output,
            {'operation': 'translate', 'part_ids': [0], 'offset_m': [0, 0, .001]}, groups=self.groups)
        _, after, new_binary = _read(self.output)
        np.testing.assert_allclose(attrs(self.output)['POSITION'], attrs(self.source)['POSITION']+[0, 0, .001], atol=1e-10)
        np.testing.assert_array_equal(attrs(self.output, 1)['POSITION'], attrs(self.source, 1)['POSITION'])
        self.assertEqual(after['nodes'][1], doc['nodes'][1])
        self.assertEqual(after['meshes'][:len(doc['meshes'])], doc['meshes'])
        self.assertEqual(after['materials'], doc['materials'])
        for k in ('textures', 'images', 'scenes', 'extras'):
            self.assertEqual(after[k], doc[k])
        self.assertEqual(new_binary[:len(binary)], binary)
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertEqual(proof['output_sha256'], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual([r['new_part_id'] for r in proof['part_mapping']], [0, 1])
        self.assertFalse(proof['contact_collision']['global_intersections_checked'])
        self.assertFalse(proof['accepted'])

    def test_rotation_transports_normals_and_tangents_independently(self):
        proof = apply_geometry_edit(self.source, self.output,
            {'operation': 'rotate', 'part_ids': [0], 'axis': [0, 1, 0], 'angle_degrees': 5,
             'pivot_m': [0, 0, 0]}, groups=self.groups)
        theta = np.radians(5)
        expected = np.array([[np.cos(theta), 0, np.sin(theta)], [0, 1, 0], [-np.sin(theta), 0, np.cos(theta)]])
        old, new = attrs(self.source), attrs(self.output)
        np.testing.assert_allclose(new['POSITION'], old['POSITION']@expected.T, atol=1e-9)
        np.testing.assert_allclose(new['NORMAL'], old['NORMAL']@expected.T, atol=1e-7)
        np.testing.assert_allclose(new['TANGENT'][:, :3], old['TANGENT'][:, :3]@expected.T, atol=1e-7)
        np.testing.assert_array_equal(new['TANGENT'][:, 3], old['TANGENT'][:, 3])
        self.assertEqual(proof['geometry'][0]['inverted_triangles'], 0)

    def test_local_bend_pins_contacts_and_matches_independent_normal_derivative(self):
        fixture(self.source, [plane(count=9), plane(.02, .04, -.02, .02, count=9)])
        proof = apply_geometry_edit(self.source, self.output,
            {'operation': 'local_bend', 'part_ids': [0], 'center_m': [0, 0, 0],
             'radius_m': .012, 'offset_m': [0, 0, .001]}, groups=self.groups)
        old, new = attrs(self.source), attrs(self.output)
        p = old['POSITION']; r2 = (p[:, 0]**2+p[:, 1]**2)/.012**2
        q = np.maximum(1-r2, 0)
        expected = p.copy(); expected[:, 2] += .001*q**3
        np.testing.assert_allclose(new['POSITION'], expected, atol=1e-10)
        normals = np.stack((.006*q**2*p[:, 0]/.012**2, .006*q**2*p[:, 1]/.012**2, np.ones(len(p))), axis=1)
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        np.testing.assert_allclose(new['NORMAL'], normals, atol=1e-7)
        seam = np.isclose(p[:, 0], .02)
        np.testing.assert_array_equal(new['POSITION'][seam], old['POSITION'][seam])
        self.assertGreater(proof['contact_collision']['contact_samples'], 0)
        self.assertEqual(proof['contact_collision']['maximum_contact_motion_m'], 0)

    def test_bend_refuses_contact_motion_and_overstrong_gradient(self):
        fixture(self.source, [plane(count=9), plane(.02, .04, -.02, .02, count=9)])
        for edit, message in [
            ({'operation': 'local_bend', 'part_ids': [0], 'center_m': [.019, 0, 0],
              'radius_m': .012, 'offset_m': [0, 0, .001]}, 'contact/seam'),
            ({'operation': 'local_bend', 'part_ids': [0], 'center_m': [0, 0, 0],
              'radius_m': .002, 'offset_m': [0, 0, .002]}, 'gradient')]:
            with self.assertRaisesRegex(ValueError, message):
                apply_geometry_edit(self.source, self.output, edit, groups=self.groups)
            self.assertFalse(self.output.exists())

    def test_translation_refuses_separating_a_seam_and_sweeping_through_other_part(self):
        fixture(self.source, [plane(), plane(.02, .04)])
        with self.assertRaisesRegex(ValueError, 'contact/seam'):
            apply_geometry_edit(self.source, self.output,
                {'operation': 'translate', 'part_ids': [0], 'offset_m': [0, 0, .001]}, groups=self.groups)
        self.assertFalse(self.output.exists())
        fixture(self.source, [plane(z=-.001), plane(z=0)])
        with self.assertRaisesRegex(ValueError, 'sweeps'):
            apply_geometry_edit(self.source, self.output,
                {'operation': 'translate', 'part_ids': [0], 'offset_m': [0, 0, .002]}, groups=self.groups)
        self.assertFalse(self.output.exists())

    def test_duplicate_positions_move_identically_across_uv_vertices_and_selected_parts(self):
        p, t = plane()
        exploded = p[t].reshape(-1, 3); faces = np.arange(len(exploded)).reshape(-1, 3)
        fixture(self.source, [(exploded, faces), (p, t)])
        apply_geometry_edit(self.source, self.output,
            {'operation': 'local_bend', 'part_ids': [0, 1], 'center_m': [0, 0, 0],
             'radius_m': .012, 'offset_m': [0, 0, .001]}, groups={'lens': [0, 1]})
        first, second = attrs(self.output, 0)['POSITION'], attrs(self.output, 1)['POSITION']
        np.testing.assert_array_equal(first, second[t].reshape(-1, 3))

    def test_material_clones_binding_preserves_geometry_and_texture_and_rejects_optics(self):
        raw, old, binary = _read(self.source)
        proof = set_frame_material(self.source, self.output, [1], base_color_linear_rgb=[.4, .5, .6],
                                   roughness=.25, metallic=.7, groups=self.groups)
        _, new, new_bin = _read(self.output)
        self.assertEqual(new_bin, binary)
        self.assertEqual(new['materials'][0], old['materials'][0])
        pbr = new['materials'][1]['pbrMetallicRoughness']
        self.assertEqual(pbr['baseColorTexture'], old['materials'][0]['pbrMetallicRoughness']['baseColorTexture'])
        self.assertEqual(pbr['baseColorFactor'], [.4, .5, .6, 1])
        self.assertEqual(pbr['roughnessFactor'], .25)
        for part in (0, 1):
            for key, value in attrs(self.source, part).items():
                np.testing.assert_array_equal(value, attrs(self.output, part)[key])
        self.assertTrue(proof['geometry_unchanged'])
        self.assertEqual(self.source.read_bytes(), raw)
        with self.assertRaisesRegex(ValueError, 'optical'):
            set_frame_material(self.source, self.root/'bad.glb', [0], metallic=.3, groups=self.groups)

    def test_material_and_geometry_edit_do_not_mutate_other_primitive_in_same_node(self):
        def merge(doc):
            doc['meshes'][0]['primitives'].extend(deepcopy(doc['meshes'][1]['primitives']))
            doc['nodes'] = [doc['nodes'][0]]; doc['scenes'][0]['nodes'] = [0]
        fixture(self.source, mutate=merge)
        set_frame_material(self.source, self.output, [1], roughness=.6, groups=self.groups)
        _, old, _ = _read(self.source); _, new, _ = _read(self.output)
        new_mesh = new['meshes'][new['nodes'][0]['mesh']]
        self.assertEqual(new_mesh['primitives'][0], old['meshes'][0]['primitives'][0])
        self.assertNotEqual(new_mesh['primitives'][1]['material'], old['meshes'][0]['primitives'][1]['material'])

    def test_refuses_invalid_parameters_without_writes(self):
        valid = {'operation': 'translate', 'part_ids': [0], 'offset_m': [0, 0, .001]}
        bad = [dict(valid, part_ids=[-1]), dict(valid, part_ids=[True]), dict(valid, part_ids=[0, 0]),
               dict(valid, offset_m=[0, 0, float('nan')]), dict(valid, offset_m=[0, 0, .004]),
               dict(valid, offset_m=['0', '0', '0.001']), dict(valid, extra=1), dict(valid, operation='delete')]
        for edit in bad:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                apply_geometry_edit(self.source, self.output, edit, groups=self.groups)
            self.assertFalse(self.output.exists())
        for parameters in ({}, {'roughness': True}, {'metallic': 1.5}, {'base_color_linear_rgb': [1, 0, -1]}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                set_frame_material(self.source, self.output, [1], groups=self.groups, **parameters)
        with self.assertRaises(ValueError):
            apply_geometry_edit(self.source, self.source, valid, groups=self.groups)
        self.output.write_bytes(b'existing')
        with self.assertRaises(ValueError):
            apply_geometry_edit(self.source, self.output, valid, groups=self.groups)
        self.assertEqual(self.output.read_bytes(), b'existing')

    def test_refuses_noncanonical_and_stale_optical_source(self):
        mutations = [lambda d: d['nodes'][0].update(translation=[1, 0, 0]),
                     lambda d: d.update(extensionsUsed=['LENSES_lens_appearance']),
                     lambda d: d['nodes'][0]['extras'].update(opticalGroupId='lens'),
                     lambda d: d['nodes'][0].update(children=[0])]
        for mutation in mutations:
            fixture(self.source, mutate=mutation)
            with self.assertRaises(ValueError):
                inspect_parts(self.source, self.groups)
        fixture(self.source)
        with self.assertRaises(ValueError):
            inspect_parts(self.source, {'a': [0], 'b': [0]})


if __name__ == '__main__':
    unittest.main()
