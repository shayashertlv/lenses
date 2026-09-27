from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from PIL import Image
from reconstruction.production_validation import (evaluate_acceptance, measure_appearance_controls,
    measure_heldout_views, bind_independent_measurements, run_delivery_validation)
from test_automatic_articulation import write_fixture


def pin_json(path, value):
    path.write_text(json.dumps(value),encoding='utf-8')
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def history(root):
    from reconstruction.multiview_intake import run_multiview_intake
    rgb=np.full((200,300,3),255,np.uint8);rgb[80:125,55:245]=25
    path=root/'used.png';Image.fromarray(rgb).save(path)
    photo={'id':'used','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'view':'front'}
    run_multiview_intake([photo],{},root/'history')
    report=root/'history'/'report.json'
    return {'path':str(report),'sha256':hashlib.sha256(report.read_bytes()).hexdigest()}


class DeliveryGateTests(unittest.TestCase):
    def fixture(self):
        return {'candidate_sha256':'a'*64,'integrity_verified':True,'runtime_passed':True,'required_parts_present':True,
            'heldout':{'views':2,'independent':True,'minimum_iou':.96,'maximum_contour_p95':.006},
            'optical':{'opaque_core_fraction':.008,'contamination_fraction':.01,'maximum_validation_error_codes':4.},
            'appearance':{'color_error_codes':3.,'transmission_error':.04,'gradient_direction_matches':True},
            'frame':{'observed_fraction':.8},'asset':{'bytes':4_000_000,'triangles':40_000,'draw_calls':8}}

    def test_supported_result_can_pass_and_missing_data_cannot(self):
        self.assertTrue(evaluate_acceptance(self.fixture())['accepted'])
        result=evaluate_acceptance({'integrity_verified':True})
        self.assertFalse(result['accepted']);self.assertEqual(result['verdict'],'needs_review')

    def test_missing_temple_bad_camera_opaque_lens_and_asset_controls(self):
        changes=[('required_parts_present',False),('heldout',{'views':2,'independent':True,'minimum_iou':.6,'maximum_contour_p95':.08}),
                 ('optical',{'opaque_core_fraction':.6,'contamination_fraction':.01,'maximum_validation_error_codes':4.}),
                 ('asset',{'bytes':40_000_000,'triangles':650_000,'draw_calls':8})]
        for key,value in changes:
            evidence=deepcopy(self.fixture());evidence[key]=value
            self.assertFalse(evaluate_acceptance(evidence)['accepted'])
            self.assertEqual(evaluate_acceptance(evidence)['verdict'],'needs_repair')

    def test_measured_wrong_hue_reversed_gradient_and_opacity_fail(self):
        y=np.linspace(40,160,40)[:,None,None];reference=np.broadcast_to(y*np.array([1.,.7,.4]),(40,30,3)).copy()
        mask=np.ones((40,30),bool)
        candidates=[reference[:,:,::-1],reference[::-1],np.zeros_like(reference)]
        for candidate in candidates:
            metrics=measure_appearance_controls(reference,candidate,mask=mask,
                reference_transmission=[.7,.5,.3],candidate_transmission=[0,0,0] if not candidate.any() else [.7,.5,.3])
            evidence=self.fixture();evidence['appearance']=metrics
            self.assertFalse(evaluate_acceptance(evidence)['accepted'])
        correct=measure_appearance_controls(reference,reference,mask=mask,reference_transmission=[.7,.5,.3],candidate_transmission=[.7,.5,.3])
        evidence=self.fixture();evidence['appearance']=correct
        self.assertTrue(evaluate_acceptance(evidence)['accepted'])

    def test_nonfinite_or_leaked_holdout_cannot_pass(self):
        evidence=self.fixture();evidence['heldout']['independent']=False
        self.assertFalse(evaluate_acceptance(evidence)['accepted'])
        evidence=self.fixture();evidence['appearance']['color_error_codes']=float('nan')
        self.assertFalse(evaluate_acceptance(evidence)['accepted'])

    def test_scalar_wrapped_claims_cannot_accept_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'model.glb';write_fixture(model)
            evidence=self.fixture();evidence['candidate_sha256']=hashlib.sha256(model.read_bytes()).hexdigest()
            report=run_delivery_validation(model,root/'validation',evidence=evidence)
            self.assertFalse(report['accepted'])
            self.assertIn('heldout_silhouette_iou',report['unmeasured_gates'])
            self.assertIn('required_parts_present',report['unmeasured_gates'])

    def test_nested_measurement_from_another_asset_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'model.glb';write_fixture(model)
            ref=pin_json(root/'measurement.json',{'candidate_sha256':'b'*64,'method':'source_bound_heldout_geometry_v1'})
            with self.assertRaisesRegex(ValueError,'another candidate'):
                bind_independent_measurements(model,{'measurement_receipts':{'heldout':ref}})

    def test_receipt_scores_are_recomputed_and_history_is_required(self):
        from reconstruction.camera import Camera,render_mask
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'model.glb';mesh=write_fixture(model);ledger=history(root)
            mask=render_mask(mesh,Camera(35.,10.,0.,0.,120.,150.,90.),(200,300))
            rgb=np.full((200,300,3),255,np.uint8);rgb[mask]=25
            photo=root/'heldout.png';Image.fromarray(rgb).save(photo)
            measurement=measure_heldout_views(model,[{'id':'heldout','path':str(photo),'view':'angled'}],source_history_receipts=[ledger])
            self.assertFalse(measurement['independent'])  # Intake cannot prove an imported model's prior image usage.
            measurement['minimum_iou']=999.;measurement['maximum_contour_p95']=-1.
            ref=pin_json(root/'measurement.json',measurement)
            verified=bind_independent_measurements(model,{'measurement_receipts':{'heldout':ref},'reconstruction_history_receipts':[ledger]})
            self.assertLessEqual(verified['heldout']['minimum_iou'],1.)
            self.assertGreaterEqual(verified['heldout']['maximum_contour_p95'],0.)
            missing=bind_independent_measurements(model,{'measurement_receipts':{'heldout':ref}})
            self.assertEqual(missing['heldout'],{})

    def test_reencoded_training_photo_is_not_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'model.glb';write_fixture(model);ledger=history(root)
            photo=root/'reencoded.bmp';Image.open(root/'used.png').save(photo)
            with self.assertRaisesRegex(ValueError,'normalized pixels'):
                measure_heldout_views(model,[{'id':'heldout','path':str(photo)}],source_history_receipts=[ledger])


if __name__=='__main__':unittest.main()
