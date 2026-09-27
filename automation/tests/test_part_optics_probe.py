"""Diagnostic normalization must retain topology and appearance bindings."""
import unittest
import numpy as np

from qa.part_optics_probe import canonicalize
from reconstruction.mesh import load_glb_bytes
from test_partition_glb import fixture, unpack, pack, accessor


class PartOpticsProbeTests(unittest.TestCase):
    def test_shared_mesh_instances_bake_independently_without_texture_changes(self):
        raw, _ = fixture(invisible=False)
        before, binary = unpack(raw)
        output, receipt = canonicalize(raw, yaw_degrees=25)
        after, new_binary = unpack(output)
        old, new = load_glb_bytes(raw), load_glb_bytes(output)
        np.testing.assert_array_equal(old.faces, new.faces)
        self.assertEqual(after['materials'], before['materials'])
        self.assertEqual(new_binary[:len(binary)], binary)
        self.assertAlmostEqual(np.ptp(new.vertices[:, 0]), .145, places=7)
        self.assertFalse(receipt['width_is_product_measurement'])
        self.assertNotEqual(new.parts[0]['mesh_index'], new.parts[1]['mesh_index'])
        for first, second in zip(old.parts, new.parts):
            p = before['meshes'][first['mesh_index']]['primitives'][0]
            q = after['meshes'][second['mesh_index']]['primitives'][0]
            np.testing.assert_array_equal(accessor(before, binary, p['attributes']['TEXCOORD_0']),
                                          accessor(after, new_binary, q['attributes']['TEXCOORD_0']))
            normal = accessor(after, new_binary, q['attributes']['NORMAL'])
            np.testing.assert_allclose(np.linalg.norm(normal, axis=1), 1, atol=1e-7)

    def test_mirrored_scene_is_not_silently_reoriented(self):
        raw, _ = fixture(invisible=False)
        doc, binary = unpack(raw)
        doc['nodes'][1]['scale'] = [-2, 3, 1]
        with self.assertRaisesRegex(ValueError, 'Mirrored'):
            canonicalize(pack(doc, binary), yaw_degrees=0)

    def test_source_morphs_are_refused(self):
        raw, _ = fixture(invisible=False)
        doc, binary = unpack(raw)
        doc['meshes'][0]['primitives'][0]['targets'] = [{'POSITION': 0}]
        with self.assertRaisesRegex(ValueError, 'morph'):
            canonicalize(pack(doc, binary))


if __name__ == '__main__':
    unittest.main()
