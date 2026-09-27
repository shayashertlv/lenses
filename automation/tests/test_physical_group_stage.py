"""End-to-end physical-group stage on a synthetic scene with a controlled aperture engine."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.camera import Camera
from reconstruction.component_scene import load_component_scene
from reconstruction.lens_asset import _pack_glb
from reconstruction.partition_stage import run_component_inventory
from reconstruction.physical_group_stage import run_physical_group_stage
from test_optical_group_raster import boxes


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_scene(path, specs):
    """One node/mesh/primitive per closed box; a transmitting lens material on the first two."""
    mesh, _ = boxes(specs)
    lens = {'name': 'lens', 'extensions': {'KHR_materials_transmission': {'transmissionFactor': 1.}},
            'pbrMetallicRoughness': {'baseColorFactor': [1, 1, 1, 1]}}
    frame = {'name': 'frame', 'pbrMetallicRoughness': {'baseColorFactor': [.1, .1, .1, 1]}}
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': 0}], 'bufferViews': [], 'accessors': [],
           'materials': [lens, frame], 'meshes': [], 'nodes': [], 'scenes': [{'nodes': list(range(len(specs)))}], 'scene': 0,
           'extensionsUsed': ['KHR_materials_transmission']}
    binary = bytearray()

    def append(array, kind, component):
        binary.extend(b'\0'*(-len(binary) % 4))
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes})
        binary.extend(array.tobytes())
        accessor = {'bufferView': len(doc['bufferViews'])-1, 'componentType': component,
                    'count': array.size if kind == 'SCALAR' else len(array), 'type': kind}
        if kind == 'VEC3':
            accessor.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        doc['accessors'].append(accessor)
        return len(doc['accessors'])-1
    for i, spec in enumerate(specs):
        p = mesh.vertices[8*i:8*i+8].astype('<f4')
        n = np.tile([0., 0., 1.], (8, 1)).astype('<f4')
        f = (mesh.faces[12*i:12*i+12]-8*i).astype('<u4')
        doc['meshes'].append({'primitives': [{'attributes': {'POSITION': append(p, 'VEC3', 5126), 'NORMAL': append(n, 'VEC3', 5126)},
                                              'indices': append(f, 'SCALAR', 5125), 'material': 0 if spec[6] >= 0 else 1}]})
        doc['nodes'].append({'mesh': i, 'name': f'box-{i}', 'extras': {'partRole': 'lens' if spec[6] >= 0 else 'frame'}})
    doc['buffers'][0]['byteLength'] = len(binary)
    path.write_bytes(_pack_glb(doc, bytes(binary)))


class FakeApertureEngine:
    """Deterministic apertures: the lens square in both crop policies."""

    def describe(self):
        return {'kind': 'FakeApertureEngine', 'policy': 'lens square rows/cols 8..56; crop known domain 4..60'}

    def propose(self, rgb):
        h, w = rgb.shape[:2]
        mask = np.zeros((h, w), bool); mask[8:57, 8:57] = True
        full_known = np.ones((h, w), bool)
        crop_known = np.zeros((h, w), bool); crop_known[4:60, 4:60] = True
        return {'method': 'fake', 'size_xy': [w, h], 'contrast': None,
                'variants': {'full': {'crop_xyxy_exclusive': [0, 0, w, h], 'mask': mask, 'known_domain': full_known,
                                      'grid_positive_pixels': 1, 'native_positive_pixels': int(mask.sum()), 'logit_range': [0, 1]},
                             'contrast_crop': {'crop_xyxy_exclusive': [4, 4, 60, 60], 'mask': mask & crop_known, 'known_domain': crop_known,
                                               'grid_positive_pixels': 1, 'native_positive_pixels': int((mask & crop_known).sum()), 'logit_range': [0, 1]}}}


def refinement_for(model, photos):
    return {'schema_version': 1, 'status': 'controlled_camera_fixture', 'source_sha256': sha(model),
            'normalization': {'center': [0, 0, 0], 'extent': 1},
            'views': [{'view_id': photo['id'], 'source_sha256': photo['sha256'], 'image_size_original': [64, 64],
                       'image_size_working': [64, 64],
                       'camera_fit': {'camera': Camera(0 if photo['id'] == 'front' else 180, 0, 0, 0, 40, 32, 32).to_dict()}}
                      for photo in photos]}


class TouchingApertureEngine:
    """Two lens squares: the full proposal merges them into one blob, the crop keeps them apart.

    The view is read from the photo's top-left pixel (front 150, back 151) so
    the fake can mirror the boxes the way the 180-degree camera does.
    """

    def describe(self):
        return {'kind': 'TouchingApertureEngine', 'policy': 'merged full blob; separated crop blobs'}

    def propose(self, rgb):
        h, w = rgb.shape[:2]
        front = int(rgb[0, 0, 0]) == 150
        big = (8, 28) if front else (36, 56)      # box A columns
        small = (36, 44) if front else (20, 28)   # box B columns
        merged = np.zeros((h, w), bool); merged[8:57, min(big[0], small[0]):max(big[1], small[1])] = True
        apart = np.zeros((h, w), bool); apart[8:57, big[0]:big[1]] = True; apart[8:57, small[0]:small[1]] = True
        known = np.ones((h, w), bool)
        return {'method': 'fake', 'size_xy': [w, h], 'contrast': None,
                'variants': {'full': {'crop_xyxy_exclusive': [0, 0, w, h], 'mask': merged, 'known_domain': known,
                                      'grid_positive_pixels': 1, 'native_positive_pixels': int(merged.sum()), 'logit_range': [0, 1]},
                             'contrast_crop': {'crop_xyxy_exclusive': [0, 0, w, h], 'mask': apart, 'known_domain': known,
                                               'grid_positive_pixels': 1, 'native_positive_pixels': int(apart.sum()), 'logit_range': [0, 1]}}}


class PhysicalGroupStageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.model = self.folder/'source.glb'
        # Lens A, a slightly smaller second shell just behind its front face
        # (0.02 in front view, 0.04 in back view; no coincident side faces), and
        # a frame strip beside the lens square.
        write_scene(self.model, [(-.6, .6, -.6, .6, .2, .3, 0), (-.55, .55, -.55, .55, .24, .28, 1), (.65, .75, -.6, .6, -.1, .1, -1)])
        self.photos = []
        for name, shade in (('front', 150), ('back', 151)):
            pixels = np.full((64, 64, 3), 245, np.uint8); pixels[8:57, 8:57] = [shade, 120, 100]
            path = self.folder/f'{name}.png'; Image.fromarray(pixels).save(path)
            self.photos.append({'id': name, 'view': name, 'path': str(path), 'sha256': sha(path)})
        self.refinement = refinement_for(self.model, self.photos)

    def test_component_scene_round_trips_the_inventory(self):
        run_component_inventory(self.model, self.folder/'inventory')
        scene = load_component_scene(self.folder/'inventory'/'report.json')
        self.assertEqual(len(scene['component_table']), 3)
        self.assertEqual(scene['source_sha256'], sha(self.model))
        self.assertEqual(len(scene['mesh'].faces), 36)
        self.assertEqual(sorted(scene['primitive_labels']), [(0, 0, 0), (1, 1, 0), (2, 2, 0)])
        self.assertAlmostEqual(scene['reference_extent'], 1.35)

    def test_stage_ranks_the_merged_shells_first_and_bridges_it(self):
        report = run_physical_group_stage(self.model, self.refinement, self.photos, self.folder/'stage', aperture_engine=FakeApertureEngine())
        json.dumps(report, allow_nan=False)
        self.assertEqual(report['status'], 'hypotheses_bridged')
        self.assertEqual(report['hypothesis_count'], 2)
        by_index = {h['index']: h for h in report['hypotheses']}
        merged = next(h for h in report['hypotheses'] if any(len(g['members']) == 2 for g in h['consensus_optical_groups']))
        split = next(h for h in report['hypotheses'] if h is not merged)
        self.assertEqual([g['members'] for g in merged['consensus_optical_groups']], [[0, 1]])
        self.assertEqual(sorted(g['members'][0] for g in split['consensus_optical_groups']), [0, 1])
        self.assertEqual(merged['composition_rank'], 0, 'the merged hypothesis has no stacked pixels and ranks first')
        self.assertGreater(merged['composition_score'], split['composition_score'])
        front_split = next(r for r in split['composition_table'] if r['view_id'] == 'front' and r['interpretation_id'] == 'full')
        self.assertGreater(front_split['excluded_fraction'], .7, 'two optical groups on most lens rays are stacked')
        front_merged = next(r for r in merged['composition_table'] if r['view_id'] == 'front' and r['interpretation_id'] == 'full')
        self.assertGreater(front_merged['clean_transmission_fraction'], .9)
        self.assertEqual(report['selected_hypothesis']['index'], merged['index'])
        registration = report['selected_hypothesis']['view_registration']
        self.assertEqual(set(registration), {g['group_id'] for g in merged['consensus_optical_groups']})
        for views in registration.values():
            self.assertEqual(set(views), {'front', 'back'})
            self.assertTrue(all(v['fit_eligible'] and v['inside_fraction'] >= .8 for v in views.values()))
        self.assertEqual(merged['bridge']['status'], 'bridge_executed')
        self.assertEqual(merged['bridge']['prepared_groups'], 1)
        self.assertEqual(merged['bridge']['partition_verification'], 'verified')
        cameras = json.loads((self.folder/'stage'/merged['bridge']['cameras']['path']).read_bytes())
        self.assertEqual(cameras['camera_transfer']['original_source_sha256'], sha(self.model))
        self.assertEqual(cameras['source_sha256'], merged['bridge']['partitioned_model']['sha256'])
        self.assertEqual(split['bridge']['status'], 'bridge_executed', 'every distinct declaration is bridged and retained')
        inference = json.loads((self.folder/'stage'/'inference.json').read_bytes())
        frame = next(g for h in inference['hypotheses'] if h['index'] == merged['index'] for g in h['groups'] if 2 in g['members'])
        self.assertEqual(frame['role_consensus'], 'non_optical_evidence')
        self.assertTrue((self.folder/'stage'/merged['folder']/'front-rays.png').exists())
        self.assertFalse(report['accepted'])

    def test_branch_dependent_lens_gets_an_optimistic_entry_that_the_rank_selects(self):
        # Box A is a wide lens, box B a narrow one beside it. Under the merged
        # full proposal B explains too little of the single blob (contained
        # minor); under the separated crop proposal it covers its own blob.
        write_scene(self.model, [(-.6, -.1, -.6, .6, .2, .3, 0), (.1, .3, -.6, .6, .2, .3, 1), (.65, .75, -.6, .6, -.1, .1, -1)])
        photos = []
        for name, shade in (('front', 150), ('back', 151)):
            pixels = np.full((64, 64, 3), 245, np.uint8); pixels[8:57, 8:57] = [shade, 120, 100]; pixels[0, 0] = [shade, 0, 0]
            path = self.folder/f'{name}-touching.png'; Image.fromarray(pixels).save(path)
            photos.append({'id': name, 'view': name, 'path': str(path), 'sha256': sha(path)})
        report = run_physical_group_stage(self.model, refinement_for(self.model, photos), photos, self.folder/'variants',
                                          aperture_engine=TouchingApertureEngine())
        json.dumps(report, allow_nan=False)
        inference = json.loads((self.folder/'variants'/'inference.json').read_bytes())
        self.assertEqual(len(inference['hypotheses']), 1, 'separated boxes at one depth form one membership hypothesis')
        narrow = next(g for g in inference['hypotheses'][0]['groups'] if g['members'] == [1])
        self.assertEqual(narrow['role_consensus'], 'branch_dependent')
        self.assertEqual({r['branch_id']: r['role'] for r in narrow['roles_by_branch']}['all-full'], 'unresolved')
        self.assertEqual({r['branch_id']: r['role'] for r in narrow['roles_by_branch']}['all-contrast_crop'], 'optical_candidate')
        self.assertEqual(report['hypothesis_count'], 2)
        by_variant = {h['role_variant']: h for h in report['hypotheses']}
        self.assertEqual(by_variant['consensus']['membership_hypothesis'], 0)
        self.assertEqual(by_variant['branch_optimistic']['membership_hypothesis'], 0)
        self.assertEqual(by_variant['branch_optimistic']['promoted_groups'], [narrow['group_id']])
        self.assertEqual([g['members'] for g in by_variant['consensus']['consensus_optical_groups']], [[0]])
        self.assertEqual([g['members'] for g in by_variant['branch_optimistic']['consensus_optical_groups']], [[0], [1]])
        self.assertEqual(by_variant['branch_optimistic']['composition_rank'], 0, 'the narrow lens adds usable rays without contamination')
        self.assertEqual(report['selected_hypothesis']['index'], by_variant['branch_optimistic']['index'])
        self.assertEqual(by_variant['branch_optimistic']['bridge']['status'], 'bridge_executed')
        self.assertEqual(by_variant['branch_optimistic']['bridge']['prepared_groups'], 2)
        self.assertEqual(by_variant['consensus']['bridge']['status'], 'bridge_executed', 'the consensus reading is bridged and retained too')
        self.assertNotEqual(by_variant['consensus']['index'], by_variant['branch_optimistic']['index'])

    def test_shared_contact_faces_are_interior_not_coincident_surfaces(self):
        from reconstruction.mesh import load_glb
        # A lens box and a wall box glued to its back: their shared square is an
        # interior contact (two faces on each side). Without that rule the two
        # would be one coincident surface from the finest rung on.
        write_scene(self.model, [(-.6, .6, -.6, .6, .2, .3, 0), (-.6, .6, -.6, .6, .1, .2, -1), (.65, .75, -.6, .6, -.1, .1, -1)])
        photos = []
        for name, shade in (('front', 150), ('back', 151)):
            pixels = np.full((64, 64, 3), 245, np.uint8); pixels[8:57, 8:57] = [shade, 120, 100]
            path = self.folder/f'{name}-wall.png'; Image.fromarray(pixels).save(path)
            photos.append({'id': name, 'view': name, 'path': str(path), 'sha256': sha(path)})
        report = run_physical_group_stage(self.model, refinement_for(self.model, photos), photos, self.folder/'wall', aperture_engine=FakeApertureEngine())
        json.dumps(report, allow_nan=False)
        contact = report['interior_contact']
        self.assertEqual(contact['interior_faces'], 4)
        self.assertEqual(contact['part_pairs'], [{'parts': [0, 1], 'faces': 4}])
        inference = json.loads((self.folder/'wall'/'inference.json').read_bytes())
        for h in inference['hypotheses']:
            self.assertFalse(any({0, 1} <= set(g['members']) for g in h['groups']), 'the lens and the wall never merge once their contact is interior')
        self.assertEqual(report['status'], 'hypotheses_bridged')
        selected = report['selected_hypothesis']
        folder = self.folder/'wall'/Path(selected['preparation_report']).parent.parent
        declarations = json.loads((folder/'partition-declarations.json').read_bytes())
        interior = {tuple(p['source_binding'].values()): [piece for piece in p['pieces'] if piece.get('declared_role') == 'interior_contact']
                    for p in declarations['partitions']}
        self.assertEqual({k: len(v) for k, v in interior.items() if v}, {(0, 0, 0): 1, (1, 1, 0): 1}, 'both touching primitives get one interior piece')
        self.assertEqual(sorted(len(v[0]['source_face_indices']) for v in interior.values() if v), [2, 2])
        prepared = json.loads((self.folder/'wall'/selected['preparation_report']).read_bytes())
        receipt = json.loads((self.folder/'wall'/Path(selected['preparation_report']).parent/prepared['export']['path']).read_bytes()) if 'export' in prepared else None
        candidate = load_glb(self.folder/'wall'/Path(selected['preparation_report']).parent/prepared['model']['path'])
        partitioned = load_glb(self.folder/'wall'/selected['partitioned_model']['path'])
        self.assertEqual(len(partitioned.faces) - len(candidate.faces), 4, 'the interior pieces are omitted from the runtime candidate')
        if receipt is not None:
            self.assertEqual(len(receipt['interior_contact_parts']), 2)

    def test_two_halves_of_one_lens_are_reunited_and_selected(self):
        from reconstruction.physical_groups import PhysicalGroupPolicy
        # A lens box cut in two at x = 0: the halves share a square (four interior faces) and their outer
        # surfaces continue across it, so a reunited reading [0, 1] joins the split one and, composing
        # identically, wins on fewer declared groups. The frame box stays apart.
        write_scene(self.model, [(-.6, 0, -.6, .6, .2, .3, 0), (0, .6, -.6, .6, .2, .3, 0), (.65, .75, -.6, .6, -.1, .1, -1)])
        photos = []
        for name, shade in (('front', 150), ('back', 151)):
            pixels = np.full((64, 64, 3), 245, np.uint8); pixels[8:57, 8:57] = [shade, 120, 100]
            path = self.folder/f'{name}-halves.png'; Image.fromarray(pixels).save(path)
            photos.append({'id': name, 'view': name, 'path': str(path), 'sha256': sha(path)})
        report = run_physical_group_stage(self.model, refinement_for(self.model, photos), photos, self.folder/'halves',
                                          aperture_engine=FakeApertureEngine(), policy=PhysicalGroupPolicy(cut_continuity_minimum_edges=1))
        json.dumps(report, allow_nan=False)
        self.assertEqual(report['interior_contact']['interior_faces'], 4)
        continuity = report['cut_continuity']
        self.assertEqual(continuity['body_edges'], [[0, 1]])
        self.assertEqual((continuity['pairs'][0]['boundary_edges'], continuity['pairs'][0]['continuous_fraction']), (4, 1.0))
        inference = json.loads((self.folder/'halves'/'inference.json').read_bytes())
        self.assertEqual(inference['body_edges'], [[0, 1]])
        reunited = [h for h in inference['hypotheses'] if h['cut_reunion']]
        self.assertEqual(len(reunited), 1)
        self.assertEqual([g['members'] for g in reunited[0]['groups'] if g['role_consensus'] == 'optical_candidate'], [[0, 1]])
        self.assertEqual(report['ranking'], 'composition_rank_v4')
        selected = report['selected_hypothesis']
        chosen = next(h for h in report['hypotheses'] if h['index'] == selected['index'])
        self.assertTrue(chosen['cut_reunion'])
        self.assertEqual([g['members'] for g in chosen['consensus_optical_groups']], [[0, 1]])
        self.assertEqual(selected['composition_rank'], 0)
        split = next(h for h in report['hypotheses'] if not h['cut_reunion'] and h['role_variant'] == 'consensus')
        self.assertEqual([g['members'] for g in split['consensus_optical_groups']], [[0], [1]])
        self.assertEqual(split['composition_detail']['contamination_band_index'], chosen['composition_detail']['contamination_band_index'])
        self.assertEqual(split['bridge']['status'], 'bridge_executed', 'the split reading is bridged and retained')
        self.assertEqual(chosen['bridge']['prepared_groups'], 1)

    def test_rank_prefers_low_contamination_over_apparent_coverage(self):
        from reconstruction.physical_group_stage import composition_rank_key
        def row(view, clean, rear, inside, outside):
            return {'view_id': view, 'interpretation_id': 'full', 'clean_transmission_fraction': clean, 'rear_content_fraction': rear,
                    'contamination_fraction': outside/(outside+inside), 'optical_inside_aperture_known_pixels': inside,
                    'optical_outside_aperture_known_pixels': outside}
        # Struts kept separate: lower usable share, little of the group outside the aperture.
        separate = [row('front', .65, .18, 9800, 200), row('angled', .62, .18, 8900, 290)]
        # Struts absorbed: apparently more usable rays, but 12 percent of the group is outside.
        absorbed = [row('front', .78, .165, 9900, 1163), row('angled', .76, .185, 8800, 1206)]
        stacked = [row('front', 0., .08, 10000, 7), row('angled', 0., .09, 9000, 83)]
        merged = [row('front', .76, .16, 10000, 7), row('angled', .70, .17, 9000, 83)]
        keys = {name: composition_rank_key(table, index)[0] for index, (name, table) in enumerate((('separate', separate), ('absorbed', absorbed), ('stacked', stacked), ('merged', merged)))}
        self.assertLess(keys['separate'], keys['absorbed'], 'absorbing frame geometry loses even with a higher usable share')
        self.assertLess(keys['merged'], keys['stacked'], 'equal contamination is decided by fittable coverage')
        # A far lens registered a few pixels off in the foreshortened view is clean in
        # the front view where it is best measured; that reading beats the one that
        # leaves the lens out, while absorbed struts stay contaminated in every view.
        one_lens = [row('front', .31, .02, 2080, 12), row('angled', .27, .02, 1300, 46)]
        both_lenses = [row('front', .61, .03, 4140, 25), row('angled', .48, .03, 2300, 520)]
        key_one, detail_one = composition_rank_key(one_lens, 0)
        key_both, detail_both = composition_rank_key(both_lenses, 1)
        self.assertEqual((detail_one['primary_view'], detail_both['primary_view']), ('front', 'front'))
        self.assertLess(key_both, key_one, 'the front view is clean for both; the reading with more usable rays wins')
        self.assertGreater(detail_both['contamination_max'], .15)
        self.assertLess(composition_rank_key(separate, 0)[0], composition_rank_key(absorbed, 1)[0], 'struts contaminate the front view too')
        self.assertEqual(composition_rank_key([], 5)[0], (10**6, 1., 10**6, 10**6, 5))
        # Identical composition: the reading with fewer declared groups (one cut body, one material) ranks first.
        self.assertLess(composition_rank_key(merged, 3, 1)[0], composition_rank_key(merged, 2, 2)[0])
        self.assertEqual(composition_rank_key(merged, 2, 2)[1]['declared_groups'], 2)

    def test_missing_cameras_or_apertures_leave_an_explicit_status(self):
        class Empty(FakeApertureEngine):
            def propose(self, rgb):
                result = super().propose(rgb)
                for variant in result['variants'].values():
                    variant['mask'] = np.zeros_like(variant['mask'])
                return result
        report = run_physical_group_stage(self.model, self.refinement, self.photos, self.folder/'empty', aperture_engine=Empty())
        self.assertEqual(report['status'], 'insufficient_views')
        self.assertEqual([v['status'] for v in report['views']], ['no_positive_aperture_proposal']*2)
        refinement = dict(self.refinement, views=self.refinement['views'][:1])
        report = run_physical_group_stage(self.model, refinement, self.photos, self.folder/'one-view', aperture_engine=FakeApertureEngine())
        self.assertEqual(report['status'], 'insufficient_views')
        self.assertEqual(report['views'][1]['status'], 'no_fitted_camera')


if __name__ == '__main__':
    unittest.main()
