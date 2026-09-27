"""Verified camera transfer to a face-partitioned candidate."""
from copy import deepcopy
import hashlib
import json
import unittest

from reconstruction.candidate_cameras import METHOD, transfer_cameras_to_partition
from reconstruction.partition_glb import partition_glb_bytes
from test_partition_glb import pack, unpack, sha
from test_partition_optical_groups import two_triangle_source


def refinement_for(raw):
    return {'schema_version': 1, 'status': 'proposal_exported', 'source_sha256': sha(raw),
            'normalization': {'center': [0.5, 0.5, 0.0], 'extent': 1.0},
            'views': [{'view_id': 'front', 'source_sha256': '0'*64, 'image_size_original': [8, 8],
                       'image_size_working': [8, 8],
                       'camera_fit': {'camera': {'yaw': 0., 'pitch': 0., 'roll': 0., 'perspective': 0.,
                                                 'scale': 4., 'center_x': 4., 'center_y': 4.}}}]}


def partition(raw):
    declaration = {'schema_version': 1, 'source_sha256': sha(raw), 'provenance': {'method': 'test'},
                   'partitions': [{'source_binding': {'node_index': 0, 'mesh_index': 0, 'primitive_index': 1},
                                   'pieces': [{'id': 'a', 'source_face_indices': [1]}, {'id': 'b', 'source_face_indices': [0]}]}]}
    return partition_glb_bytes(raw, declaration)


class CandidateCameraTransferTests(unittest.TestCase):
    def test_transfer_rebinds_cameras_with_explicit_provenance(self):
        raw = two_triangle_source()
        output, receipt = partition(raw)
        refinement = refinement_for(raw)
        result = transfer_cameras_to_partition(refinement, raw, output, receipt, partitioned_model_path='x/partitioned.glb')
        json.dumps(result, allow_nan=False)
        self.assertEqual(result['source_sha256'], hashlib.sha256(output).hexdigest())
        self.assertEqual(result['camera_transfer']['original_source_sha256'], sha(raw))
        self.assertEqual(result['camera_transfer']['method'], METHOD)
        self.assertEqual(result['camera_transfer']['triangle_count'], 2)
        self.assertEqual(result['views'], refinement['views'])
        self.assertEqual(result['normalization'], refinement['normalization'])
        self.assertEqual(refinement['source_sha256'], sha(raw), 'the input report is not mutated')

    def test_exported_refinement_binding_is_accepted_and_recorded(self):
        raw = two_triangle_source()
        output, receipt = partition(raw)
        refinement = refinement_for(raw)
        refinement.update(source_sha256='1'*64, status='proposal_exported', rerender_nonregression=True,
                          export={'output_sha256': sha(raw)})
        result = transfer_cameras_to_partition(refinement, raw, output, receipt, partitioned_model_path='p')
        self.assertEqual(result['camera_transfer']['refinement_binding'], 'exported_proposal')
        self.assertEqual(result['source_sha256'], hashlib.sha256(output).hexdigest())
        refinement['rerender_nonregression'] = False
        with self.assertRaises(ValueError):
            transfer_cameras_to_partition(refinement, raw, output, receipt, partitioned_model_path='p')

    def test_wrong_original_changed_geometry_and_broken_receipt_are_rejected(self):
        raw = two_triangle_source()
        output, receipt = partition(raw)
        other = refinement_for(raw); other['source_sha256'] = '1'*64
        with self.assertRaises(ValueError):
            transfer_cameras_to_partition(other, raw, output, receipt, partitioned_model_path='p')
        bad_receipt = deepcopy(receipt); bad_receipt['receipt_sha256'] = '2'*64
        with self.assertRaises(ValueError):
            transfer_cameras_to_partition(refinement_for(raw), raw, output, bad_receipt, partitioned_model_path='p')
        # Move one vertex in the partitioned bytes: the receipt no longer verifies.
        document, binary = unpack(output)
        moved = bytearray(binary); moved[0:4] = b'\x00\x00\x80\x3f'
        with self.assertRaises(ValueError):
            transfer_cameras_to_partition(refinement_for(raw), raw, pack(document, bytes(moved)), receipt, partitioned_model_path='p')
        missing = refinement_for(raw); del missing['views'][0]['camera_fit']
        with self.assertRaises(ValueError):
            transfer_cameras_to_partition(missing, raw, output, receipt, partitioned_model_path='p')


if __name__ == '__main__':
    unittest.main()
