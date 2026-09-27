"""Independent checks for lossless face partitioning and source-face lineage."""
from copy import deepcopy
import hashlib
import json
import struct
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction.partition_glb import inspect_partition_source, partition_glb_bytes, verify_partition_glb_bytes


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def reseal(report):
    report = deepcopy(report)
    report.pop('receipt_sha256', None)
    report['receipt_sha256'] = sha(json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    return report


def pack(document, binary):
    encoded = json.dumps(document, separators=(',', ':'), allow_nan=False).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary += b'\0' * (-len(binary) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 28 + len(encoded) + len(binary))
            + struct.pack('<II', len(encoded), 0x4E4F534A) + encoded
            + struct.pack('<II', len(binary), 0x004E4942) + binary)


def unpack(raw):
    magic, version, length = struct.unpack_from('<4sII', raw)
    assert (magic, version, length) == (b'glTF', 2, len(raw))
    chunks, cursor = {}, 12
    while cursor < len(raw):
        length, kind = struct.unpack_from('<II', raw, cursor)
        cursor += 8
        chunks[kind] = raw[cursor:cursor + length]
        cursor += length
    return json.loads(chunks[0x4E4F534A]), chunks[0x004E4942]


def accessor(document, binary, index):
    """Decode only ordinary numeric test accessors, independent of core code."""
    row = document['accessors'][index]
    view = document['bufferViews'][row['bufferView']]
    dtype = np.dtype({5121: 'u1', 5123: '<u2', 5125: '<u4', 5126: '<f4'}[row['componentType']])
    columns = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[row['type']]
    return np.ndarray((row['count'], columns), dtype=dtype, buffer=binary,
                      offset=view.get('byteOffset', 0) + row.get('byteOffset', 0),
                      strides=(view.get('byteStride', columns * dtype.itemsize), dtype.itemsize)).copy()


def triangles(document, binary, primitive):
    if 'indices' in primitive:
        return accessor(document, binary, primitive['indices']).reshape((-1, 3))
    return np.arange(document['accessors'][primitive['attributes']['POSITION']]['count']).reshape((-1, 3))


def selected_nodes(document):
    result = []
    def visit(index):
        result.append(index)
        for child in document['nodes'][index].get('children', []):
            visit(child)
    for root in document['scenes'][document.get('scene', 0)]['nodes']:
        visit(root)
    return result


def fixture(*, indexed=True, duplicate_degenerate=False, invisible=True):
    if indexed:
        positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [2, 0, 0], [2, 1, 0]], '<f4')
        faces = np.array([[0, 1, 2], [2, 1, 3], [1, 4, 3], [3, 4, 5]], '<u2')
        if duplicate_degenerate:
            faces = np.array([[0, 1, 2], [0, 1, 2], [1, 1, 3], [3, 4, 5]], '<u2')
    else:
        positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0],
                              [2, 0, 0], [3, 0, 0], [2, 1, 0],
                              [4, 0, 0], [5, 0, 0], [4, 1, 0],
                              [6, 0, 0], [7, 0, 0], [6, 1, 0]], '<f4')
        faces = np.arange(12, dtype='<u2').reshape((-1, 3))
    count = len(positions)
    packed = np.zeros((count, 8), '<f4')
    packed[:, :3] = positions
    packed[:, 5] = 1
    packed[:, 6:] = positions[:, :2] / 8
    binary = bytearray(packed.tobytes())
    views = [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(binary), 'byteStride': 32, 'target': 34962}]
    accessors = [
        {'bufferView': 0, 'byteOffset': 0, 'componentType': 5126, 'count': count, 'type': 'VEC3',
         'min': positions.min(axis=0).tolist(), 'max': positions.max(axis=0).tolist()},
        {'bufferView': 0, 'byteOffset': 12, 'componentType': 5126, 'count': count, 'type': 'VEC3'},
        {'bufferView': 0, 'byteOffset': 24, 'componentType': 5126, 'count': count, 'type': 'VEC2'},
    ]
    def append(values, kind, component, **extra):
        binary.extend(b'\0' * (-len(binary) % 4))
        views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': values.nbytes})
        binary.extend(values.tobytes())
        accessors.append({'bufferView': len(views) - 1, 'componentType': component,
                          'count': values.size if kind == 'SCALAR' else len(values), 'type': kind, **extra})
        return len(accessors) - 1
    index_accessor = append(faces.ravel(), 'SCALAR', 5123) if indexed else None
    color_accessor = append(np.tile(np.array([[70, 90, 120, 255]], 'u1'), (count, 1)), 'VEC4', 5121, normalized=True)
    sparse_index = append(np.array([0], 'u1'), 'SCALAR', 5121)
    sparse_values = append(np.array([[255, 0, 100, 255]], 'u1'), 'VEC4', 5121, normalized=True)
    accessors.append({'componentType': 5121, 'type': 'VEC4', 'count': count, 'normalized': True,
                      'sparse': {'count': 1,
                                 'indices': {'bufferView': accessors[sparse_index]['bufferView'], 'componentType': 5121},
                                 'values': {'bufferView': accessors[sparse_values]['bufferView']}}})
    primitive = {'attributes': {'POSITION': 0, 'NORMAL': 1, 'TEXCOORD_0': 2,
                                'COLOR_0': color_accessor, 'COLOR_1': len(accessors) - 1},
                 'material': 0, 'mode': 4, 'extras': {'original_primitive_note': 'retain every field'}}
    if indexed:
        primitive['indices'] = index_accessor
    primitives = [primitive]
    if invisible:
        hidden = deepcopy(primitive)
        hidden['material'] = 1
        hidden['extras'] = {'invisible_but_present': True}
        primitives.append(hidden)
    document = {'asset': {'version': '2.0', 'generator': 'independent partition test'},
                'buffers': [{'byteLength': len(binary)}], 'bufferViews': views, 'accessors': accessors,
                'materials': [{'name': 'original', 'alphaMode': 'BLEND',
                               'pbrMetallicRoughness': {'baseColorFactor': [0.3, 0.5, 0.7, 0.5]}},
                              {'name': 'invisible', 'alphaMode': 'BLEND',
                               'pbrMetallicRoughness': {'baseColorFactor': [1, 1, 1, 0]}}],
                'meshes': [{'name': 'shared source', 'primitives': primitives, 'extras': {'preserve': 17}}],
                'nodes': [{'name': 'root', 'children': [1, 2], 'translation': [0, 0, 2]},
                          {'name': 'target', 'mesh': 0, 'scale': [2, 3, 1], 'extras': {'partRole': 'unverified'}},
                          {'name': 'other instance', 'mesh': 0, 'translation': [4, 0, 0]},
                          {'name': 'inactive instance', 'mesh': 0, 'translation': [-4, 0, 0]}],
                'scenes': [{'name': 'selected', 'nodes': [0]}, {'name': 'inactive shared hierarchy', 'nodes': [0, 3]}],
                'scene': 0, 'extras': {'retain': {'value': [1, 2, 3]}}}
    return pack(document, bytes(binary)), faces


def declaration(raw, *, binding=None, pieces=None):
    return {'schema_version': 1, 'source_sha256': sha(raw), 'provenance': {'method': 'explicit test face hypothesis'},
            'partitions': [{'source_binding': binding or {'node_index': 1, 'mesh_index': 0, 'primitive_index': 0},
                            'pieces': pieces or [{'id': 'odd', 'source_face_indices': [3, 1]},
                                                 {'id': 'even', 'source_face_indices': [2, 0]}]}]}


class PartitionGlbTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.faces = fixture()

    def changed_node(self, document):
        return next(document['nodes'][i] for i in selected_nodes(document) if document['nodes'][i].get('name') == 'target')

    def test_every_source_record_attribute_and_binary_byte_is_retained(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        before, oldbin = unpack(self.raw)
        after, newbin = unpack(out)
        self.assertEqual(newbin[:len(oldbin)], oldbin)
        for key in ('nodes', 'meshes', 'accessors', 'bufferViews', 'materials'):
            self.assertEqual(after[key][:len(before[key])], before[key])
        self.assertEqual(after['extras']['retain'], before['extras']['retain'])
        self.assertEqual(after['scenes'][1:], before['scenes'][1:])
        self.assertEqual(after['nodes'][:4], before['nodes'])
        parts = after['meshes'][self.changed_node(after)['mesh']]['primitives']
        self.assertEqual(len(parts), 3)
        for p in parts[:2]:
            self.assertEqual({k: v for k, v in p.items() if k != 'indices'},
                             {k: v for k, v in before['meshes'][0]['primitives'][0].items() if k != 'indices'})
        self.assertEqual(parts[2], before['meshes'][0]['primitives'][1])
        np.testing.assert_array_equal(triangles(after, newbin, parts[0]), self.faces[[0, 2]])
        np.testing.assert_array_equal(triangles(after, newbin, parts[1]), self.faces[[1, 3]])
        self.assertEqual(len(after['accessors']) - len(before['accessors']), 2)
        self.assertTrue(all(a['type'] == 'SCALAR' for a in after['accessors'][len(before['accessors']):]))
        self.assertIsInstance(verify_partition_glb_bytes(self.raw, out, report), dict)

    def test_shared_instances_and_inactive_scene_are_not_repartitioned(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        before, _ = unpack(self.raw)
        after, _ = unpack(out)
        active = selected_nodes(after)
        self.assertTrue(set(active).isdisjoint(selected_nodes(before)))
        other = next(after['nodes'][i] for i in active if after['nodes'][i].get('name') == 'other instance')
        self.assertEqual(other['mesh'], 0)
        self.assertEqual(other, before['nodes'][2])
        self.assertEqual(after['scenes'][1], before['scenes'][1])
        self.assertEqual(after['nodes'][0], before['nodes'][0])
        self.assertEqual(len(after['meshes']), len(before['meshes']) + 1)
        self.assertIsInstance(verify_partition_glb_bytes(self.raw, out, report), dict)

    def test_two_changed_instances_get_independent_meshes_and_memberships(self):
        request = declaration(self.raw)
        request['partitions'].append({'source_binding': {'node_index': 2, 'mesh_index': 0, 'primitive_index': 0},
                                      'pieces': [{'id': 'other-first', 'source_face_indices': [0]},
                                                 {'id': 'other-rest', 'source_face_indices': [3, 1, 2]}]})
        saved_request = deepcopy(request)
        out, report = partition_glb_bytes(self.raw, request)
        after, binary = unpack(out)
        target = self.changed_node(after)
        other = next(after['nodes'][i] for i in selected_nodes(after) if after['nodes'][i].get('name') == 'other instance')
        self.assertNotEqual(target['mesh'], other['mesh'])
        self.assertNotEqual(other['mesh'], 0)
        other_parts = after['meshes'][other['mesh']]['primitives']
        np.testing.assert_array_equal(triangles(after, binary, other_parts[0]), self.faces[[0]])
        np.testing.assert_array_equal(triangles(after, binary, other_parts[1]), self.faces[[1, 2, 3]])
        self.assertEqual(request, saved_request)
        self.assertEqual(verify_partition_glb_bytes(self.raw, out, report)['status'], 'verified')

    def test_invisible_primitive_can_itself_be_partitioned_without_material_reassignment(self):
        request = declaration(self.raw, binding={'node_index': 1, 'mesh_index': 0, 'primitive_index': 1})
        out, report = partition_glb_bytes(self.raw, request)
        before, _ = unpack(self.raw)
        after, binary = unpack(out)
        parts = after['meshes'][self.changed_node(after)['mesh']]['primitives']
        self.assertEqual(parts[0], before['meshes'][0]['primitives'][0])
        self.assertEqual([p['material'] for p in parts], [0, 1, 1])
        np.testing.assert_array_equal(triangles(after, binary, parts[1]), self.faces[[0, 2]])
        np.testing.assert_array_equal(triangles(after, binary, parts[2]), self.faces[[1, 3]])
        self.assertEqual(verify_partition_glb_bytes(self.raw, out, report)['status'], 'verified')

    def test_nonindexed_source_gains_only_partition_index_accessors(self):
        raw, faces = fixture(indexed=False)
        out, report = partition_glb_bytes(raw, declaration(raw))
        before, oldbin = unpack(raw)
        after, newbin = unpack(out)
        self.assertEqual(newbin[:len(oldbin)], oldbin)
        parts = after['meshes'][self.changed_node(after)['mesh']]['primitives']
        np.testing.assert_array_equal(triangles(after, newbin, parts[0]), faces[[0, 2]])
        np.testing.assert_array_equal(triangles(after, newbin, parts[1]), faces[[1, 3]])
        self.assertNotIn('indices', parts[2])
        self.assertEqual(after['accessors'][:len(before['accessors'])], before['accessors'])
        self.assertIsInstance(verify_partition_glb_bytes(raw, out, report), dict)

    def test_duplicate_and_degenerate_triangles_are_not_cleaned_or_deduplicated(self):
        raw, faces = fixture(duplicate_degenerate=True)
        out, report = partition_glb_bytes(raw, declaration(raw))
        document, binary = unpack(out)
        parts = document['meshes'][self.changed_node(document)['mesh']]['primitives']
        np.testing.assert_array_equal(triangles(document, binary, parts[0]), faces[[0, 2]])
        np.testing.assert_array_equal(triangles(document, binary, parts[1]), faces[[1, 3]])
        self.assertIsInstance(verify_partition_glb_bytes(raw, out, report), dict)

    def test_semantic_order_does_not_change_canonical_geometry_output(self):
        first = declaration(self.raw)
        second = deepcopy(first)
        second['partitions'][0]['pieces'].reverse()
        for piece in second['partitions'][0]['pieces']:
            piece['source_face_indices'].reverse()
        out_a, _ = partition_glb_bytes(self.raw, first)
        out_b, _ = partition_glb_bytes(self.raw, second)
        # The source declaration order may be retained as provenance, but actual
        # output primitives must always be ordered by their first source face.
        doc_a, bin_a = unpack(out_a)
        doc_b, bin_b = unpack(out_b)
        parts_a = doc_a['meshes'][self.changed_node(doc_a)['mesh']]['primitives']
        parts_b = doc_b['meshes'][self.changed_node(doc_b)['mesh']]['primitives']
        self.assertEqual(parts_a, parts_b)
        for a, b in zip(parts_a, parts_b):
            np.testing.assert_array_equal(triangles(doc_a, bin_a, a), triangles(doc_b, bin_b, b))

    def test_declared_piece_role_travels_on_the_primitive_not_the_node(self):
        from reconstruction.mesh import load_glb_bytes
        pieces = [{'id': 'lens', 'source_face_indices': [3, 1], 'declared_role': 'optical'},
                  {'id': 'rest', 'source_face_indices': [2, 0]}]
        out, report = partition_glb_bytes(self.raw, declaration(self.raw, pieces=pieces))
        document, _ = unpack(out)
        node = self.changed_node(document)
        self.assertEqual(node['extras'], {'partRole': 'unverified'}, 'the source node keeps its own extras')
        primitives = document['meshes'][node['mesh']]['primitives']
        by_piece = {row['piece_id']: primitives[row['output_binding']['primitive_index']]
                    for row in report['primitives'][0]['outputs']}
        self.assertEqual({k: by_piece['lens']['extras'][k] for k in ('partRole', 'pieceId')}, {'partRole': 'optical', 'pieceId': 'lens'})
        self.assertEqual(by_piece['lens']['extras'].get('original_primitive_note'), by_piece['rest'].get('extras', {}).get('original_primitive_note'), 'source primitive extras are kept')
        self.assertNotIn('partRole', by_piece['rest'].get('extras', {}))
        self.assertEqual({p['id']: p.get('declared_role') for p in report['declarations']['partitions'][0]['pieces']}, {'lens': 'optical', 'rest': None})
        parts = load_glb_bytes(out).parts
        roles = {p['declared_role'] for p in parts if p['name'] == 'target'}
        self.assertEqual(roles, {'optical', 'unverified'}, 'the loader reads the primitive role before the node role')
        with self.assertRaises(ValueError):
            partition_glb_bytes(self.raw, declaration(self.raw, pieces=[{**pieces[0], 'declared_role': 'frame'}, pieces[1]]))
        with self.assertRaises(ValueError):
            partition_glb_bytes(self.raw, declaration(self.raw, pieces=[{**pieces[0], 'role': 'optical'}, pieces[1]]))

    def test_invalid_incomplete_or_overlapping_face_membership_is_rejected(self):
        invalid = [[], [0, 1, 2], [0, 1, 2, 4], [-1, 1, 2, 3], [False, 1, 2, 3],
                   [0., 1, 2, 3], [0, 1, 1, 2, 3]]
        for indices in invalid:
            with self.subTest(indices=indices):
                request = declaration(self.raw)
                request['partitions'][0]['pieces'] = [{'id': 'only', 'source_face_indices': indices}]
                with self.assertRaises(ValueError):
                    partition_glb_bytes(self.raw, request)
        request = declaration(self.raw)
        request['partitions'][0]['pieces'][1]['source_face_indices'] = [0, 1, 2]
        with self.assertRaises(ValueError):
            partition_glb_bytes(self.raw, request)

    def test_stale_wrong_inactive_and_duplicate_bindings_are_rejected(self):
        cases = []
        stale = declaration(self.raw); stale['source_sha256'] = '0' * 64; cases.append(stale)
        for binding in ({'node_index': 1, 'mesh_index': 1, 'primitive_index': 0},
                        {'node_index': 1, 'mesh_index': 0, 'primitive_index': 99},
                        {'node_index': 3, 'mesh_index': 0, 'primitive_index': 0},
                        {'node_index': True, 'mesh_index': 0, 'primitive_index': 0}):
            cases.append(declaration(self.raw, binding=binding))
        duplicate = declaration(self.raw); duplicate['partitions'].append(deepcopy(duplicate['partitions'][0])); cases.append(duplicate)
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    partition_glb_bytes(self.raw, case)

    def test_piece_identifiers_are_safe_and_globally_unique(self):
        for name in ('../escape', '', 'contains spaces'):
            request = declaration(self.raw)
            request['partitions'][0]['pieces'][0]['id'] = name
            with self.subTest(name=name), self.assertRaises(ValueError):
                partition_glb_bytes(self.raw, request)
        request = declaration(self.raw)
        other = deepcopy(request['partitions'][0]); other['source_binding']['node_index'] = 2
        request['partitions'].append(other)
        with self.assertRaises(ValueError):
            partition_glb_bytes(self.raw, request)

    def test_verifier_rejects_changed_source_and_unsealed_output_edits(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        document, binary = unpack(self.raw)
        document['nodes'][1]['scale'][0] = 5
        with self.assertRaises(ValueError):
            verify_partition_glb_bytes(pack(document, binary), out, report)
        document, binary = unpack(out)
        target = self.changed_node(document)
        target['scale'][0] = 5
        with self.assertRaises(ValueError):
            verify_partition_glb_bytes(self.raw, pack(document, binary), report)

    def test_receipt_covers_invisible_primitives_and_exact_original_triangle_ordinals(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        self.assertEqual(report['receipt_sha256'], reseal(report)['receipt_sha256'])
        self.assertEqual(report['method'], 'source_preserving_face_partition_v1')
        self.assertEqual(report['source_sha256'], sha(self.raw))
        self.assertEqual(report['output_sha256'], sha(out))
        self.assertFalse(report['accepted'])
        self.assertEqual(report['quality_verdict'], 'unmeasured')
        self.assertEqual(report['render_equivalence'], 'unmeasured')
        by_binding = {tuple(row['source_binding'][k] for k in ('node_index', 'mesh_index', 'primitive_index')): row
                      for row in report['primitives']}
        self.assertEqual(set(by_binding), {(1, 0, 0), (1, 0, 1), (2, 0, 0), (2, 0, 1)})
        split = by_binding[(1, 0, 0)]
        self.assertEqual(split['source_face_count'], 4)
        self.assertEqual(split['source_vertex_count'], 6)
        self.assertFalse(split['source_triangle_order_preserved'])
        self.assertEqual([row['piece_id'] for row in split['outputs']], ['even', 'odd'])
        self.assertEqual([row['source_face_ranges'] for row in split['outputs']],
                         [[[0, 1], [2, 3]], [[1, 2], [3, 4]]])
        # Hidden primitive index 1 must move to index 2 in the target clone,
        # although the ordinary rendering evaluator would omit that primitive.
        hidden = by_binding[(1, 0, 1)]['outputs']
        self.assertEqual(len(hidden), 1)
        self.assertIsNone(hidden[0]['piece_id'])
        self.assertEqual(hidden[0]['output_binding']['primitive_index'], 2)
        self.assertEqual(hidden[0]['source_face_ranges'], [[0, 4]])
        with patch('reconstruction.partition_glb.partition_glb_bytes', side_effect=AssertionError('Verifier called exporter')):
            checked = verify_partition_glb_bytes(self.raw, out, report)
        self.assertEqual(checked['status'], 'verified')

    def test_rehashed_new_index_corruption_is_not_accepted_as_a_valid_partition(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        document, original_binary = unpack(out)
        binary = bytearray(original_binary)
        primitive = document['meshes'][self.changed_node(document)['mesh']]['primitives'][0]
        row = document['accessors'][primitive['indices']]
        view = document['bufferViews'][row['bufferView']]
        offset = view.get('byteOffset', 0) + row.get('byteOffset', 0)
        dtype = np.dtype({5121: 'u1', 5123: '<u2', 5125: '<u4'}[row['componentType']])
        indices = np.frombuffer(binary, dtype=dtype, count=row['count'], offset=offset)
        indices[0], indices[1] = int(indices[1]), int(indices[0])
        corrupted = pack(document, bytes(binary))
        forged = deepcopy(report); forged['output_sha256'] = sha(corrupted)
        with self.assertRaises(ValueError):
            verify_partition_glb_bytes(self.raw, corrupted, reseal(forged))

    def test_rehashed_lineage_cannot_relabel_the_same_output_triangles(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        forged = deepcopy(report)
        row = next(p for p in forged['primitives'] if p['source_binding'] == {'node_index': 1, 'mesh_index': 0, 'primitive_index': 0})
        row['outputs'][0]['source_face_ranges'] = [[1, 2], [3, 4]]
        with self.assertRaises(ValueError):
            verify_partition_glb_bytes(self.raw, out, reseal(forged))
        forged = deepcopy(report)
        row = next(p for p in forged['primitives'] if p['source_binding'] == {'node_index': 1, 'mesh_index': 0, 'primitive_index': 0})
        row['outputs'][0]['output_binding']['primitive_index'] = 1
        with self.assertRaises(ValueError):
            verify_partition_glb_bytes(self.raw, out, reseal(forged))

    def test_rehashed_source_attribute_or_material_mutation_is_rejected(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        document, binary = unpack(out)
        for mutation in ('attribute', 'material'):
            altered = deepcopy(document)
            if mutation == 'attribute':
                altered['meshes'][self.changed_node(altered)['mesh']]['primitives'][0]['attributes']['COLOR_0'] = 0
            else:
                altered['materials'][0]['pbrMetallicRoughness']['baseColorFactor'][0] = .9
            corrupted = pack(altered, binary)
            forged = deepcopy(report); forged['output_sha256'] = sha(corrupted)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_partition_glb_bytes(self.raw, corrupted, reseal(forged))

    def test_rehashed_material_boolean_cannot_replace_equal_numeric_zero(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        document, binary = unpack(out)
        alpha = document['materials'][1]['pbrMetallicRoughness']['baseColorFactor']
        self.assertIs(type(alpha[3]), int)
        self.assertEqual(alpha[3], 0)
        alpha[3] = False
        corrupted = pack(document, binary)
        forged = deepcopy(report); forged['output_sha256'] = sha(corrupted)
        # Both hashes are valid; preservation must distinguish JSON false from
        # numeric zero even though ordinary Python metadata equality does not.
        with self.assertRaises(ValueError):
            verify_partition_glb_bytes(self.raw, corrupted, reseal(forged))

    def test_resealed_receipt_rejects_equal_values_with_changed_json_types(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        replacements = [
            (('selected_scene',), False),
            (('node_map', 0, 'source_node_index'), False),
            (('node_map', 0, 'output_node_index'), 4.0),
            (('primitives', 0, 'source_binding', 'mesh_index'), False),
            (('primitives', 0, 'source_face_count'), 4.0),
            (('primitives', 0, 'source_vertex_count'), 6.0),
            (('primitives', 0, 'outputs', 0, 'output_binding', 'primitive_index'), False),
            (('primitives', 0, 'outputs', 0, 'triangle_count'), 2.0),
            (('primitives', 0, 'source_triangle_order_preserved'), 0),
            (('preservation', 'original_binary_prefix'), 1),
            (('limits', 'source_bytes'), float(report['limits']['source_bytes'])),
            (('output_primitives',), float(report['output_primitives'])),
            (('output_accessor_vertex_instances',), float(report['output_accessor_vertex_instances'])),
            (('accepted',), 0),
        ]
        for path, replacement in replacements:
            forged = deepcopy(report)
            parent = forged
            for key in path[:-1]:
                parent = parent[key]
            old = parent[path[-1]]
            with self.subTest(path=path):
                self.assertEqual(old, replacement)
                self.assertIsNot(type(old), type(replacement))
                parent[path[-1]] = replacement
                with self.assertRaises(ValueError):
                    verify_partition_glb_bytes(self.raw, out, reseal(forged))

    def test_matrix_and_trs_conflict_is_rejected(self):
        document, binary = unpack(self.raw)
        document['nodes'][1]['matrix'] = np.eye(4).ravel(order='F').tolist()
        malformed = pack(document, binary)
        with self.assertRaises(ValueError):
            partition_glb_bytes(malformed, declaration(malformed))

    def test_primitive_restart_sentinel_is_rejected_even_when_vertex_index_is_in_range(self):
        positions = np.zeros((65536, 3), '<f4')
        faces = np.array([[0, 1, 65535], [0, 1, 2]], '<u2')
        document = {'asset': {'version': '2.0'}, 'scene': 0,
                    'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
                    'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
                    'buffers': [{'byteLength': positions.nbytes + faces.nbytes}],
                    'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': positions.nbytes},
                                    {'buffer': 0, 'byteOffset': positions.nbytes, 'byteLength': faces.nbytes}],
                    'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 65536, 'type': 'VEC3'},
                                  {'bufferView': 1, 'componentType': 5123, 'count': 6, 'type': 'SCALAR'}]}
        malformed = pack(document, positions.tobytes() + faces.tobytes())
        request = declaration(malformed, binding={'node_index': 0, 'mesh_index': 0, 'primitive_index': 0},
                              pieces=[{'id': 'first', 'source_face_indices': [0]}, {'id': 'second', 'source_face_indices': [1]}])
        with self.assertRaises(ValueError):
            partition_glb_bytes(malformed, request)

    def test_resealed_receipt_cannot_upgrade_identity_or_replace_contract_fields(self):
        out, report = partition_glb_bytes(self.raw, declaration(self.raw))
        for key, value in (('semantic_identity', 'verified_lens_identity'),
                           ('limits', {'source_bytes': 1}), ('schema_version', True)):
            forged = deepcopy(report); forged[key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                verify_partition_glb_bytes(self.raw, out, reseal(forged))

    def test_unsupported_asset_version_and_required_extension_are_rejected(self):
        for alteration in ('asset_version', 'unknown_required_extension'):
            document, binary = unpack(self.raw)
            if alteration == 'asset_version':
                document['asset']['version'] = '1.0'
            else:
                document['extensionsUsed'] = ['TEST_unknown_required_extension']
                document['extensionsRequired'] = ['TEST_unknown_required_extension']
            raw = pack(document, binary)
            with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                partition_glb_bytes(raw, declaration(raw))

    def test_unsigned_int_custom_vertex_attribute_is_rejected(self):
        document, binary = unpack(self.raw)
        # The buffer range and count are otherwise valid, so rejection must not
        # depend on an out-of-bounds accessor incidentally hiding this type.
        document['accessors'].append({'bufferView': 0, 'byteOffset': 0, 'componentType': 5125,
                                      'count': 6, 'type': 'SCALAR'})
        document['meshes'][0]['primitives'][0]['attributes']['_CUSTOM'] = len(document['accessors']) - 1
        raw = pack(document, binary)
        with self.assertRaises(ValueError):
            partition_glb_bytes(raw, declaration(raw))

    def test_implicit_zero_custom_attribute_is_preserved_without_materializing_it(self):
        document, binary = unpack(self.raw)
        zero = {'componentType': 5126, 'count': 6, 'type': 'VEC2'}
        document['accessors'].append(zero)
        zero_index = len(document['accessors']) - 1
        document['meshes'][0]['primitives'][0]['attributes']['_IMPLICIT_ZERO'] = zero_index
        raw = pack(document, binary)
        out, report = partition_glb_bytes(raw, declaration(raw))
        after, new_binary = unpack(out)
        self.assertEqual(after['accessors'][zero_index], zero)
        self.assertNotIn('bufferView', after['accessors'][zero_index])
        self.assertEqual(new_binary[:len(binary)], binary)
        parts = after['meshes'][self.changed_node(after)['mesh']]['primitives']
        self.assertEqual([p['attributes']['_IMPLICIT_ZERO'] for p in parts[:2]], [zero_index, zero_index])
        self.assertEqual(verify_partition_glb_bytes(raw, out, report)['status'], 'verified')

    def test_vertex_capacity_rejects_before_first_position_allocation(self):
        with patch.dict('reconstruction.partition_glb.LIMITS', {'source_vertex_instances': 5}), \
                patch('reconstruction.partition_glb._float_accessor', side_effect=AssertionError('Position copy happened')) as decode:
            with self.assertRaises(ValueError):
                inspect_partition_source(self.raw)
            decode.assert_not_called()

    def test_shared_instance_capacity_rejects_before_excess_position_copy(self):
        from reconstruction.deform_glb import _float_accessor
        # Two primitives per instance, six vertices each: the third primitive
        # exceeds 12 even though it reuses exactly the same source accessor.
        with patch.dict('reconstruction.partition_glb.LIMITS', {'source_vertex_instances': 12}), \
                patch('reconstruction.partition_glb._float_accessor', wraps=_float_accessor) as decode:
            with self.assertRaises(ValueError):
                inspect_partition_source(self.raw)
            self.assertEqual(decode.call_count, 2)

    def test_declared_oversized_position_count_is_rejected_before_allocation(self):
        from reconstruction.partition_glb import LIMITS
        document, binary = unpack(self.raw)
        document['accessors'][0]['count'] = LIMITS['source_vertex_instances'] + 1
        raw = pack(document, binary)
        with patch('reconstruction.partition_glb._float_accessor', side_effect=AssertionError('Position copy happened')) as decode:
            with self.assertRaises(ValueError):
                inspect_partition_source(raw)
            decode.assert_not_called()

    def test_source_face_capacity_precedes_index_array_allocation(self):
        from reconstruction.deform_glb import _triangles
        # Four faces per primitive. The second primitive exceeds the cumulative
        # cap and must not allocate its decoded triangle array.
        with patch.dict('reconstruction.partition_glb.LIMITS', {'source_faces': 4}), \
                patch('reconstruction.partition_glb._triangles', wraps=_triangles) as decode:
            with self.assertRaises(ValueError):
                inspect_partition_source(self.raw)
            self.assertEqual(decode.call_count, 1)

    def test_declared_output_capacity_precedes_membership_array_allocation(self):
        for bound, limit in (('output_primitives', 4), ('output_accessor_vertex_instances', 24)):
            # Source has four primitive instances / 24 accessor vertex entries.
            # Splitting one instance adds a primitive and six vertex entries.
            with self.subTest(bound=bound), \
                    patch.dict('reconstruction.partition_glb.LIMITS', {bound: limit}), \
                    patch('reconstruction.partition_glb.np.full', side_effect=AssertionError('Membership allocation happened')) as allocate:
                with self.assertRaises(ValueError):
                    partition_glb_bytes(self.raw, declaration(self.raw))
                allocate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
