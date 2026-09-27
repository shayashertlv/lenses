"""Composition evidence is bound, reversible and never an articulation label."""
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.camera import Camera
from reconstruction.composition_diagnostic import CompositionPolicy, _digest, project_composition, run_composition_diagnostic
from reconstruction.mesh import load_glb


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model(path, rear_z=-.2):
    binary = bytearray()
    views, accessors, meshes, nodes = [], [], [], []
    for i, (width, z, role, name) in enumerate([(1.6, .2, 'lens', 'lens'), (.4, rear_z, 'frame', 'temple_left')]):
        p = np.array([[-width/2, -.6, z], [width/2, -.6, z], [width/2, .6, z], [-width/2, .6, z]], '<f4')
        f = np.array([0, 1, 2, 0, 2, 3], '<u4')
        ids = []
        for array, kind, dtype in [(p, 'VEC3', 5126), (f, 'SCALAR', 5125)]:
            offset = len(binary)
            binary.extend(array.tobytes())
            views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': array.nbytes})
            accessors.append({'bufferView': len(views)-1, 'componentType': dtype, 'count': len(array), 'type': kind})
            ids.append(len(accessors)-1)
        meshes.append({'primitives': [{'attributes': {'POSITION': ids[0]}, 'indices': ids[1], 'material': i}]})
        nodes.append({'mesh': i, 'name': name, 'extras': {'partRole': role}})
    document = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(binary)}], 'bufferViews': views,
                'accessors': accessors, 'meshes': meshes, 'nodes': nodes, 'materials': [{'name': 'lens'}, {'name': 'temple_left'}],
                'scene': 0, 'scenes': [{'nodes': [0, 1]}]}
    raw = json.dumps(document).encode()
    raw += b' '*((-len(raw)) % 4)
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 28+len(raw)+len(binary))+struct.pack('<II', len(raw), 0x4e4f534a)+raw+
                     struct.pack('<II', len(binary), 0x004e4942)+binary)


class CompositionDiagnosticTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.model = self.root/'model.glb'
        model(self.model)
        self.photo = self.root/'photo.png'
        rgb = np.full((100, 140, 3), 220, np.uint8)
        rgb[24:78, 65:75] = 40
        rgb[31:36, 33:50] = 20  # Structure unrelated to the modeled rear strip.
        Image.fromarray(rgb).save(self.photo)
        self.camera = Camera(0, 0, 0, 0, 45, 70, 50)
        projection = {'candidate_sha256': sha(self.model), 'camera': self.camera.to_dict(),
                      'camera_sha256': _digest(self.camera.to_dict()), 'working_size': [140, 100],
                      'normalization': {'center': [0, 0, 0], 'extent': 1}}
        self.region = {'schema_version': 1, 'candidate_sha256': sha(self.model), 'photos': [
            {'id': 'front', 'source': str(self.photo), 'source_sha256': sha(self.photo), 'image_size': [140, 100],
             'candidate_projection': projection, 'regions': []}]}
        self.regions = self.root/'regions.json'
        self.save()

    def save(self):
        self.regions.write_text(json.dumps(self.region))

    def run_stage(self, **kwargs):
        return run_composition_diagnostic(self.model, self.regions, self.root/'out', **kwargs)

    def test_opaque_entry_order_keeps_rear_and_rejects_front_and_tie(self):
        for z, behind, visible, tied in [(-.2, True, True, False), (.4, False, False, False), (.2, False, False, True)]:
            with self.subTest(z=z):
                model(self.model, z)
                fields = project_composition(load_glb(self.model), self.camera, (100, 140), {'center': [0, 0, 0], 'extent': 1})
                self.assertEqual(bool(fields['opaque_behind_optical'][50, 70]), behind)
                self.assertEqual(bool(fields['visible_optical'][50, 70]), visible)
                self.assertEqual(bool(fields['depth_tie'][50, 70]), tied)

    def test_actual_pipeline_keeps_sources_and_never_identifies_fold_or_acceptance(self):
        before = {p: p.read_bytes() for p in (self.model, self.photo, self.regions)}
        report = self.run_stage()
        self.assertFalse(report['accepted'])
        self.assertEqual(report['quality_verdict'], 'unmeasured')
        self.assertEqual(report['articulation_identification'], 'unmeasured')
        view = report['views'][0]
        self.assertGreater(view['masks']['opaque_behind_optical']['pixels'], 0)
        self.assertGreater(view['unexplained_optical_region_edge_pixels'], 0)
        self.assertEqual(view['pivot_status'], 'unverified_not_supplied')
        self.assertIsNone(view['selected_region_alternative'])
        self.assertTrue((self.root/'out'/view['overlay']['path']).is_file())
        for p, raw in before.items():
            self.assertEqual(p.read_bytes(), raw)
        with self.assertRaisesRegex(ValueError, 'empty'):
            self.run_stage()

    def test_every_mask_alternative_is_preserved_even_empty_or_duplicate(self):
        directory = self.root/'front'/'region'
        directory.mkdir(parents=True)
        masks = []
        for i in range(3):
            path = directory/f'mask-{i}.png'
            mask = np.zeros((100, 140), np.uint8)
            if i:
                mask[20:80, 30:100] = 255
            Image.fromarray(mask).save(path)
            masks.append({'decoder_index': i, 'mask': {'path': path.name, 'sha256': sha(path)}})
        self.region['photos'][0]['regions'] = [{'id': 'region', 'directory': 'region', 'alternatives': [masks], 'hypotheses': []}]
        self.save()
        report = self.run_stage()
        rows = report['views'][0]['all_region_alternatives']
        self.assertEqual([r['alternative'] for r in rows], [0, 1, 2])
        self.assertEqual(rows[0]['mask_pixels'], 0)
        self.assertEqual(rows[1]['sha256'], rows[2]['sha256'])

    def test_model_photo_camera_and_mask_tampering_fail_before_write(self):
        original = json.loads(json.dumps(self.region))
        mutations = [lambda r: r.update(candidate_sha256='0'*64),
                     lambda r: r['photos'][0].update(source_sha256='0'*64),
                     lambda r: r['photos'][0]['candidate_projection']['camera'].update(yaw=30),
                     lambda r: r['photos'][0]['candidate_projection'].update(candidate_sha256='0'*64)]
        for mutate in mutations:
            self.region = json.loads(json.dumps(original))
            mutate(self.region)
            self.save()
            with self.assertRaises(ValueError):
                self.run_stage()
            self.assertFalse((self.root/'out').exists())

    def test_missing_optical_identity_reports_insufficient_evidence(self):
        # Source metadata is only a candidate prior; removing it cannot turn all
        # dark pixels into lenses or rear temples.
        mesh = load_glb(self.model)
        for part in mesh.parts:
            part.update(declared_role='frame', name='unknown', material='unknown')
        fields = project_composition(mesh, self.camera, (100, 140), {'center': [0, 0, 0], 'extent': 1})
        self.assertFalse(fields['optical'].any())
        self.assertEqual(fields['named_rear_parts'], [])
        self.assertIsNone(fields['posterior_plane_source_z'])

    def test_duplicate_views_and_invalid_policy_fail_before_output(self):
        second = json.loads(json.dumps(self.region['photos'][0]))
        second['id'] = 'other'
        self.region['photos'].append(second)
        self.save()
        with self.assertRaisesRegex(ValueError, 'Duplicate photograph'):
            self.run_stage()
        self.assertFalse((self.root/'out').exists())
        for args in ({'maximum_boundary_probes': True}, {'image_edge_sigma_native_px': float('nan')}, {'residual_fraction_threshold': 1.}):
            with self.assertRaises(ValueError):
                CompositionPolicy(**args)

    def test_mask_escape_hash_and_decoded_grid_fail_before_output(self):
        directory = self.root/'front'/'region'
        directory.mkdir(parents=True)
        path = directory/'mask.png'
        Image.fromarray(np.zeros((100, 140), np.uint8)).save(path)
        candidate = {'id': 'region', 'directory': 'region', 'alternatives': [[
            {'decoder_index': 0, 'mask': {'path': 'mask.png', 'sha256': sha(path)}}]], 'hypotheses': []}
        self.region['photos'][0]['regions'] = [candidate]
        for bad_path, bad_hash in [('mask.png', '0'*64), ('../../../../outside.png', sha(path))]:
            candidate['alternatives'][0][0]['mask'] = {'path': bad_path, 'sha256': bad_hash}
            self.save()
            with self.assertRaises(ValueError):
                self.run_stage()
            self.assertFalse((self.root/'out').exists())
        Image.fromarray(np.zeros((50, 70), np.uint8)).save(path)
        candidate['alternatives'][0][0]['mask'] = {'path': 'mask.png', 'sha256': sha(path)}
        self.save()
        with self.assertRaisesRegex(ValueError, 'pixel grid'):
            self.run_stage()


if __name__ == '__main__':
    unittest.main()
