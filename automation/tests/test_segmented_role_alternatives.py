"""Role branches cannot silently lose ambiguity or bind unrelated geometry."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reconstruction.mesh import load_glb_bytes
from reconstruction.segmented_providers import pin
from reconstruction.segmented_role_alternatives import (validated_hypotheses, verify_reduced_part_lineage,
                                                       process_role_alternatives)
from test_part_role_inference import asset, quad


class RoleAlternativeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.model=self.root/'model.glb'
        self.parts=[quad(-1,0,-1,1),quad(0,1,-1,1),quad(-1,0,1,1.05)]
        self.model.write_bytes(asset(self.parts))
        model_pin=pin(self.model)
        mesh=load_glb_bytes(self.model.read_bytes())
        self.report=dict(source_path=str(self.model),source_sha256=model_pin['sha256'],primary_groups=[[0],[1]],
            parts=[dict(part_index=i,source_binding={k:p[k] for k in ('node_index','mesh_index','primitive_index')}) for i,p in enumerate(mesh.parts)],
            fragment_evidence=[dict(part_index=2,seed_part_index=0,candidate=True)],
            hypotheses=[dict(id='primary',groups=[[0],[1]],added_fragments=[]),
                        dict(id='edge-alternative-001',groups=[[0,2],[1]],added_fragments=[2])])
        self.proof=dict(model=model_pin,lod=dict(source_sha256=model_pin['sha256'],candidate_sha256=model_pin['sha256'],
                       vertices_exact=True,material_uv_bindings_exact=True),
                       compact=dict(source_sha256=model_pin['sha256'],output_sha256=model_pin['sha256'],
                            active_semantics_identical=True,index_maps={key:{str(i):i for i in range(3)} for key in ('nodes','meshes')}))

    def test_existing_reduced_part_lineage_verified_independently(self):
        result=verify_reduced_part_lineage(self.model,self.report,self.proof)
        self.assertEqual(result['maximum_vertex_membership_error'],0)
        self.assertFalse(result['old_face_ids_reused'])
        changed=deepcopy(self.report);changed['parts'][1]['source_binding']['mesh_index']=0
        with self.assertRaisesRegex(ValueError,'binding differs'):
            verify_reduced_part_lineage(self.model,changed,self.proof)

    def test_forged_receipt_cannot_hide_changed_part_geometry(self):
        altered=self.root/'altered.glb'
        modified=deepcopy(self.parts);modified[2][1,0]+=.1
        altered.write_bytes(asset(modified))
        proof=deepcopy(self.proof)
        proof['model']=pin(altered);proof['compact']['output_sha256']=pin(altered)['sha256']
        with self.assertRaisesRegex(ValueError,'not retained source-part vertices'):
            verify_reduced_part_lineage(altered,self.report,proof)

    def test_alternatives_must_retain_seeds_and_evidence_for_every_added_part(self):
        invalid=deepcopy(self.report);invalid['hypotheses'][1]['groups']=[[0,2]]
        with self.assertRaisesRegex(ValueError,'removes a supported seed'):
            validated_hypotheses(invalid)
        invalid=deepcopy(self.report);invalid['fragment_evidence']=[]
        with self.assertRaisesRegex(ValueError,'lacks recorded'):
            validated_hypotheses(invalid)
        invalid=deepcopy(self.report);invalid['hypotheses'][1]['groups']=[[0,1,2]]
        with self.assertRaisesRegex(ValueError,'changes established seed grouping'):
            validated_hypotheses(invalid)

    def test_truncated_or_rejected_alternatives_still_block_silent_primary_selection(self):
        def build(source,reduced,hypothesis,folder,settings):
            if hypothesis['id']!='primary':
                raise ValueError('strict preparation rejects this shape')
            return dict(id='primary',groups=hypothesis['groups'],status='within_delivery_budgets',
                        model=pin(self.model),standard_control=pin(self.model),accepted=False)
        with patch('reconstruction.segmented_role_alternatives._build_variant',side_effect=build):
            result=process_role_alternatives(self.model,self.report,self.root/'full',reduction_proof=self.proof)
            self.assertEqual(result['variants'][1]['status'],'variant_rejected')
            self.assertTrue(result['role_selection_required'])
            self.assertFalse(result['primary_may_ship'])
            self.assertIsNone(result['selected_hypothesis'])
            limited=process_role_alternatives(self.model,self.report,self.root/'limited',reduction_proof=self.proof,maximum_alternatives=0)
            self.assertEqual(limited['deferred_hypothesis_ids'],['edge-alternative-001'])
            self.assertFalse(limited['primary_may_ship'])

    def test_complete_replay_is_verified_and_does_not_rebuild_branches(self):
        def build(source,reduced,hypothesis,folder,settings):
            return dict(id=hypothesis['id'],groups=hypothesis['groups'],status='within_delivery_budgets',
                        model=pin(self.model),standard_control=pin(self.model),accepted=False)
        with patch('reconstruction.segmented_role_alternatives._build_variant',side_effect=build) as builder:
            output=self.root/'out'
            first=process_role_alternatives(self.model,self.report,output,reduction_proof=self.proof)
            again=process_role_alternatives(self.model,self.report,output,reduction_proof=self.proof)
            self.assertEqual(first,again)
            self.assertEqual(builder.call_count,2)
            (output/'report.json').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'changed'):
                process_role_alternatives(self.model,self.report,output,reduction_proof=self.proof)


if __name__=='__main__':
    unittest.main()
