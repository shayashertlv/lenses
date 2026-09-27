"""Verified face pieces become fresh explicit optical-group hypotheses."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction.mesh import load_glb_bytes
from reconstruction.partition_glb import partition_glb_bytes
from reconstruction.partition_optical_groups import declarations_for_partition
from reconstruction.prepare_optical_groups import run_optical_group_preparation
from test_partition_glb import pack, unpack, sha, reseal


FRAME = {'id': 'source-world', 'units': 'meters', 'up_axis': '+Y', 'forward_axis': '+Z',
         'provenance': {'method': 'explicit test coordinate hypothesis'}}


def two_triangle_source():
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], '<f4')
    normals = np.tile(np.array([[0, 0, 1]], '<f4'), (4, 1))
    indices = np.array([[0, 1, 2], [1, 3, 2]], '<u2')
    binary = positions.tobytes() + normals.tobytes() + indices.tobytes()
    primitive = {'attributes': {'POSITION': 0, 'NORMAL': 1}, 'indices': 2, 'material': 1}
    invisible = deepcopy(primitive); invisible['material'] = 0
    document = {'asset': {'version': '2.0'}, 'scene': 0, 'scenes': [{'nodes': [0]}],
                'nodes': [{'mesh': 0, 'name': 'source-instance'}],
                'meshes': [{'primitives': [invisible, primitive]}],
                'materials': [{'alphaMode': 'BLEND', 'pbrMetallicRoughness': {'baseColorFactor': [1, 1, 1, 0]}},
                              {'name': 'unspecified source material'}],
                'buffers': [{'byteLength': len(binary)}],
                'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': positions.nbytes},
                                {'buffer': 0, 'byteOffset': positions.nbytes, 'byteLength': normals.nbytes},
                                {'buffer': 0, 'byteOffset': positions.nbytes + normals.nbytes, 'byteLength': indices.nbytes}],
                'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 4, 'type': 'VEC3'},
                              {'bufferView': 1, 'componentType': 5126, 'count': 4, 'type': 'VEC3'},
                              {'bufferView': 2, 'componentType': 5123, 'count': 6, 'type': 'SCALAR'}]}
    return pack(document, binary)


def partition(raw, primitive=1):
    declaration = {'schema_version': 1, 'source_sha256': sha(raw),
                   'provenance': {'method': 'explicit two-face partition'},
                   'partitions': [{'source_binding': {'node_index': 0, 'mesh_index': 0, 'primitive_index': primitive},
                                   'pieces': [{'id': 'second', 'source_face_indices': [1]},
                                              {'id': 'first', 'source_face_indices': [0]}]}]}
    return partition_glb_bytes(raw, declaration)


class PartitionOpticalGroupsTests(unittest.TestCase):
    def setUp(self):
        self.raw = two_triangle_source()
        self.output, self.receipt = partition(self.raw)

    def declarations(self, groups=None, **overrides):
        return declarations_for_partition(overrides.get('source', self.raw), overrides.get('output', self.output),
            overrides.get('receipt', self.receipt),
            groups if groups is not None else [{'group_id': 'one-lens-hypothesis', 'piece_ids': ['second', 'first']}],
            coordinate_frame=FRAME, provenance={'method': 'explicit test group hypothesis'})

    def test_fresh_sha_binding_and_visible_part_ordinal_after_alpha_zero_filter(self):
        result = self.declarations()
        self.assertEqual(result['source_sha256'], sha(self.output))
        self.assertNotEqual(result['source_sha256'], sha(self.raw))
        self.assertEqual(result['coordinate_frame'], FRAME)
        mesh = load_glb_bytes(self.output)
        self.assertEqual(len(mesh.parts), 2)
        members = {m['id']: m for m in result['groups'][0]['members']}
        self.assertEqual(members['first']['source_part_index'], 0)
        self.assertEqual(members['second']['source_part_index'], 1)
        self.assertEqual(members['first']['source_binding']['primitive_index'], 1)
        self.assertEqual(members['second']['source_binding']['primitive_index'], 2)
        for member in members.values():
            actual = mesh.parts[member['source_part_index']]
            self.assertEqual(member['source_binding'], {k: actual[k] for k in ('node_index', 'mesh_index', 'primitive_index')})

    def test_two_pieces_share_one_explicit_group_and_lineage_survives(self):
        result = self.declarations()
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual({m['id'] for m in result['groups'][0]['members']}, {'first', 'second'})
        lineage = result['provenance']['partition_lineage']
        self.assertEqual(lineage['original_source_sha256'], sha(self.raw))
        self.assertEqual(lineage['partitioned_source_sha256'], sha(self.output))
        self.assertEqual(lineage['receipt_sha256'], self.receipt['receipt_sha256'])
        self.assertEqual(lineage['verification_scope'], 'verified')
        self.assertEqual(result['provenance']['identity_status'], 'unverified')
        self.assertEqual(result['provenance']['unselected_piece_ids'], [])

    def test_unselected_piece_is_recorded_without_becoming_a_group_member(self):
        result = self.declarations([{'group_id': 'partial-hypothesis', 'piece_ids': ['second']}])
        self.assertEqual([m['id'] for m in result['groups'][0]['members']], ['second'])
        self.assertEqual(result['provenance']['unselected_piece_ids'], ['first'])

    def test_missing_repeated_empty_and_duplicate_group_ids_are_rejected(self):
        requests = [[], [{'group_id': 'g', 'piece_ids': []}],
                    [{'group_id': 'g', 'piece_ids': ['not-present']}],
                    [{'group_id': 'g', 'piece_ids': ['first', 'first']}],
                    [{'group_id': 'a', 'piece_ids': ['first']}, {'group_id': 'b', 'piece_ids': ['first']}],
                    [{'group_id': 'same', 'piece_ids': ['first']}, {'group_id': 'same', 'piece_ids': ['second']}]]
        for groups in requests:
            with self.subTest(groups=groups), self.assertRaises(ValueError):
                self.declarations(groups)

    def test_stale_source_or_output_is_rejected(self):
        for target, raw in (('source', self.raw), ('output', self.output)):
            document, binary = unpack(raw)
            document['asset']['generator'] = 'different captured asset'
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.declarations(**{target: pack(document, binary)})

    def test_rehashed_receipt_with_contradictory_piece_mapping_is_rejected(self):
        forged = deepcopy(self.receipt)
        source = next(r for r in forged['primitives'] if r['source_binding']['primitive_index'] == 1)
        source['outputs'][0]['source_face_ranges'] = [[1, 2]]
        with self.assertRaises(ValueError):
            self.declarations(receipt=reseal(forged))

    def test_alpha_zero_requested_piece_cannot_be_silently_dropped(self):
        output, receipt = partition(self.raw, primitive=0)
        with self.assertRaisesRegex(ValueError, 'absent.*inventory|cannot be dropped'):
            self.declarations(output=output, receipt=receipt)

    def test_actual_group_preparation_uses_partitioned_source_and_preserves_lineage(self):
        declarations = self.declarations()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            model = folder/'partitioned.glb'; model.write_bytes(self.output)
            with patch('reconstruction.prepare_optical_groups.implementation_manifest', return_value={'test': 'fixed'}):
                report = run_optical_group_preparation(model, folder/'prepared', grouping_mode='explicit_declarations',
                                                       declarations=declarations)
            self.assertEqual(report['status'], 'prepared_optical_group_candidate', report['reasons'])
            self.assertEqual(report['source_sha256'], sha(self.output))
            self.assertFalse(report['accepted'])
            self.assertEqual(report['semantic_identity'], 'unverified')
            self.assertEqual(len(report['groups']), 1)
            self.assertEqual(len(report['groups'][0]['primitives']), 2)
            export = json.loads((folder/'prepared'/report['export']['path']).read_text())
            identity = export['groups'][0]['identity']
            self.assertEqual(identity['status'], 'unverified')
            self.assertEqual(identity['provenance']['declaration_provenance'], declarations['provenance'])
            self.assertEqual(export['source_sha256'], sha(self.output))
            self.assertEqual(sum(m['triangles'] for m in export['groups'][0]['members']), 2)
            self.assertEqual((folder/'prepared'/report['source_snapshot']['path']).read_bytes(), self.output)


if __name__ == '__main__':
    unittest.main()
