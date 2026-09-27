"""Small real geometry measurements through the reserved-evaluation job path."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.camera import Camera, render_mask
from reconstruction.evaluation_stage import (
    STAGES, reserve_job_evaluation, capture_job_usage, capture_delivery_usage,
    measure_reserved_views, pin, read_pin)
from reconstruction.initializer import _json_bytes, MESHY_ENDPOINT
from reconstruction.meshy_transport import _raw_json
from reconstruction.multiview_intake import run_multiview_intake
from reconstruction.production_validation import bind_independent_measurements, measure_heldout_views
from reconstruction.surface_transfer import _chunks, _pack
from test_automatic_articulation import write_fixture


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


def verified_provider_fixture(folder, model, photos):
    """No network: the same persisted HTTP contract, with a valid geometry GLB."""
    folder.mkdir(parents=True, exist_ok=True)
    initial=folder/'initial.glb';initial.write_bytes(model.read_bytes())
    selected=[{**photo,'sha256':sha(Path(photo['path']).read_bytes())} for photo in photos]
    settings={'ai_model':'meshy-7.1','should_texture':True}
    request={'kind':'meshy','selection':{'selected':selected,'unused':[]},'settings':settings}
    request_sha=sha(_json_bytes(request));task_id='local-fixture-task'
    save(folder/'request.json',request)
    save(folder/'artifact_receipt.json',{'request_sha256':request_sha,'task_id':task_id,
                                        'sha256':sha(initial.read_bytes())})
    save(folder/'task_receipt.json',{'request_sha256':request_sha,'task_id':task_id,'association':'submit_response'})
    payload={**settings,'image_urls':['data:image/png;base64,'+base64.b64encode(
        Path(photo['path']).read_bytes()).decode('ascii') for photo in selected]}
    body=_raw_json(payload)
    save(folder/'transport/submission.json',{'request_sha256':request_sha,'payload_sha256':sha(body),
                                            'payload_bytes':len(body)})
    download='https://assets.meshy.ai/local-fixture.glb'
    exchanges=[('post','POST',MESHY_ENDPOINT,True,_raw_json({'result':task_id}),sha(body)),
               ('get','GET',MESHY_ENDPOINT+'/'+task_id,True,_raw_json({
                   'id':task_id,'status':'SUCCEEDED','model_urls':{'glb':download}}),None),
               ('download','GET',download,False,initial.read_bytes(),None)]
    for name,method,url,authenticated,response,body_sha in exchanges:
        directory=folder/'transport'/('http-'+name)
        save(directory/'request.json',{'method':method,'url':url,'authenticated_api':authenticated,
                                       'request_sha256':request_sha,'body_sha256':body_sha})
        save(directory/'response.json',{'saved_sha256':sha(response),'status':200,'error_type':None,
                                        'secret_redacted':False})
        (directory/'response.bin').write_bytes(response)
    return {'initializer_folder':str(folder.resolve()),'model_path':str(initial.resolve())}


class EvaluationStageIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.model=self.root/'candidate.glb';mesh=write_fixture(self.model).normalized()
        self.photos=[]
        for index,(identifier,yaw,view) in enumerate([
                ('fit-front',0.,'front'),('fit-back',180.,'back'),
                ('eval-left',35.,'angled'),('eval-right',-50.,'angled')]):
            mask=render_mask(mesh,Camera(yaw,10.,0.,0.,165.,150.,100.),(210,300))
            rgb=np.full((210,300,3),255,np.uint8);rgb[mask]=25+index
            path=self.root/(identifier+'.png');Image.fromarray(rgb).save(path)
            self.photos.append({'id':identifier,'path':str(path),'view':view})
        self.request={'schema_version':1,'photos':self.photos,
                      'reserved_photo_ids':['eval-left','eval-right']}
        self.job=self.root/'job';self.job.mkdir();self.journal={'stages':{}}
        self.clean,self.context=reserve_job_evaluation(self.request,self.root,self.job,self.journal)
        self.fit=[{**photo,'sha256':sha(Path(photo['path']).read_bytes())} for photo in self.clean['photos']]
        self.provider=verified_provider_fixture(self.root/'provider',self.model,self.fit)
        self._capture_training_usage()
        run_multiview_intake(self.fit,{},self.root/'training-history')
        self.history=pin(self.root/'training-history/report.json')

    def tearDown(self):
        self.temp.cleanup()

    def test_cached_transport_report_snapshots_resume_and_changed_original_rejects(self):
        from reconstruction.evaluation_stage import capture_cached_semantics
        from reconstruction.photo_semantics import build_image_manifest
        from test_photo_semantics import response
        original = self.root/'external.png'
        original.write_bytes(Path(self.fit[0]['path']).read_bytes())
        manifest = build_image_manifest([{'id':'external','path':str(original)}])
        report = self.root/'cached.json'
        save(report,{'source_images':manifest,'response':response()})
        owned = capture_cached_semantics(self.job,self.journal,self.context,report)
        before = owned.read_bytes()
        original.write_bytes(original.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'Cached semantic source photo changed'):
            capture_cached_semantics(self.job,self.journal,self.context,report)
        original.unlink()
        self.assertEqual(owned,capture_cached_semantics(self.job,self.journal,self.context,report))
        self.assertEqual(before,owned.read_bytes())

    def _capture_training_usage(self):
        for stage_id in STAGES:
            if stage_id=='frame':continue
            capture_job_usage(self.job,self.journal,self.context,stage_id,self.fit,
                              external_history=self.provider if stage_id=='initializer' else None)
        regions=self.root/'regions.json'
        save(regions,{'photos':[{'id':p['id'],'source':p['path'],'source_sha256':sha(Path(p['path']).read_bytes())}
                               for p in self.fit]})
        self.context=capture_delivery_usage(self.context,regions,self.root/'frame-usage')

    def _measure(self, name='evaluation', context=None):
        return measure_reserved_views(context or self.context,self.model,self.root/name,
                                      history_receipts=[self.history])

    def _evidence(self, measurement, context=None):
        return {'measurement_receipts':{'heldout':measurement},
                'reconstruction_history_receipts':[self.history],
                'evaluation_context':context if context is not None else self.context}

    def test_valid_provider_chain_to_sealed_measurement_and_bound_recomputation(self):
        reference=self._measure();report=read_pin(reference)
        seal=json.loads((self.root/'evaluation/candidate-seal.json').read_bytes())
        self.assertTrue(seal['independent_evaluation_eligible'])
        self.assertEqual(seal['sealed_candidate'],pin(self.model))
        self.assertEqual(set(seal['usage_receipt_sha256s']),set(STAGES))
        self.assertEqual(report['method'],'source_bound_heldout_geometry_v2')
        self.assertEqual(report['views'],2)
        self.assertTrue(report['independent'])
        verified=bind_independent_measurements(self.model,self._evidence(reference))
        self.assertTrue(verified['heldout']['independent'])
        self.assertEqual(verified['heldout']['evaluation_context'],self.context)
        self.assertGreaterEqual(verified['heldout']['minimum_iou'],0.)
        self.assertLessEqual(verified['heldout']['minimum_iou'],1.)
        self.assertEqual(verified['independent_measurement_audit'][0]['status'],'recomputed_from_pinned_inputs')

    def test_scores_and_wrapped_scalar_claims_never_bypass_recomputation(self):
        reference=self._measure();report=read_pin(reference)
        report.update(minimum_iou=999.,maximum_contour_p95=-5.,independent=False)
        save(Path(reference['path']),report);reference=pin(reference['path'])
        evidence=self._evidence(reference)
        evidence.update(heldout={'independent':True,'minimum_iou':999.},
                        appearance={'color_error_codes':0},required_parts_present=True)
        verified=bind_independent_measurements(self.model,evidence)
        self.assertTrue(verified['heldout']['independent'])
        self.assertLessEqual(verified['heldout']['minimum_iou'],1.)
        self.assertGreaterEqual(verified['heldout']['maximum_contour_p95'],0.)
        self.assertEqual(verified['appearance'],{})
        self.assertIsNone(verified['required_parts_present'])

    def test_no_current_job_context_cannot_reuse_an_independent_measurement(self):
        reference=self._measure()
        evidence=self._evidence(reference);evidence.pop('evaluation_context')
        result=bind_independent_measurements(self.model,evidence)
        self.assertEqual(result['heldout'],{})
        different=deepcopy(self.context);different['expected_stages'].pop('frame')
        result=bind_independent_measurements(self.model,self._evidence(reference,different))
        self.assertEqual(result['heldout'],{})

    def test_changed_usage_receipt_invalidates_current_context(self):
        reference=self._measure();usage=Path(self.context['usage_receipts'][0]['path'])
        value=json.loads(usage.read_bytes());value['phase']='frame';save(usage,value)
        with self.assertRaisesRegex(ValueError,'context artifact changed'):
            bind_independent_measurements(self.model,self._evidence(reference))

    def test_changed_captured_usage_pixels_are_detected_after_measurement(self):
        reference=self._measure();usage=read_pin(self.context['usage_receipts'][1])
        Path(usage['photos'][0]['snapshot_path']).write_bytes(b'changed captured pixels')
        with self.assertRaisesRegex(ValueError,'Usage photo snapshot changed'):
            bind_independent_measurements(self.model,self._evidence(reference))

    def test_changed_reserved_photo_snapshot_is_detected_after_measurement(self):
        reference=self._measure();reservation=read_pin(self.context['reservation'])
        photo=next(p for p in reservation['photos'] if p['purpose']=='evaluation')
        Path(photo['normalized_snapshot']['path']).write_bytes(b'changed reserved pixels')
        with self.assertRaisesRegex(ValueError,'Reserved photo snapshot changed'):
            bind_independent_measurements(self.model,self._evidence(reference))

    def test_different_valid_candidate_bytes_cannot_reuse_the_score(self):
        reference=self._measure();doc,binary=_chunks(self.model.read_bytes())
        doc['asset']['generator']='modified-after-heldout-seal'
        self.model.write_bytes(_pack(doc,binary))
        with self.assertRaisesRegex(ValueError,'another candidate'):
            bind_independent_measurements(self.model,self._evidence(reference))

    def test_repair_cannot_get_a_fresh_independent_score_from_consumed_views(self):
        self._measure()
        doc,binary=_chunks(self.model.read_bytes())
        doc['asset']['generator']='a-new-candidate-after-seeing-evaluation'
        self.model.write_bytes(_pack(doc,binary))
        with self.assertRaisesRegex(ValueError,'already consumed'):
            self._measure(name='new-evaluation')
        # Dropping the public context field cannot evade its immutable reservation lock.
        context=deepcopy(self.context);context.pop('candidate_commitment')
        with self.assertRaisesRegex(ValueError,'already consumed'):
            self._measure(name='another-evaluation',context=context)

    def test_same_candidate_recomputation_can_reuse_its_committed_evaluation(self):
        first=read_pin(self._measure())
        second=read_pin(self._measure(name='repeated-evaluation'))
        self.assertTrue(second['independent'])
        self.assertEqual(first['minimum_iou'],second['minimum_iou'])

    def test_missing_frame_ledger_does_not_produce_independent_scores(self):
        context=deepcopy(self.context)
        context['usage_receipts']=[r for r in context['usage_receipts'] if read_pin(r)['stage_id']!='frame']
        reference=self._measure(context=context)
        self.assertFalse(read_pin(reference)['independent'])
        result=bind_independent_measurements(self.model,self._evidence(reference,context))
        self.assertFalse(result['heldout']['independent'])

    def test_imported_initializer_history_remains_unverified_despite_owned_intake(self):
        folder=self.root/'imported';folder.mkdir()
        imported=folder/'initial.glb';imported.write_bytes(self.model.read_bytes())
        save(folder/'request.json',{'kind':'existing_glb','source':pin(imported)})
        save(folder/'artifact_receipt.json',{'sha256':sha(imported.read_bytes()),'provenance':'existing_glb_copy'})
        from reconstruction.evaluation_reservation import record_photo_usage
        context=deepcopy(self.context)
        context['usage_receipts']=[r for r in context['usage_receipts'] if read_pin(r)['stage_id']!='initializer']
        record_photo_usage(read_pin(context['reservation']),'initializer','provider',self.fit,self.root/'import-usage',
                           external_history={'initializer_folder':str(folder),'model_path':str(imported)})
        context['usage_receipts'].append(pin(self.root/'import-usage/usage.json'))
        reference=self._measure(context=context)
        self.assertEqual(read_pin(reference)['views'],2)
        self.assertFalse(read_pin(reference)['independent'])
        result=bind_independent_measurements(self.model,self._evidence(reference,context))
        self.assertFalse(result['heldout']['independent'])

    def test_old_intake_and_true_boolean_do_not_establish_independence(self):
        measured=measure_heldout_views(self.model,self.photos[2:],source_history_receipts=[self.history],
                                       source_history_complete=True,resolution=128)
        self.assertEqual(measured['views'],2)
        self.assertFalse(measured['independent'])
        file=self.root/'legacy-measurement.json';save(file,measured)
        result=bind_independent_measurements(self.model,{'measurement_receipts':{'heldout':pin(file)},
                                                        'reconstruction_history_receipts':[self.history]})
        self.assertFalse(result['heldout']['independent'])

    def test_reservation_and_stage_capture_resume_keep_exact_context(self):
        clean,context=reserve_job_evaluation(self.request,self.root,self.job,self.journal)
        self.assertEqual(clean,self.clean)
        for stage_id in STAGES:
            if stage_id=='frame':continue
            capture_job_usage(self.job,self.journal,context,stage_id,self.fit,
                              external_history=self.provider if stage_id=='initializer' else None)
        expected=[r for r in self.context['usage_receipts'] if read_pin(r)['stage_id']!='frame']
        self.assertEqual(context['usage_receipts'],expected)
        self.assertEqual(len(self.journal['stages']['evaluation_reservation']),1)


if __name__=='__main__':unittest.main()
