"""Shared material hypotheses must preserve contrary evidence and provenance."""
import copy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction import joint_photo_lens_fit as joint
from reconstruction import photo_lens_fit as single
from reconstruction.ar_material_search import propose_material_candidates
from reconstruction.joint_photo_lens_stage import joint_preview_representatives
from reconstruction.material_relations import infer_material_relations
from test_semantic_photo_lens_fit import fixture, policy, SOURCE


def paired_fixture():
    groups, priors, targets = fixture()
    semantic = {'construction': {'lens_count_or_shield': 'two separate lenses', 'confidence': 'high',
        'evidence': [{'photo_id': 'front', 'source_sha256': SOURCE, 'observation': 'Two separate apertures.'}]}}
    bindings = [g['surface_binding'] for g in groups]
    priors['material_relations'] = infer_material_relations(semantic, priors, bindings)
    return groups, priors, semantic


class MaterialRelationTests(unittest.TestCase):
    def test_inference_abstains_on_shield_uncertainty_bad_coordinate_or_different_facts(self):
        groups, priors, semantic = paired_fixture()
        self.assertEqual(len(priors['material_relations']), 1)
        bindings = [g['surface_binding'] for g in groups]
        for description in ('single continuous shield', 'possibly two lenses', 'uncertain pair', 'two shield lenses'):
            variant = copy.deepcopy(semantic)
            variant['construction']['lens_count_or_shield'] = description
            self.assertEqual(infer_material_relations(variant, priors, bindings), [])
        changed = copy.deepcopy(bindings); changed[1]['coordinate_method'] = 'inverted_height'
        self.assertEqual(infer_material_relations(semantic, priors, changed), [])
        priors['groups']['g1']['declared_facts'] = {'mirror_coating': True}
        self.assertEqual(infer_material_relations(semantic, priors, bindings), [])

    def test_aliases_material_not_view_geometry_and_preserves_independent_hypothesis(self):
        groups, priors, _ = paired_fixture()
        p = replace(policy(), photo_policy=replace(policy().photo_policy, max_nfev=12, roughness_values=(.05, .25)))
        report = joint.fit_joint_photo_lens_candidates(groups, policy=p, appearance_priors=priors)
        self.assertEqual(report['exploration']['failed_runs'], [])
        paired = [c for c in report['candidates'] if c['assumptions']['material_relation']['mode'] == 'shared_manufactured_pair']
        independent = [c for c in report['candidates'] if c['assumptions']['material_relation']['mode'] == 'independent']
        self.assertEqual((len(paired), len(independent)), (3, 3))
        for candidate in paired:
            self.assertEqual(candidate['groups']['g0']['appearance'], candidate['groups']['g1']['appearance'])
            material = [b for b in candidate['parameter_blocks'] if b['key'][0] == 'shared_material']
            self.assertEqual(len(material), 1)
            self.assertEqual(material[0]['referenced_by_groups'], ['g0', 'g1'])
            self.assertEqual(candidate['optimizer']['parameter_count'] + 15, independent[0]['optimizer']['parameter_count'])
            self.assertEqual(report['roughness_factorization']['variants_by_candidate_id'][candidate['candidate_id']], 2)
        self.assertEqual(len(joint_preview_representatives(report, include_material_relations=True)), 2)
        self.assertEqual(len(joint_preview_representatives(report)), 1)
        self.assertTrue(any(c['groups']['g0']['appearance'] != c['groups']['g1']['appearance'] for c in independent))

    def test_shared_initialization_is_order_invariant_and_curvature_counted_once(self):
        groups, priors, _ = paired_fixture()
        p = policy()
        bindings, records, branches, _, _ = joint._prepare_joint(groups, p)
        validated = single.validate_appearance_priors(priors, records, bindings)
        relation = validated['material_relations'][0]
        hypothesis = {'mode': 'shared_manufactured_pair', 'relation_ids': [relation['relation_id']],
                      'shared_group_sets': [relation['group_ids']]}
        model = joint._JointModel(branches[0], {'g0':'gradient_tint','g1':'gradient_tint'}, 'semantic_softbox',
            {'g0':'rear_content_excluded','g1':'rear_content_excluded'}, p.photo_policy, {}, validated, hypothesis)
        x = model.initial(0)
        local = model.models['g0']
        data_count = sum(3*sum(d['train']) for m in model.models.values() for d in m.data)
        anchor_count = sum(len(m.material_anchor_penalties(x[model.maps[g]])) for g,m in model.models.items())
        regularization_count = len(local.material_regularization(x[model.maps['g0']]))
        self.assertEqual(len(model.residual(x)), data_count+anchor_count+regularization_count+1)
        permuted = joint._JointModel(list(reversed(branches[0])), {'g1':'gradient_tint','g0':'gradient_tint'}, 'semantic_softbox',
            {'g0':'rear_content_excluded','g1':'rear_content_excluded'}, p.photo_policy, {}, validated, hypothesis)
        np.testing.assert_array_equal(permuted.initial(0), x)

    def test_stale_binding_unknown_group_and_contradictory_facts_rejected_before_optimization(self):
        groups, priors, _ = paired_fixture()
        variants = []
        bad = copy.deepcopy(priors); bad['material_relations'][0]['prepared_glb_sha256'] = 'b'*64; variants.append(bad)
        bad = copy.deepcopy(priors); bad['material_relations'][0]['group_ids'][0] = 'unknown'; variants.append(bad)
        bad = copy.deepcopy(priors); bad['material_relations'][0]['evidence'][0]['source_sha256'] = 'c'*64; variants.append(bad)
        bad = copy.deepcopy(priors); bad['groups']['g1']['declared_facts'] = {'mirror_coating':True}; variants.append(bad)
        with patch.object(joint, 'least_squares', side_effect=AssertionError('must preflight')):
            for bad in variants:
                with self.assertRaises(ValueError):
                    joint.fit_joint_photo_lens_candidates(groups, policy=policy(), appearance_priors=bad)

    def test_relation_is_checkpoint_bound_and_pair_can_export_when_one_lens_has_no_anchors(self):
        groups, priors, _ = paired_fixture()
        p = replace(policy(), photo_policy=replace(policy().photo_policy, max_nfev=3))
        with tempfile.TemporaryDirectory() as directory:
            joint.fit_joint_photo_lens_candidates(groups, policy=p, appearance_priors=priors, checkpoint_dir=Path(directory))
            changed = copy.deepcopy(priors); changed['material_relations'][0]['confidence'] = 'medium'
            with self.assertRaisesRegex(ValueError, 'immutable'):
                joint.fit_joint_photo_lens_candidates(groups, policy=p, appearance_priors=changed, checkpoint_dir=Path(directory))
        priors['groups']['g1']['density_anchors'] = []
        targets = fixture()[2]
        existing = [{'candidate_id':'old', 'appearances': {f'g{i}': a.to_dict() for i,a in enumerate(targets)},
            'family_assignment': {'g0':'gradient_tint','g1':'gradient_tint'}, 'score_codes':4,
            'prepared_glb_sha256':'a'*64}]
        candidates = propose_material_candidates({}, priors, existing_candidates=existing)
        paired = [c for c in candidates if c['origin'] == 'paired_measured_density_proposal']
        self.assertEqual(len(paired), 3)
        self.assertEqual(candidates[0]['appearances'], existing[0]['appearances'])
        for candidate in paired:
            self.assertEqual(candidate['appearances']['g0'], candidate['appearances']['g1'])
        existing[0]['prepared_glb_sha256'] = 'c'*64
        with self.assertRaisesRegex(ValueError, 'candidate asset'):
            propose_material_candidates({}, priors, existing_candidates=existing)


if __name__ == '__main__':
    unittest.main()
