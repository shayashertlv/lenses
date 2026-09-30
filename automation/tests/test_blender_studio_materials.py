"""Material saves are narrow, portable, validated, and preserve the original bytes."""
from copy import deepcopy
import json
from pathlib import Path
import struct
import tempfile
import unittest

from blender_agent import studio_materials as M


def fixture(document=None):
    doc = document or {
        'asset': {'version': '2.0'}, 'buffers': [{'byteLength': 4}],
        'nodes': [{'mesh': 0, 'extras': {'partRole': 'lens'}}, {'mesh': 1, 'extras': {'partRole': 'frame'}}],
        'meshes': [{'primitives': [{'material': 0, 'attributes': {'POSITION': 0}}]},
                   {'primitives': [{'material': 1, 'attributes': {'POSITION': 0}}]}],
        'materials': [
            {'name': 'Shield', 'pbrMetallicRoughness': {'baseColorFactor': [.45, .45, .45, .7], 'metallicFactor': .88, 'roughnessFactor': .08},
             'extensions': {'KHR_materials_transmission': {'transmissionFactor': .95}, 'KHR_materials_ior': {'ior': 1.58},
                            'KHR_materials_iridescence': {'iridescenceFactor': 1, 'iridescenceThicknessMinimum': 100, 'iridescenceThicknessMaximum': 650},
                            'KHR_materials_volume': {'thicknessFactor': .00115}}},
            {'name': 'Frame', 'pbrMetallicRoughness': {'baseColorFactor': [.01,.01,.01,1]}}],
        'extensionsUsed': ['KHR_materials_transmission', 'KHR_materials_ior', 'KHR_materials_iridescence', 'KHR_materials_volume']}
    payload = json.dumps(doc).encode(); payload += b' ' * (-len(payload) % 4)
    tail = struct.pack('<II', 4, 0x004E4942) + b'\x00\x01\x02\x03'
    return struct.pack('<III', 0x46546C67, 2, 20 + len(payload) + len(tail)) + struct.pack('<II', len(payload), M.JSON_CHUNK) + payload + tail


class StudioMaterialsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.source = self.root / 'original.glb'
        self.raw = fixture(); self.source.write_bytes(self.raw)

    def save(self, edits, name='edited.glb'):
        target = self.root / name
        receipt = M.write_material_revision(self.source, target, edits, expected_sha256=M.sha256(self.raw))
        return target, receipt

    def test_lens_colour_changes_only_factor_preserving_alpha_and_every_binary_byte(self):
        target, receipt = self.save({'0': {'base_color': '#87c9b0'}})
        _, before, tail = M.read_glb(self.source); _, after, new_tail = M.read_glb(target)
        expected = deepcopy(before)
        expected['materials'][0]['pbrMetallicRoughness']['baseColorFactor'] = M.hex_to_linear('#87c9b0') + [.7]
        self.assertEqual(after, expected); self.assertEqual(tail, new_tail)
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertTrue(receipt['geometry_unchanged'])
        self.assertEqual(M.describe_model(target)['materials'][0]['properties']['base_color'], '#87c9b0')

    def test_existing_shader_features_only_and_fixed_transmission_class(self):
        doc = M.describe_model(self.source)
        self.assertEqual(doc['materials'][0]['roles'], ['lens'])
        self.assertIn('transmission', doc['materials'][0]['editable_keys'])
        self.assertNotIn('transmission', doc['materials'][1]['editable_keys'])
        for edits in ({'1': {'transmission': .8}}, {'0': {'transmission': 0}}, {'0': {'emission': 1}}):
            with self.subTest(edits=edits), self.assertRaises(ValueError): self.save(edits)
            self.assertFalse((self.root / 'edited.glb').exists())

    def test_viewer_only_revision_retains_exact_glb_identity(self):
        target, receipt = self.save({})
        self.assertEqual(target.read_bytes(), self.raw)
        self.assertEqual(receipt['source_sha256'], receipt['model_sha256'])
        self.assertEqual(M.validate_viewer({'lens_reflection': 3}), {'lens_reflection': 3})

    def test_replaying_displayed_defaults_preserves_exact_floats_and_bytes(self):
        properties = M.describe_model(self.source)['materials']
        target, receipt = self.save({item['id']: item['properties'] for item in properties})
        self.assertEqual(target.read_bytes(), self.raw)
        self.assertEqual(receipt['changed_fields'], [])
        self.assertTrue(receipt['source_blend_matches_revision'])

    def test_invalid_and_nonfinite_values_fail_before_writing(self):
        for values in ({'roughness': float('nan')}, {'metallic': True}, {'ior': 100}, {'base_color': 'red'}, {'base_color': '#ffffff00'}):
            with self.subTest(values=values), self.assertRaises(ValueError): self.save({'0': values})
        for edits in ({'../../escape': {'roughness': .5}}, {'-1': {}}, {'20': {}}, {'00': {}}, {'0': []}):
            with self.subTest(edits=edits), self.assertRaises(ValueError): self.save(edits)
        self.assertFalse((self.root / 'edited.glb').exists())
        for viewer in ({'lens_reflection': 0}, {'lens_reflection': float('inf')}, {'lens_reflection': True}, {'url': 'https://example.com'}):
            with self.subTest(viewer=viewer), self.assertRaises(ValueError): M.validate_viewer(viewer)

    def test_stale_hash_and_existing_destination_are_not_overwritten(self):
        with self.assertRaises(ValueError):
            M.write_material_revision(self.source, self.root/'stale.glb', {}, expected_sha256='0'*64)
        target, _ = self.save({'0': {'roughness': .2}})
        old = target.read_bytes()
        with self.assertRaises(FileExistsError): self.save({'0': {'roughness': .7}})
        self.assertEqual(target.read_bytes(), old)

    def test_unlimited_absorption_omits_distance_and_thin_film_interval_stays_valid(self):
        target, _ = self.save({'0': {'attenuation_distance': 0, 'iridescence_thickness': 50}})
        _, doc, _ = M.read_glb(target)
        ext = doc['materials'][0]['extensions']
        self.assertNotIn('attenuationDistance', ext['KHR_materials_volume'])
        self.assertEqual(ext['KHR_materials_iridescence']['iridescenceThicknessMinimum'], 50)
        self.assertEqual(ext['KHR_materials_iridescence']['iridescenceThicknessMaximum'], 50)

    def test_external_resources_and_malformed_glb_are_rejected(self):
        _, doc, _ = M.read_glb(self.source)
        doc['images'] = [{'uri': 'http://unrelated.example/picture.png'}]
        self.source.write_bytes(fixture(doc))
        with self.assertRaises(ValueError): M.describe_model(self.source)
        self.source.write_bytes(self.raw[:-1])
        with self.assertRaises(ValueError): M.describe_model(self.source)

    def test_canonical_optics_are_explicitly_uneditable(self):
        _, doc, _ = M.read_glb(self.source)
        doc['extensionsUsed'].append('LENSES_lens_appearance')
        self.raw=fixture(doc); self.source.write_bytes(self.raw)
        self.assertFalse(M.describe_model(self.source)['materials'][0]['editable'])
        with self.assertRaises(ValueError): self.save({'0': {'base_color': '#ffffff'}})

    def test_unused_materials_and_out_of_editor_range_baselines_are_not_replayed(self):
        _, doc, _ = M.read_glb(self.source)
        doc['materials'].append({'name': 'Unused'})
        doc['materials'][0]['extensions']['KHR_materials_volume']['attenuationDistance'] = 100
        doc['materials'][0]['extensions']['KHR_materials_transmission']['transmissionFactor'] = .00001
        self.raw = fixture(doc); self.source.write_bytes(self.raw)
        materials = M.describe_model(self.source)['materials']
        self.assertFalse(materials[2]['editable'])
        self.assertNotIn('attenuation_distance', materials[0]['editable_keys'])
        self.assertNotIn('transmission', materials[0]['editable_keys'])
        target, _ = self.save({item['id']: item['properties'] for item in materials if item['editable']})
        self.assertEqual(target.read_bytes(), self.raw)


if __name__ == '__main__': unittest.main()
