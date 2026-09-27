"""Physical scale from a stated frame width: positions to metres, then the bridge underside at the origin."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

from reconstruction.mesh import load_glb_bytes
from reconstruction.scale import bridge_origin, run_scale_stage, scale_glb
from test_initializer import glb_bytes


def unpack(raw):
    length = struct.unpack_from('<I', raw, 12)[0]
    doc = json.loads(raw[20:20 + length])
    offset = 20 + length + 8
    return doc, raw[offset:]


def frame_glb(boxes, transform=None):
    """One node and mesh per box (x0, x1, y0, y1, z0, z1) in source units; optional translation on the first node."""
    binary, views, accessors, meshes, nodes = bytearray(), [], [], [], []
    for index, (x0, x1, y0, y1, z0, z1) in enumerate(boxes):
        points = np.array([[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)], '<f4')
        faces = np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4], [2, 6, 7], [2, 7, 3], [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]], '<u4')
        for array, kind, component in ((points, 'VEC3', 5126), (faces.ravel(), 'SCALAR', 5125)):
            binary.extend(b'\0' * (-len(binary) % 4))
            views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes})
            binary.extend(array.tobytes())
            accessor = {'bufferView': len(views) - 1, 'componentType': component, 'count': int(array.size if kind == 'SCALAR' else len(array)), 'type': kind}
            if kind == 'VEC3':
                accessor.update(min=points.min(axis=0).tolist(), max=points.max(axis=0).tolist())
            accessors.append(accessor)
        meshes.append({'primitives': [{'attributes': {'POSITION': len(accessors) - 2}, 'indices': len(accessors) - 1}]})
        nodes.append({'mesh': index, 'name': f'part-{index}'})
    if transform:
        nodes[0]['translation'] = list(transform)
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(binary)}], 'bufferViews': views, 'accessors': accessors,
           'meshes': meshes, 'nodes': nodes, 'scenes': [{'nodes': list(range(len(nodes)))}], 'scene': 0}
    payload = json.dumps(doc).encode(); payload += b' ' * (-len(payload) % 4)
    binary = bytes(binary) + b'\0' * (-len(binary) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 28 + len(payload) + len(binary)) + struct.pack('<II', len(payload), 0x4E4F534A) + payload
            + struct.pack('<II', len(binary), 0x004E4942) + binary)


def column(x0, x1, y0, y1, z0, z1, step=.012):
    """A box sliced along Y so its vertices sample every millimetre of the scaled model, as a real mesh does."""
    edges = np.linspace(y0, y1, int(np.ceil((y1 - y0) / step)) + 1)
    return [(x0, x1, float(a), float(b), z0, z1) for a, b in zip(edges, edges[1:])]


# A rimmed pair in source units, 1.8 wide: lenses, a thin bridge bar above the nose gap, temples going to -Z,
# nose pads set back and outside the central column.
RIMMED = [(-.9, -.15, -.3, .3, 0., .05), (.15, .9, -.3, .3, 0., .05), *column(-.06, .06, .2, .3, 0., .05),
          (-.9, -.85, .1, .2, -1.5, 0.), (.85, .9, .1, .2, -1.5, 0.), (-.14, -.1, -.05, .05, -.15, -.1), (.1, .14, -.05, .05, -.15, -.1)]
# A shield: one lens across the centre with the nose piece attached below it, continuous in the central column.
SHIELD = [(-.9, .9, -.3, .3, 0., .05), *column(-.05, .05, -.3, .3, 0., .05), *column(-.05, .05, -.45, -.3, -.1, -.05), (-.9, -.85, .1, .2, -1.5, 0.), (.85, .9, .1, .2, -1.5, 0.)]


class ScaleTests(unittest.TestCase):
    def test_bridge_underside_becomes_the_origin_and_the_front_faces_plus_z(self):
        scaled, receipt = scale_glb(frame_glb(RIMMED), frame_width_mm=127)
        factor = receipt['factor_source_units_to_meters']
        placement = receipt['placement']
        self.assertEqual((placement['rule'], placement['via']), ('bridge_underside', 'baked_into_positions'))
        # The bar's underside is at y = .2 and its median depth at z = .025 in source units; x is already centred.
        np.testing.assert_allclose(placement['origin_mm_before_placement'], [0., .2 * factor * 1000, .025 * factor * 1000], atol=1e-6)
        np.testing.assert_allclose(placement['origin_mm_after_placement'], [0., 0., 0.], atol=1e-6)
        after = load_glb_bytes(scaled).vertices
        self.assertAlmostEqual(after[:, 0].max() - after[:, 0].min(), .127, places=7)
        self.assertAlmostEqual(after[:, 0].max() + after[:, 0].min(), 0., places=7)
        bar = after[(np.abs(after[:, 0]) < .01) & (after[:, 1] > -.001)]
        self.assertAlmostEqual(bar[:, 1].min(), 0., places=7, msg='the bridge underside sits at y = 0')
        self.assertAlmostEqual(np.median(bar[:, 2]), 0., places=7, msg='the bridge depth sits at z = 0')
        self.assertLess(after[:, 2].min(), -.1, 'temples extend toward -Z')
        self.assertAlmostEqual(after[:, 2].max(), .025 * factor, places=7, msg='the front stays a bar half-depth in front of the origin')
        runs = placement['column_runs']
        self.assertTrue(any(r['thin'] and r['at_front'] for r in runs))
        doc, _ = unpack(scaled)
        self.assertEqual(doc['extras']['lensesPlacement']['rule'], 'bridge_underside')
        self.assertEqual(receipt['placement']['translated_vertices'], receipt['scaled_vertices'])
        json.dumps(receipt, allow_nan=False)

    def test_a_continuous_central_column_falls_back_to_the_front_slab_centre(self):
        scaled, receipt = scale_glb(frame_glb(SHIELD), frame_width_mm=138)
        placement = receipt['placement']
        self.assertEqual(placement['rule'], 'front_slab_center')
        self.assertFalse(any(r['thin'] and r['at_front'] for r in placement['column_runs']), 'the shield column is one tall run')
        after = load_glb_bytes(scaled).vertices
        factor = receipt['factor_source_units_to_meters']
        self.assertAlmostEqual(after[:, 2].max(), .0025, places=7, msg='the front-most point sits 2.5 mm in front of the origin')
        slab = after[after[:, 2] >= after[:, 2].max() - .03]
        self.assertAlmostEqual(slab[:, 1].max() + slab[:, 1].min(), 0., places=7, msg='the front slab is vertically centred')
        np.testing.assert_allclose(placement['origin_mm_after_placement'], [0., 0., 0.], atol=1e-6)

    def test_transformed_nodes_move_through_their_root_translations(self):
        scaled, receipt = scale_glb(frame_glb(RIMMED, transform=[0., 0., 0.]), frame_width_mm=127)
        self.assertEqual(receipt['placement']['via'], 'root_node_translation')
        doc, _ = unpack(scaled)
        origin = np.asarray(receipt['placement']['origin_mm_before_placement']) / 1000.
        for node in doc['nodes']:
            np.testing.assert_allclose(node['translation'], -origin, atol=1e-9)
        np.testing.assert_allclose(receipt['placement']['origin_mm_after_placement'], [0., 0., 0.], atol=1e-6)
        with self.assertRaises(ValueError):
            bridge_origin(np.zeros((0, 3)))

    def test_positions_and_translations_scale_to_the_stated_width(self):
        # The fixture triangle spans one source unit along X; a translated second
        # instance moves with the scale while normals-free attributes stay as they are.
        raw = glb_bytes(lambda doc: (doc['nodes'].append({'mesh': 0, 'translation': [2, 0, 0], 'name': 'shifted'}),
                                     doc['scenes'][0]['nodes'].append(1)))
        before = load_glb_bytes(raw)
        scaled, receipt = scale_glb(raw, frame_width_mm=127)
        after = load_glb_bytes(scaled)
        self.assertAlmostEqual(receipt['source_extent_units_xyz'][0], 3.0)
        self.assertAlmostEqual(receipt['factor_source_units_to_meters'], .127 / 3.0)
        self.assertAlmostEqual(after.vertices[:, 0].max() - after.vertices[:, 0].min(), .127, places=7)
        origin = np.asarray(receipt['placement']['origin_mm_before_placement']) / 1000.
        self.assertEqual(receipt['placement']['rule'], 'front_slab_center')
        self.assertEqual(receipt['placement']['via'], 'root_node_translation', 'the shifted instance carries a transform')
        np.testing.assert_allclose(after.vertices, before.vertices * receipt['factor_source_units_to_meters'] - origin, rtol=1e-6, atol=1e-9)
        doc, binary = unpack(scaled)
        np.testing.assert_allclose(doc['nodes'][1]['translation'], np.array([2 * receipt['factor_source_units_to_meters'], 0, 0]) - origin, atol=1e-12)
        self.assertEqual(doc['accessors'][0]['min'], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(doc['accessors'][0]['max'][0], receipt['factor_source_units_to_meters'], places=7)
        self.assertEqual(doc['extras']['arbitrary_metadata'], 'preserve me exactly')
        self.assertEqual(doc['extras']['lensesPhysicalScale']['frame_width_mm'], 127.0)
        self.assertEqual(receipt['scaled_position_accessors'], [0])
        self.assertEqual(receipt['scaled_vertices'], 3)
        self.assertEqual(receipt['output_sha256'], __import__('hashlib').sha256(scaled).hexdigest())
        self.assertFalse(receipt['accepted'])
        json.dumps(receipt, allow_nan=False)

    def test_shared_accessor_is_scaled_once_and_invalid_widths_are_refused(self):
        raw = glb_bytes(lambda doc: doc['meshes'].append({'primitives': [{'attributes': {'POSITION': 0}}]}))
        scaled, receipt = scale_glb(raw, frame_width_mm=100)
        self.assertEqual(receipt['scaled_vertices'], 3)
        placed = load_glb_bytes(scaled).vertices
        self.assertAlmostEqual(placed[:, 0].max() - placed[:, 0].min(), .1, places=7)
        self.assertAlmostEqual(placed[:, 0].max() + placed[:, 0].min(), 0., places=7, msg='the lateral centre is the origin')
        for width in (0, -1, 10, 400, float('nan'), True, '127'):
            with self.subTest(width=width), self.assertRaises(ValueError):
                scale_glb(raw, frame_width_mm=width)
        flat = glb_bytes(lambda doc: doc['accessors'][0].update(componentType=5123))
        with self.assertRaises(ValueError):
            scale_glb(flat, frame_width_mm=127)

    def test_stage_without_frame_width_applies_nothing_and_says_so(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / 'initial.glb'
            model.write_bytes(glb_bytes())
            none = run_scale_stage(model, {}, Path(folder) / 'none')
            self.assertEqual((none['status'], none['application'], none['model']), ('not_applied', 'not_supplied', None))
            partial = run_scale_stage(model, {'temple_length': 140}, Path(folder) / 'partial')
            self.assertEqual(partial['application'], 'supplied_without_frame_width_unapplied')
            done = run_scale_stage(model, {'frame_width': 140, 'temple_length': 140}, Path(folder) / 'done')
            self.assertEqual((done['status'], done['application']), ('scaled', 'frame_width_applied_as_uniform_scale_to_meters'))
            written = (Path(folder) / 'done' / 'scaled.glb').read_bytes()
            self.assertEqual(done['model']['sha256'], __import__('hashlib').sha256(written).hexdigest())
            self.assertEqual(done['receipt']['source_sha256'], __import__('hashlib').sha256(model.read_bytes()).hexdigest())
            self.assertAlmostEqual(load_glb_bytes(written).vertices[:, 0].max(), .07, places=7)
            self.assertEqual(model.read_bytes(), glb_bytes(), 'the initial model keeps its bytes')


if __name__ == '__main__':
    unittest.main()
