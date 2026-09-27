"""Analytic controls for competing physical-group partitions."""
import json
import unittest

import numpy as np

from reconstruction.physical_groups import (
    PhysicalGroupLimits, PhysicalGroupPolicy, infer_physical_groups, partition_declarations_for_hypothesis,
)


SHAPE = (10, 10)


def block(rows, cols):
    return np.asarray(sorted(r*SHAPE[1]+c for r in rows for c in cols), np.int64)


def view(identifier, pieces, apertures=None):
    """pieces: list of (pixels, depth) per component in canonical order."""
    pixels, depths = [], []
    for p, d in pieces:
        p = np.asarray(p, np.int64)
        pixels.append(p)
        depths.append(np.full(len(p), float(d)) if np.ndim(d) == 0 else np.asarray(d, float))
    interpretations = []
    for aid, mask in (apertures or {}).items():
        full = np.zeros(SHAPE, bool); full.ravel()[mask] = True
        interpretations.append({'id': aid, 'mask': full, 'known_domain': np.ones(SHAPE, bool),
                                'provenance': {'method': 'analytic test aperture'}})
    return {'id': identifier, 'shape': SHAPE, 'component_pixels': pixels, 'component_depths': depths,
            'interpretations': interpretations, 'provenance': {'method': 'analytic test view'}}


def components(count):
    return [{'component_id': i, 'source_face_count': 2} for i in range(count)]


def branches(views, chosen):
    return [{'id': 'b', 'interpretation_by_view': {v['id']: chosen for v in views}}]


class PhysicalGroupTests(unittest.TestCase):
    def infer(self, comps, views, aperture='a', **kwargs):
        result = infer_physical_groups(comps, views, branches(views, aperture) if views[0]['interpretations'] else [], **kwargs)
        json.dumps(result['report'], allow_nan=False)
        self.assertFalse(result['report']['accepted'])
        for hypothesis in result['report']['hypotheses']:
            members = sorted(m for g in hypothesis['groups'] for m in g['members'])
            self.assertEqual(members, list(range(len(comps))), 'every component appears exactly once')
        return result

    def groups_of(self, result, index=0):
        return sorted(tuple(g['members']) for g in result['report']['hypotheses'][index]['groups'])

    def test_duplicate_shells_merge_far_layer_and_partial_overlap_do_not_minor_pieces_attach(self):
        big = block(range(2, 8), range(2, 8))
        far = block(range(2, 8), range(2, 8))
        minor = block([4], [4])
        partial = block(range(2, 8), range(5, 10))
        pad = block([5], [5])
        pieces = [(big, .5), (big, .5005), (far, 1.), (minor, .5002), (partial, .5), (pad, .9)]
        views = [view('front', pieces, {'a': big}), view('angled', pieces, {'a': big})]
        result = self.infer(components(6), views)
        self.assertEqual(result['report']['hypothesis_count'], 1)
        self.assertEqual(self.groups_of(result), [(0, 1, 3), (2,), (4,), (5,)])
        relations = {(p['left_component'], p['right_component']): p['relation_by_rung'] for p in result['report']['pairs']}
        self.assertEqual(set(relations[(0, 1)]), {'same_surface'})
        self.assertEqual(set(relations[(0, 2)]), {'separated_layers'})
        self.assertEqual(set(relations[(0, 3)]), {'minor_coincident'})
        self.assertEqual(set(relations[(0, 4)]), {'partial_overlap'})
        self.assertEqual(set(relations[(0, 5)]), {'minor_separated'}, 'a pad well behind the lens is never attached')
        hypothesis = result['report']['hypotheses'][0]
        self.assertEqual(hypothesis['attached_minor_components'], [3])
        group = next(g for g in hypothesis['groups'] if 0 in g['members'])
        self.assertEqual(group['attached_minor_members'], [3])
        self.assertEqual(group['member_pairs_without_direct_overlap'], [])
        self.assertEqual([(m['left_component'], m['right_component']) for m in hypothesis['unattached_minor_pairs']],
                         [(0, 5), (1, 5), (2, 3), (2, 5), (4, 5)])

    def test_body_edges_add_a_reunited_hypothesis_and_are_validated(self):
        # Two side-by-side pieces never overlap, so the ladder keeps them apart in every rung.
        aperture = {'a': block(range(1, 9), range(0, 9))}
        front = view('front', [(block(range(2, 8), range(1, 4)), 1.0), (block(range(2, 8), range(5, 8)), 1.0)], aperture)
        back = view('back', [(block(range(2, 8), range(5, 8)), 1.0), (block(range(2, 8), range(1, 4)), 1.0)], aperture)
        plain = self.infer(components(2), [front, back])
        self.assertEqual([h['cut_reunion'] for h in plain['report']['hypotheses']], [False])
        self.assertEqual(self.groups_of(plain), [(0,), (1,)])
        self.assertEqual(plain['report']['body_edges'], [])
        reunited = self.infer(components(2), [front, back], body_edges=[[1, 0]])
        self.assertEqual(len(reunited['report']['hypotheses']), 2)
        extra = reunited['report']['hypotheses'][1]
        self.assertEqual((extra['cut_reunion'], extra['reunited_from'], extra['rungs']), (True, 0, plain['report']['hypotheses'][0]['rungs']))
        self.assertEqual(self.groups_of(reunited, 1), [(0, 1)])
        self.assertEqual(self.groups_of(reunited, 0), [(0,), (1,)], 'the split reading stays')
        self.assertEqual(reunited['report']['body_edges'], [[0, 1]])
        self.assertEqual(extra['groups'][0]['role_consensus'], 'optical_candidate')
        for bad in ([[0, 0]], [[0, 2]], [[0]], [(0, 'x')], 'no'):
            with self.assertRaises(ValueError):
                infer_physical_groups(components(2), [front, back], branches([front, back], 'a'), body_edges=bad)
        for bad in (dict(cut_continuity_fraction=1.), dict(cut_continuity_angle_degrees=90), dict(cut_continuity_minimum_edges=0)):
            with self.assertRaises(ValueError):
                PhysicalGroupPolicy(**bad)

    def test_minor_piece_coincident_with_two_groups_is_not_used_as_a_bridge(self):
        left = block(range(2, 8), range(0, 6))
        right = block(range(2, 8), range(4, 10))
        sliver = block([4], [4])
        pieces = [(left, .5), (right, .5), (sliver, .5)]
        views = [view('front', pieces), view('angled', pieces)]
        result = self.infer(components(3), views)
        self.assertEqual(self.groups_of(result), [(0,), (1,), (2,)])
        hypothesis = result['report']['hypotheses'][0]
        self.assertEqual(hypothesis['attached_minor_components'], [])
        self.assertEqual([a['component_id'] for a in hypothesis['ambiguous_minor_attachments']], [2])

    def test_constant_gap_is_rung_dependent_and_yields_two_hypotheses(self):
        big = block(range(2, 8), range(2, 8))
        pieces = [(big, .5), (big, .515)]
        views = [view('front', pieces), view('angled', pieces)]
        result = self.infer(components(2), views)
        pair = result['report']['pairs'][0]
        self.assertTrue(pair['rung_dependent'])
        self.assertEqual(pair['merge_by_rung'], [False, False, True, True, True])
        self.assertEqual(result['report']['hypothesis_count'], 2)
        self.assertEqual(self.groups_of(result, 0), [(0,), (1,)])
        self.assertEqual(self.groups_of(result, 1), [(0, 1)])
        self.assertEqual(result['report']['hypotheses'][0]['rungs'], [0, 1])
        self.assertEqual(result['report']['hypotheses'][1]['depth_tolerances'], [.02, .04, .08])

    def test_partial_pieces_of_one_surface_merge_transitively_without_mutual_overlap(self):
        whole = block(range(2, 8), range(2, 8))
        left = block(range(2, 8), range(2, 5))
        right = block(range(2, 8), range(5, 8))
        pieces = [(whole, .5), (left, .5001), (right, .4999)]
        views = [view('front', pieces), view('angled', pieces)]
        result = self.infer(components(3), views)
        self.assertEqual(self.groups_of(result), [(0, 1, 2)])
        pairs = {(p['left_component'], p['right_component']) for p in result['report']['pairs']}
        self.assertNotIn((1, 2), pairs, 'the two halves never overlap each other')
        group = result['report']['hypotheses'][0]['groups'][0]
        self.assertEqual(group['member_pairs_without_direct_overlap'], [[1, 2]], 'bridged through the whole surface, and reported')

    def test_overlap_in_one_view_but_not_the_other_never_merges(self):
        big = block(range(2, 8), range(2, 8))
        elsewhere = block(range(2, 8), range(0, 2))
        front = view('front', [(big, .5), (big, .5)])
        angled = view('angled', [(big, .5), (elsewhere, .5)])
        result = self.infer(components(2), [front, angled])
        self.assertEqual(self.groups_of(result), [(0,), (1,)])
        self.assertEqual(set(result['report']['pairs'][0]['relation_by_rung']), {'inconsistent_across_views'})

    def test_roles_follow_branch_evidence_and_never_change_membership(self):
        lens = block(range(2, 8), range(2, 8))
        frame = block(range(0, 10), range(0, 2))
        aperture_lens = lens
        aperture_wide = block(range(0, 10), range(0, 10))
        pieces = [(lens, .5), (frame, .4)]
        views = [view('front', pieces, {'tight': aperture_lens, 'wide': aperture_wide}),
                 view('angled', pieces, {'tight': aperture_lens, 'wide': aperture_wide})]
        branch_list = [{'id': 'tight', 'interpretation_by_view': {'front': 'tight', 'angled': 'tight'}},
                       {'id': 'wide', 'interpretation_by_view': {'front': 'wide', 'angled': 'wide'}}]
        result = infer_physical_groups(components(2), views, branch_list)
        groups = {tuple(g['members']): g for g in result['report']['hypotheses'][0]['groups']}
        lens_roles = {r['branch_id']: r['role'] for r in groups[(0,)]['roles_by_branch']}
        frame_roles = {r['branch_id']: r['role'] for r in groups[(1,)]['roles_by_branch']}
        self.assertEqual(lens_roles, {'tight': 'optical_candidate', 'wide': 'optical_candidate'})
        self.assertEqual(groups[(0,)]['role_consensus'], 'optical_candidate')
        self.assertEqual(frame_roles['tight'], 'non_optical_evidence')
        self.assertEqual(frame_roles['wide'], 'unresolved', 'a wide aperture contains the frame but it explains too little')
        self.assertEqual(groups[(1,)]['role_consensus'], 'branch_dependent')
        self.assertEqual(result['report']['hypotheses'][0]['role_counts']['branch_dependent'], 1)

    def test_unobserved_components_stay_unknown_singletons_and_support_is_evidence_only(self):
        big = block(range(2, 8), range(2, 8))
        pieces = [(big, .5), (np.empty(0, np.int64), np.empty(0)), (big, .5001)]
        views = [view('front', pieces, {'a': big}), view('angled', pieces, {'a': big})]
        result = self.infer(components(3), views, support=[{'id': 's', 'selected_component_ids': [0]}])
        self.assertEqual(result['report']['unobserved_component_ids'], [1])
        groups = {tuple(g['members']): g for g in result['report']['hypotheses'][0]['groups']}
        self.assertEqual(groups[(1,)]['role_consensus'], 'unobserved')
        self.assertEqual(groups[(0, 2)]['support_union_evidence'][0]['selected_fraction_of_observed_members'], .5)
        self.assertEqual(groups[(0, 2)]['support_union_evidence'][0]['selected_members'], [0])

    def test_group_layering_reports_order_between_groups(self):
        big = block(range(2, 8), range(2, 8))
        pieces = [(big, .5), (big, 1.5)]
        views = [view('front', pieces), view('angled', pieces)]
        result = self.infer(components(2), views)
        layering = result['report']['hypotheses'][0]['group_layering']
        self.assertEqual(len(layering), 1)
        self.assertEqual([r['order'] for r in layering[0]['views']], ['left_nearer', 'left_nearer'])

    def test_invalid_inputs_are_rejected(self):
        big = block(range(2, 8), range(2, 8))
        good = view('front', [(big, .5)], {'a': big})
        with self.assertRaises(ValueError):
            infer_physical_groups(components(2), [good], [])
        with self.assertRaises(ValueError):
            infer_physical_groups(components(1), [good], [{'id': 'b', 'interpretation_by_view': {'front': 'missing'}}])
        bad = view('front', [(big, .5)], {'a': big})
        bad['component_depths'][0][0] = np.inf
        with self.assertRaises(ValueError):
            infer_physical_groups(components(1), [bad], [])
        with self.assertRaises(ValueError):
            PhysicalGroupPolicy(depth_tolerance_ladder=(.02, .01))
        with self.assertRaises(ValueError):
            PhysicalGroupPolicy(optical_cover_fraction=1.)
        with self.assertRaises(ValueError):
            infer_physical_groups(components(1), [good], [], limits=PhysicalGroupLimits(maximum_component_pixels=1))

    def test_partition_declarations_cover_every_face_once_with_explicit_remainder(self):
        big = block(range(2, 8), range(2, 8))
        pieces = [(big, .5), (big, .5001), (block([0], [0]), .5), (block([9], [9]), 2.)]
        views = [view('front', pieces, {'a': big}), view('angled', pieces, {'a': big})]
        comps = [{'component_id': 0, 'source_face_count': 3, 'source_binding': {'node_index': 0, 'mesh_index': 0, 'primitive_index': 0}, 'local_component_id': 0},
                 {'component_id': 1, 'source_face_count': 2, 'source_binding': {'node_index': 0, 'mesh_index': 0, 'primitive_index': 0}, 'local_component_id': 1},
                 {'component_id': 2, 'source_face_count': 1, 'source_binding': {'node_index': 0, 'mesh_index': 0, 'primitive_index': 0}, 'local_component_id': 2},
                 {'component_id': 3, 'source_face_count': 4, 'source_binding': {'node_index': 1, 'mesh_index': 1, 'primitive_index': 0}, 'local_component_id': 0}]
        result = self.infer(comps, views)
        hypothesis = result['report']['hypotheses'][0]
        self.assertEqual(self.groups_of(result), [(0, 1), (2,), (3,)])
        labels = {(0, 0, 0): np.array([0, 1, 0, 2, 1, 0]), (1, 1, 0): np.array([0, 0, 0, 0])}
        declarations, groups = partition_declarations_for_hypothesis(
            hypothesis, comps, labels, source_sha256='0'*64, declared_groups=['group-0000'],
            provenance={'test': True})
        json.dumps(declarations, allow_nan=False)
        self.assertEqual(groups, [{'group_id': 'group-0000', 'piece_ids': ['p0-0-0-group-0000']}])
        self.assertEqual(len(declarations['partitions']), 1, 'the untouched second primitive stays unpartitioned')
        first = declarations['partitions'][0]
        self.assertEqual([p['id'] for p in first['pieces']], ['p0-0-0-group-0000', 'p0-0-0-remainder'])
        self.assertEqual(first['pieces'][0]['source_face_indices'], [0, 1, 2, 4, 5])
        self.assertEqual(first['pieces'][1]['source_face_indices'], [3])
        self.assertEqual(first['pieces'][0]['declared_role'], 'optical', 'the hypothesis declares the group piece optical')
        self.assertNotIn('declared_role', first['pieces'][1], 'the remainder carries no role')
        with self.assertRaises(ValueError):
            partition_declarations_for_hypothesis(hypothesis, comps, labels, source_sha256='0'*64,
                                                  declared_groups=['group-0000', 'group-0000'], provenance={'t': 1})
        with self.assertRaises(ValueError):
            partition_declarations_for_hypothesis(hypothesis, comps, {(0, 0, 0): np.array([0, 1]), (1, 1, 0): labels[(1, 1, 0)]},
                                                  source_sha256='0'*64, declared_groups=['group-0000'], provenance={'t': 1})


if __name__ == '__main__':
    unittest.main()
