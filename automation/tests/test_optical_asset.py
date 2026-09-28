"""Actual GLB preservation, instance isolation, float32 and optical identity."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np

from reconstruction.deform_glb import _read, _float_accessor
from reconstruction.lens_appearance import DensityKeyframe, LensAppearance, ReflectanceKeyframe
from reconstruction.lens_asset import EXTENSION, _pack_glb
from reconstruction.mesh import load_glb
from reconstruction.optical_asset import write_optical_candidate
from test_deform_glb import fixture


def descriptor(clear=False):
    return LensAppearance((DensityKeyframe(0., (0., 0., 0.)), DensityKeyframe(1., (.3, .7, 1.2))),
        normal_reflectance_rgb=(.2, .4, .8)) if not clear else LensAppearance((DensityKeyframe(0., (0., 0., 0.)),), normal_reflectance_rgb=(0., 0., 0.))


def surface(name='sheet', clear=False):
    return {'id': name, 'positions': np.array([[.1, .2, .3], [2.1, .2, .3], [.1, 3.2, .3]]),
            'normals': np.array([[0., 0., 1.]] * 3), 'uv': np.array([[0., 0.], [1., 0.], [0., 1.]]),
            'indices': np.array([[0, 1, 2]]), 'appearance': descriptor(clear)}


class OpticalAssetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source, self.output = self.folder / 'source.glb', self.folder / 'candidate.glb'
        fixture(self.source)

    def write(self, replacements=None, **kwargs):
        return write_optical_candidate(self.source, self.output,
            replacements or [{'part_index': 0, 'surfaces': [surface()]}],
            source_sha256=hashlib.sha256(self.source.read_bytes()).hexdigest(), provenance={'method': 'test_supplied_surface'}, **kwargs)

    def test_original_bytes_shared_mesh_instance_and_embedded_assets_unchanged(self):
        raw, original, old_binary = _read(self.source)
        source_mesh = load_glb(self.source)
        report = self.write()
        _, result, binary = _read(self.output)
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertEqual(binary[:len(old_binary)], old_binary)
        self.assertEqual(result['nodes'][:len(original['nodes'])], original['nodes'])
        self.assertEqual(result['meshes'][:len(original['meshes'])], original['meshes'])
        self.assertEqual(result['materials'][:len(original['materials'])], original['materials'])
        for field in ('textures', 'images'):
            self.assertEqual(result[field], original[field])
        self.assertEqual(result['extras']['keep'], original['extras']['keep'])
        loaded = load_glb(self.output)
        self.assertEqual(len(loaded.parts), 2)
        np.testing.assert_array_equal(loaded.vertices[:3], source_mesh.vertices[3:])
        np.testing.assert_allclose(loaded.vertices[3:], surface()['positions'], atol=1e-7)
        self.assertTrue(report['source_binary_prefix_preserved'])
        self.assertFalse(report['accepted'])

    def test_other_scene_references_remain_unchanged(self):
        fixture(self.source, lambda doc: doc['scenes'].append({'nodes': [0]}))
        _, original, _ = _read(self.source)
        self.write()
        _, result, binary = _read(self.output)
        self.assertEqual(result['scenes'][1], original['scenes'][1])
        self.assertEqual(result['nodes'][:3], original['nodes'])
        result['scene'] = 1
        other = self.folder / 'other.glb'
        other.write_bytes(_pack_glb(result, binary))
        np.testing.assert_array_equal(load_glb(other).vertices, load_glb(self.source).vertices)

    def test_external_image_uri_refused_before_relocating_or_writing(self):
        for uri in ('texture.png', '../texture.png', 'https://assets.example/texture.png', None):
            with self.subTest(uri=uri):
                fixture(self.source, lambda doc: doc['images'].__setitem__(0, {'uri': uri}))
                original = self.source.read_bytes()
                with self.assertRaisesRegex(ValueError, 'External image resources'):
                    self.write()
                self.assertFalse(self.output.exists())
                self.assertEqual(self.source.read_bytes(), original)

    def test_embedded_data_uri_is_preserved_exactly(self):
        uri = 'data:image/png;base64,cGlubmVkIGVtYmVkZGVkIGltYWdl'
        fixture(self.source, lambda doc: doc['images'].__setitem__(0, {'uri': uri}))
        self.write()
        _, result, _ = _read(self.output)
        self.assertEqual(result['images'], [{'uri': uri}])

    def test_unreplaced_primitive_and_nested_child_survive(self):
        def mutate(doc):
            doc['meshes'][0]['primitives'].append(deepcopy(doc['meshes'][0]['primitives'][0]))
            doc['nodes'][1]['children'] = [3]
            doc['nodes'].append({'mesh': 0, 'translation': [0, 0, -1], 'extras': {'keep': 'nested frame'}})
        fixture(self.source, mutate)
        before = load_glb(self.source)
        self.write()
        after = load_glb(self.output)
        # Exactly one primitive was replaced, all others retain original geometry.
        self.assertEqual(len(after.parts), len(before.parts))
        np.testing.assert_array_equal(after.vertices[:-3], before.vertices[3:])
        self.assertTrue(any(part['node_index'] != before.parts[i]['node_index'] for i, part in enumerate(after.parts[:-1])))

    def test_clear_zero_alpha_fallback_keeps_canonical_geometry_and_exact_descriptor(self):
        report = self.write([{'part_index': 0, 'surfaces': [surface(clear=True)]}])
        _, doc, binary = _read(self.output)
        binding = report['surfaces'][0]
        material = doc['materials'][binding['material_index']]
        self.assertEqual(material['pbrMetallicRoughness']['baseColorFactor'][3], 0.)
        self.assertEqual(material['extensions'][EXTENSION]['appearance'], descriptor(True).to_dict())
        self.assertEqual(sum(p['has_lens_appearance_extension'] for p in load_glb(self.output).parts), 1)
        node = doc['nodes'][binding['node_index']]
        self.assertFalse(any(key in node for key in ('matrix', 'translation', 'rotation', 'scale')))
        self.assertEqual(node['extras']['lensSurfaceProfile'], 'front_sheet_v1')
        self.assertEqual(node['extras']['materialIdentification'], 'unmeasured')
        attrs = doc['meshes'][node['mesh']]['primitives'][0]['attributes']
        np.testing.assert_array_equal(_float_accessor(doc, binary, attrs['TEXCOORD_0'], 2), surface()['uv'])

    def test_invalid_winding_coordinates_indices_and_normals_refused_before_write(self):
        for mutate, message in ((lambda s: s.update(indices=np.array([[0, 2, 1]])), 'triangles'),
                                (lambda s: s.update(normals=np.array([[0., 0., -1.]]*3)), 'normals'),
                                (lambda s: s['uv'].__setitem__((2, 1), -1.), 'height'),
                                (lambda s: s['positions'].__setitem__((0, 0), np.nan), 'positions'),
                                (lambda s: s.update(indices=np.array([[0, 1, 99]])), 'indices')):
            with self.subTest(message=message):
                candidate = surface()
                mutate(candidate)
                with self.assertRaisesRegex(ValueError, message):
                    self.write([{'part_index': 0, 'surfaces': [candidate]}])
                self.assertFalse(self.output.exists())

    def test_source_binding_destination_and_duplicate_id_protected(self):
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            write_optical_candidate(self.source, self.output, [], source_sha256='0'*64, provenance={'method': 'test'})
        with self.assertRaisesRegex(ValueError, 'surface ids'):
            self.write([{'part_index': 0, 'surfaces': [surface()]}, {'part_index': 1, 'surfaces': [surface()]}])
        self.write()
        original = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, 'new destination'):
            self.write()
        self.assertEqual(self.output.read_bytes(), original)

    def test_reflected_or_grazing_material_is_not_reduced_to_fallback_color(self):
        item = surface()
        appearance = LensAppearance((DensityKeyframe(0., (1., 2., 3.)),), (1., 1., 1.), angular_reflectance_keyframes=(
            ReflectanceKeyframe(0., (1., 1., 1.)), ReflectanceKeyframe(45., (.2, .9, .5)), ReflectanceKeyframe(90., (1., 1., 1.))))
        item['appearance'] = appearance
        report = self.write([{'part_index': 0, 'surfaces': [item]}])
        self.assertEqual(report['surfaces'][0]['appearance'], appearance.to_dict())
        _, doc, _ = _read(self.output)
        actual = LensAppearance.from_dict(doc['materials'][-1]['extensions'][EXTENSION]['appearance'])
        np.testing.assert_array_equal(actual.evaluate([0, .5, 1], [0, 45, 80]).reflectance_rgb,
                                      appearance.evaluate([0, .5, 1], [0, 45, 80]).reflectance_rgb)


if __name__ == '__main__':
    unittest.main()
