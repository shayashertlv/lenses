"""Real GLB source preservation, explicit group identity and byte-bound reload."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction.deform_glb import _read
from reconstruction.lens_appearance import LensAppearance, DensityKeyframe
from reconstruction.lens_asset import _pack_glb, EXTENSION
from reconstruction.mesh import load_glb
from reconstruction.optical_group_asset import write_optical_group_candidate, read_optical_group_candidate, _hash, _seal
from reconstruction.optical_groups import prepare_optical_group
from reconstruction.optical_group_raster import PROFILE
from test_deform_glb import fixture


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def appearance(clear=False):
    return LensAppearance((DensityKeyframe(0., (0., 0., 0.) if clear else (.2, .4, .8)),),
                          normal_reflectance_rgb=(0., 0., 0.) if clear else (.1, .2, .3))


def groups_for(path, memberships, clear=False, binding_updates=None):
    mesh = load_glb(path); source_sha = sha(path); result = []
    for gid, indices in memberships:
        members = []
        for index in indices:
            part = mesh.parts[index]; start = part['vertex_start']
            members.append({'id': f'member-{index}', 'source_binding': {'asset_sha256': source_sha,
                **{key: part[key] for key in ('node_index', 'mesh_index', 'primitive_index')}, **(binding_updates or {})},
                'coordinate_frame_id': 'source-world',
                'positions': mesh.vertices[start:start+part['vertex_count']].copy(),
                'indices': mesh.faces[part['face_start']:part['face_start']+part['face_count']].copy()-start})
        prepared = prepare_optical_group(gid, members,
            identity={'status': 'unverified', 'provenance': {'method': 'explicit test membership'}},
            coordinate_frame={'id': 'source-world', 'units': 'unitless', 'up_axis': '+Y', 'forward_axis': '+Z',
                              'provenance': {'method': 'source selected scene world'}})
        result.append({'prepared': prepared, 'appearance': appearance(clear)})
    return result


class OpticalGroupAssetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name); self.source = self.folder/'source.glb'; self.output = self.folder/'candidate.glb'
        fixture(self.source)

    def write(self, groups=None):
        return write_optical_group_candidate(self.source, self.output,
            groups if groups is not None else groups_for(self.source, [('lens', [0])]),
            source_sha256=sha(self.source), provenance={'method': 'explicit test preparation'})

    def test_transformed_instance_preserved_and_actual_float32_read(self):
        original, before, oldbin = _read(self.source); oldmesh = load_glb(self.source)
        receipt = self.write(); actual = read_optical_group_candidate(self.output, receipt,
            expected_sha256=receipt['output_sha256'], expected_receipt_sha256=receipt['receipt_sha256'])
        _, after, newbin = _read(self.output)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(newbin[:len(oldbin)], oldbin)
        for name in ('nodes', 'meshes', 'materials', 'accessors', 'bufferViews'):
            self.assertEqual(after[name][:len(before[name])], before[name])
        for name in ('images', 'textures'):
            self.assertEqual(before[name], after[name])
        self.assertEqual(after['extras']['keep'], before['extras']['keep'])
        np.testing.assert_array_equal(actual['mesh'].vertices[:3], oldmesh.vertices[3:])
        np.testing.assert_array_equal(actual['mesh'].vertices[3:], oldmesh.vertices[:3].astype('f4').astype(float))
        self.assertTrue(np.isnan(actual['uv'][:3]).all())
        np.testing.assert_array_equal(actual['face_groups'], [-1, 0])
        row = receipt['groups'][0]['members'][0]
        self.assertEqual(row['source_to_common_matrix'][0], [2., 0., 0., .1])
        self.assertGreater(row['quantization']['attributes']['positions']['maximum_absolute_error'], 0)
        self.assertEqual(actual['groups'][0]['surface_binding']['prepared_glb_sha256'], sha(self.output))
        self.assertEqual(actual['groups'][0]['surface_binding']['optical_profile'], PROFILE)
        self.assertFalse(actual['accepted'])

    def test_multipart_common_uv_one_material_and_distinct_group_material_identity(self):
        receipt = self.write(groups_for(self.source, [('one-group', [0, 1])]))
        actual = read_optical_group_candidate(self.output, receipt)
        self.assertEqual(len(receipt['groups'][0]['members']), 2)
        self.assertEqual(len({m['material_index'] for m in receipt['groups'][0]['members']}), 1)
        self.assertEqual(actual['groups'][0]['source_part_indices'], [0, 1])
        np.testing.assert_array_equal(actual['face_groups'], [0, 0])
        group_uv = np.concatenate([p['uv'] for p in groups_for(self.source, [('one-group', [0, 1])])[0]['prepared']['primitives']])
        np.testing.assert_array_equal(actual['uv'], group_uv.astype('f4').astype(float))
        other = self.folder/'separate.glb'
        separate = write_optical_group_candidate(self.source, other, groups_for(self.source, [('z', [0]), ('a', [1])]),
            source_sha256=sha(self.source), provenance={'method': 'separate groups'})
        self.assertEqual([g['group_id'] for g in separate['groups']], ['a', 'z'])
        self.assertNotEqual(separate['groups'][0]['material_index'], separate['groups'][1]['material_index'])
        self.assertEqual(separate['groups'][0]['appearance_sha256'], separate['groups'][1]['appearance_sha256'])

    def test_other_scenes_nested_instances_and_reflection_survive(self):
        def mutate(doc):
            doc['scenes'].append({'nodes': [0]})
            doc['nodes'][1]['scale'] = [-2, 3, 4]
            doc['nodes'][1]['children'] = [3]
            doc['nodes'].append({'mesh': 0, 'translation': [0, 0, -1]})
        fixture(self.source, mutate)
        source_mesh = load_glb(self.source); _, before, _ = _read(self.source)
        receipt = self.write(); actual = read_optical_group_candidate(self.output, receipt)
        _, doc, binary = _read(self.output)
        self.assertEqual(doc['scenes'][1], before['scenes'][1])
        self.assertEqual(len(actual['mesh'].parts), len(source_mesh.parts))
        self.assertLess(receipt['groups'][0]['members'][0]['source_to_common_matrix'][0][0], 0)
        doc['scene'] = 1; alternate = self.folder/'alternate.glb'; alternate.write_bytes(_pack_glb(doc, binary))
        np.testing.assert_array_equal(load_glb(alternate).vertices, source_mesh.vertices)
        np.testing.assert_array_equal(load_glb(alternate).faces, source_mesh.faces)

    def test_closed_back_surface_and_clear_material_survive(self):
        # A tetrahedron has front/back/side triangles; no envelope is extracted.
        p = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]], '<f4')
        f = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], '<u4')
        doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': p.nbytes+f.nbytes}],
            'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': p.nbytes},
                            {'buffer': 0, 'byteOffset': p.nbytes, 'byteLength': f.nbytes}],
            'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 4, 'type': 'VEC3'},
                          {'bufferView': 1, 'componentType': 5125, 'count': 12, 'type': 'SCALAR'}],
            'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
            'nodes': [{'mesh': 0}], 'scenes': [{'nodes': [0]}], 'scene': 0}
        self.source.write_bytes(_pack_glb(doc, p.tobytes()+f.tobytes()))
        receipt = self.write(groups_for(self.source, [('closed', [0])], clear=True))
        actual = read_optical_group_candidate(self.output, receipt)
        np.testing.assert_array_equal(actual['mesh'].faces, f)
        np.testing.assert_array_equal(actual['mesh'].vertices, p)
        self.assertTrue(np.any(actual['normals'][:, 2] < 0))
        _, output, _ = _read(self.output)
        self.assertEqual(output['materials'][-1]['pbrMetallicRoughness']['baseColorFactor'][3], 0.)
        self.assertEqual(output['nodes'][-1]['extras']['lensSurfaceProfile'], PROFILE)
        self.assertEqual(len(actual['mesh'].parts), 1)
        self.assertIn(EXTENSION, output['materials'][-1]['extensions'])

    def test_undeclared_legacy_optical_primitives_are_explicitly_demoted_to_opaque(self):
        p = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., 1.]], '<f4')
        f = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], '<u4')
        lens = {'name': 'lens', 'alphaMode': 'BLEND', 'pbrMetallicRoughness': {'baseColorFactor': [.9, .9, 1., .6]},
                'extensions': {'KHR_materials_transmission': {'transmissionFactor': 1.}, 'KHR_materials_ior': {'ior': 1.5}}}
        ghost = {'name': 'ghost', 'alphaMode': 'BLEND', 'pbrMetallicRoughness': {'baseColorFactor': [1., 1., 1., 0.]},
                 'extensions': {'KHR_materials_transmission': {'transmissionFactor': .5}}}
        frame = {'name': 'frame', 'pbrMetallicRoughness': {'baseColorFactor': [.1, .1, .1, 1.]}}
        primitive = {'attributes': {'POSITION': 0}, 'indices': 1}
        doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': p.nbytes+f.nbytes}],
            'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': p.nbytes},
                            {'buffer': 0, 'byteOffset': p.nbytes, 'byteLength': f.nbytes}],
            'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 4, 'type': 'VEC3'},
                          {'bufferView': 1, 'componentType': 5125, 'count': 12, 'type': 'SCALAR'}],
            'materials': [lens, ghost, frame],
            'meshes': [{'primitives': [{**primitive, 'material': 0}]},
                       {'primitives': [{**primitive, 'material': 0}, {**primitive, 'material': 1}]},
                       {'primitives': [{**primitive, 'material': 2}]}],
            'nodes': [{'mesh': 0, 'extras': {'partRole': 'lens'}, 'translation': [0, 0, 0]},
                      {'mesh': 1, 'extras': {'partRole': 'lens'}, 'translation': [3, 0, 0]},
                      {'mesh': 2, 'extras': {'partRole': 'frame'}, 'translation': [6, 0, 0]}],
            'scenes': [{'nodes': [0, 1, 2]}], 'scene': 0, 'extensionsUsed': ['KHR_materials_transmission', 'KHR_materials_ior']}
        self.source.write_bytes(_pack_glb(doc, p.tobytes()+f.tobytes()))
        receipt = self.write(groups_for(self.source, [('declared', [0])], clear=True))
        demoted = receipt['demoted_legacy_optical_parts']
        self.assertEqual([(d['source_node_index'], d['source_primitive_index']) for d in demoted], [(1, 0), (1, 1)])
        self.assertEqual(demoted[0]['removed_extensions'], ['KHR_materials_transmission', 'KHR_materials_ior'])
        self.assertEqual(demoted[0]['alpha_mode_conversion'], 'BLEND with positive alpha converted to OPAQUE alpha 1')
        self.assertIsNone(demoted[1]['alpha_mode_conversion'], 'an invisible alpha-zero primitive is not resurrected')
        self.assertEqual(demoted[0]['original_part_role'], 'lens')
        _, output, _ = _read(self.output)
        self.assertEqual([m['name'] for m in output['materials'][:3]], ['lens', 'ghost', 'frame'], 'original records stay')
        visible = output['materials'][demoted[0]['demoted_material_index']]
        self.assertNotIn('extensions', visible)
        self.assertEqual(visible['alphaMode'], 'OPAQUE')
        self.assertEqual(visible['pbrMetallicRoughness']['baseColorFactor'][3], 1)
        invisible = output['materials'][demoted[1]['demoted_material_index']]
        self.assertEqual(invisible['alphaMode'], 'BLEND')
        self.assertEqual(invisible['pbrMetallicRoughness']['baseColorFactor'][3], 0.)
        node = output['nodes'][demoted[0]['candidate_node_index']]
        self.assertEqual(node['extras']['partRole'], 'undeclared_optical_remainder')
        self.assertEqual(node['extras']['undeclaredOpticalRemainder']['source_primitive_indices'], [0, 1])
        self.assertEqual(output['nodes'][demoted[0]['candidate_node_index']+1]['extras']['partRole'], 'frame')
        actual = read_optical_group_candidate(self.output, receipt)
        self.assertEqual(len(actual['mesh'].parts), 3, 'declared group, demoted visible remainder and frame; ghost stays invisible')
        for material in output['materials']:
            transmission = material.get('extensions', {}).get('KHR_materials_transmission', {}).get('transmissionFactor', 0)
            self.assertTrue(transmission == 0 or material['name'] in ('lens', 'ghost'), 'no live legacy optics beside the canonical group')

    def test_same_group_coincidence_supported_cross_group_conflict_rejected(self):
        def duplicate(doc):
            doc['meshes'][0]['primitives'].append(deepcopy(doc['meshes'][0]['primitives'][0]))
        fixture(self.source, duplicate)
        receipt = self.write(groups_for(self.source, [('same', [0, 1])]))
        self.assertGreater(receipt['coincident_patch_validation']['counts']['proven_same_group_pairs'], 0)
        bad = self.folder/'bad.glb'
        with self.assertRaisesRegex(ValueError, 'coincident-patch'):
            write_optical_group_candidate(self.source, bad, groups_for(self.source, [('one', [0]), ('two', [1])]),
                source_sha256=sha(self.source), provenance={'method': 'test'})
        self.assertFalse(bad.exists())

    def test_float32_collapse_external_resources_and_instancing_fail_before_write(self):
        for mutate, message in (
                (lambda d: d['nodes'][0].update(translation=[1e10, 1e10, 0]), 'collapses'),
                (lambda d: d['images'].__setitem__(0, {'uri': 'texture.png'}), 'External image'),
                (lambda d: d['nodes'][1].update(extensions={'EXT_mesh_gpu_instancing': {}}), 'instancing')):
            with self.subTest(message=message):
                fixture(self.source, mutate)
                with self.assertRaisesRegex(ValueError, message):
                    self.write()
                self.assertFalse(self.output.exists())

    def test_cross_group_coincidence_created_only_by_float32_is_rejected(self):
        def separated(doc):
            doc['nodes'][0] = {'children': [1, 2]}
            doc['nodes'][1] = {'mesh': 0, 'translation': [0, 0, 1.]}
            doc['nodes'][2] = {'mesh': 0, 'translation': [0, 0, 1.+1e-8]}
        fixture(self.source, separated)
        mesh = load_glb(self.source)
        self.assertNotEqual(mesh.vertices[0, 2], mesh.vertices[3, 2])
        self.assertEqual(np.float32(mesh.vertices[0, 2]), np.float32(mesh.vertices[3, 2]))
        with self.assertRaisesRegex(ValueError, 'cross_group_coincident_patch_undefined_order'):
            self.write(groups_for(self.source, [('front', [0]), ('back', [1])]))
        self.assertFalse(self.output.exists())

    def test_prepared_membership_and_hash_changes_rejected(self):
        groups = groups_for(self.source, [('one', [0])]); groups[0]['prepared']['primitives'][0]['positions'][0, 0] += .01
        with self.assertRaisesRegex(ValueError, 'report hash'):
            self.write(groups)
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.write(groups_for(self.source, [('one', [0]), ('two', [0])]))
        groups = groups_for(self.source, [('one', [0])]); groups[0]['prepared']['primitives'].clear()
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.write(groups)
        groups = groups_for(self.source, [('one', [0])]); groups[0]['prepared']['report']['group_id'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'report hash'):
            self.write(groups)
        self.assertFalse(self.output.exists())

    def test_receipt_and_glb_hash_tampering_no_overwrite(self):
        receipt = self.write(); before = self.output.read_bytes()
        bad = deepcopy(receipt); bad['groups'][0]['members'].clear()
        with self.assertRaisesRegex(ValueError, 'receipt hash'):
            read_optical_group_candidate(self.output, bad)
        with self.assertRaisesRegex(ValueError, 'receipt hash'):
            read_optical_group_candidate(self.output, receipt, expected_receipt_sha256='0'*64)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            read_optical_group_candidate(self.output, receipt, expected_sha256='0'*64)
        with self.assertRaisesRegex(ValueError, 'new destination'):
            self.write()
        self.assertEqual(self.output.read_bytes(), before)
        changed = bytearray(before); changed[-1] ^= 1; self.output.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            read_optical_group_candidate(self.output, receipt)

    def test_optional_source_accessor_and_material_bindings_are_verified(self):
        for name in ('material_index', 'position_accessor', 'index_accessor', 'normal_accessor'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Optional source binding'):
                self.write(groups_for(self.source, [('one', [0])], binding_updates={name: 999}))
        self.assertFalse(self.output.exists())
        _, doc, _ = _read(self.source)
        primitive = doc['meshes'][0]['primitives'][0]
        correct = {'material_index': primitive['material'], 'position_accessor': primitive['attributes']['POSITION'],
                   'index_accessor': primitive['indices'], 'normal_accessor': primitive['attributes']['NORMAL']}
        receipt = self.write(groups_for(self.source, [('one', [0])], binding_updates=correct))
        self.assertEqual(receipt['groups'][0]['members'][0]['source_binding']['normal_accessor'], correct['normal_accessor'])

    def test_captured_bytes_prevent_opaque_geometry_aba_reread(self):
        receipt = self.write(); original, doc, binary = _read(self.output)
        expected = read_optical_group_candidate(self.output, receipt)['mesh'].vertices.copy()
        # This changed opaque instance is valid GLB and leaves every optical
        # member/attribute unchanged. The previous separate mesh read could use
        # B while both the first and final hash reads saw the pinned A bytes.
        doc['nodes'][receipt['original_record_counts']['nodes']+2]['translation'][0] += 97
        changed = _pack_glb(doc, binary); real_read = Path.read_bytes
        reads = []
        def aba(path):
            if path.resolve() == self.output.resolve():
                reads.append(path)
                return changed if len(reads) == 2 else original
            return real_read(path)
        with patch.object(Path, 'read_bytes', aba):
            with self.assertRaisesRegex(ValueError, 'changed during read'):
                read_optical_group_candidate(self.output, receipt)
        self.assertEqual(len(reads), 2)  # Captured decode makes no middle path read.
        np.testing.assert_array_equal(read_optical_group_candidate(self.output, receipt)['mesh'].vertices, expected)

    def rewrite_bound_candidate(self, receipt, mutate):
        # Rehash every artifact pointer deliberately: a structurally invalid
        # candidate must not pass merely because all transport hashes agree.
        receipt = deepcopy(receipt); receipt.pop('receipt_sha256')
        _, doc, binary = _read(self.output); binary = bytearray(binary)
        member = receipt['groups'][0]['members'][0]
        primitive = doc['meshes'][member['mesh_index']]['primitives'][0]
        arrays = {}
        for name, attr, width in (('positions', 'POSITION', 3), ('normals', 'NORMAL', 3), ('uv', 'TEXCOORD_0', 2)):
            accessor = doc['accessors'][primitive['attributes'][attr]]; view = doc['bufferViews'][accessor['bufferView']]
            arrays[name] = np.ndarray((accessor['count'], width), dtype='<f4', buffer=binary,
                offset=view.get('byteOffset', 0)+accessor.get('byteOffset', 0))
        mutate(arrays, doc, member, primitive)
        for name, array in arrays.items(): member['attribute_sha256'][name] = hashlib.sha256(array.tobytes()).hexdigest()
        declaration = {'schema_version': 1, 'profile': PROFILE, 'source_sha256': receipt['source_sha256'],
                       'groups': receipt['groups'], 'provenance': receipt['provenance'], 'accepted': False}
        receipt['declaration_sha256'] = _hash(declaration)
        doc['extras']['effectiveOpticalGroups'] = {'declaration_sha256': _hash(declaration), **declaration}
        self.output.write_bytes(_pack_glb(doc, binary)); receipt['output_sha256'] = sha(self.output)
        return _seal(receipt)

    def test_old_receipt_revalidated_and_new_runtime_proof_not_trusted(self):
        receipt = self.write()
        self.assertTrue(receipt['runtime_contract_validation']['complete'])
        old = deepcopy(receipt); old.pop('receipt_sha256'); old.pop('runtime_contract_validation')
        result = read_optical_group_candidate(self.output, _seal(old))
        self.assertTrue(result['runtime_contract_validation']['complete'])
        self.assertFalse(result['accepted']); self.assertEqual(result['quality_verdict'], 'unmeasured')
        for normals, reason in (([[0, 0, 1], [0, 0, -2], [.25, 0, 1]], 'zero_on_edge'),
                                ([[1, 0, 0], [0, 2, 0], [-4, -4, 0]], 'zero_interior')):
            with self.subTest(reason=reason):
                bound = self.rewrite_bound_candidate(receipt, lambda a, d, m, p: a['normals'].__setitem__(slice(None), normals))
                with self.assertRaisesRegex(ValueError, reason): read_optical_group_candidate(self.output, bound)

    def test_rehashed_height_metadata_and_accessor_contract_fail_strict_read(self):
        receipt = self.write(); original = self.output.read_bytes()
        def uv(a, d, m, p): a['uv'][1, 1] = .5
        def metadata(a, d, m, p):
            m['node_metadata']['partRole'] = 'frame'
            d['nodes'][m['node_index']]['extras'] = deepcopy(m['node_metadata'])
        def accessor(a, d, m, p): d['accessors'][p['attributes']['NORMAL']]['normalized'] = True
        for mutate, message in ((uv, 'group_wide_Y'), (metadata, 'profile/binding'), (accessor, 'float32 accessors')):
            with self.subTest(message=message):
                self.output.write_bytes(original)
                bound = self.rewrite_bound_candidate(receipt, mutate)
                with self.assertRaisesRegex(ValueError, message): read_optical_group_candidate(self.output, bound)

    def test_export_rejects_supplied_interpolant_singularity_before_write(self):
        groups = groups_for(self.source, [('one', [0])]); old = groups[0]['prepared']
        item = old['primitives'][0]
        prepared = prepare_optical_group('one', [{k: item[k] for k in ('id', 'positions', 'indices')} | {
            'source_binding': old['report']['primitives'][0]['source_binding'],
            'coordinate_frame_id': 'source-world', 'normals': np.asarray([[1., 0, 0], [0, 2., 0], [-4., -4., 0]]),
            'normal_transform': {'method': 'identity', 'source_to_common_matrix': np.eye(4).tolist(),
                                 'provenance': {'method': 'controlled common-frame normal hypothesis'}}}],
            identity=old['report']['identity'], coordinate_frame=old['report']['coordinate_frame'])
        with self.assertRaisesRegex(ValueError, 'normal_field_contains_zero_interior'):
            self.write([{'prepared': prepared, 'appearance': appearance()}])
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
