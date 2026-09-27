import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.initializer import _json_bytes, MESHY_ENDPOINT
from reconstruction.meshy_transport import _raw_json
from qa.staged_evaluation.evaluation_reservation import (
    prepare_evaluation_reservation, record_photo_usage, verify_photo_usage,
    verify_reservation, verified_external_history)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_json_bytes(value))


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.photos=[]
        for i in range(5):
            path=self.root/f'photo{i}.png'
            data=np.random.default_rng(i).integers(0,256,(20,32,3),dtype=np.uint8)
            Image.fromarray(data).save(path)
            self.photos.append({'id':f'p{i}','path':str(path),'view':'front' if i==0 else 'back'})
        self.request={'schema_version':1,'photos':deepcopy(self.photos[:3]),'reserved_photo_ids':['p2']}

    def tearDown(self):
        self.temp.cleanup()

    def reserve(self, request=None):
        return prepare_evaluation_reservation(request or self.request,self.root,self.root/'reservation')

    def history(self, selected=None, unused=()):
        folder=self.root/'initializer';folder.mkdir(exist_ok=True)
        selected=selected or self.photos[:2]
        selected=[{**p,'sha256':sha(Path(p['path']).read_bytes())} for p in selected]
        settings={'ai_model':'meshy-7.1','should_texture':True}
        unused=[{**p,'sha256':sha(Path(p['path']).read_bytes())} for p in unused]
        request={'kind':'meshy','selection':{'selected':selected,'unused':unused},'settings':settings}
        request_sha=sha(_json_bytes(request));model=folder/'initial.glb';model.write_bytes(b'fixture-provider-glb-bytes')
        save(folder/'request.json',request)
        save(folder/'artifact_receipt.json',{'request_sha256':request_sha,'task_id':'task1','sha256':sha(model.read_bytes())})
        save(folder/'task_receipt.json',{'request_sha256':request_sha,'task_id':'task1','association':'submit_response'})
        payload={**settings,'image_urls':['data:image/png;base64,'+base64.b64encode(Path(p['path']).read_bytes()).decode() for p in selected]}
        body=_raw_json(payload)
        save(folder/'transport/submission.json',{'request_sha256':request_sha,'payload_sha256':sha(body),'payload_bytes':len(body)})
        download='https://assets.meshy.ai/model.glb'
        exchanges=[('post','POST',MESHY_ENDPOINT,True,_raw_json({'result':'task1'}),sha(body)),
                   ('get','GET',MESHY_ENDPOINT+'/task1',True,_raw_json({'id':'task1','status':'SUCCEEDED','model_urls':{'glb':download}}),None),
                   ('download','GET',download,False,model.read_bytes(),None)]
        for name,method,url,authenticated,response,body_sha in exchanges:
            directory=folder/'transport'/('http-'+name)
            save(directory/'request.json',{'method':method,'url':url,'authenticated_api':authenticated,
                                           'request_sha256':request_sha,'body_sha256':body_sha})
            save(directory/'response.json',{'saved_sha256':sha(response),'status':200,'error_type':None,'secret_redacted':False})
            (directory/'response.bin').write_bytes(response)
        return {'initializer_folder':str(folder),'model_path':str(model)}

    def test_absent_config_preserves_request_and_creates_nothing(self):
        request={k:v for k,v in self.request.items() if k!='reserved_photo_ids'}
        result=self.reserve(request)
        self.assertEqual(result['status'],'not_requested')
        self.assertEqual(result['reconstruction_request'],request)
        self.assertFalse((self.root/'reservation').exists())

    def test_explicit_subset_is_owned_and_stripped_before_intake(self):
        result=self.reserve()
        self.assertEqual([p['id'] for p in result['reconstruction_request']['photos']],['p0','p1'])
        self.assertNotIn('reserved_photo_ids',result['reconstruction_request'])
        self.assertEqual([p['id'] for p in result['evaluation_photos']],['p2'])
        Path(self.photos[2]['path']).unlink()
        self.assertEqual(verify_reservation(result['receipt'])['evaluation_photo_ids'],['p2'])

    def test_additional_evaluation_and_provider_reserved(self):
        request={'schema_version':1,'photos':self.photos[:2],'initializer':{'kind':'meshy','provider_views':self.photos[2:4]},
                 'reserved_photo_ids':['p3']}
        result=self.reserve(request)
        self.assertEqual([r['id'] for r in result['reconstruction_request']['initializer']['provider_views']],['p2'])
        self.assertNotEqual(result['reconstruction_request']['initializer']['provider_views'][0]['path'],self.photos[2]['path'])

    def test_additional_evaluation_interface(self):
        result=self.reserve({'schema_version':1,'photos':self.photos[:2],'evaluation_photos':[self.photos[2]]})
        self.assertEqual(result['receipt']['evaluation_photo_ids'],['p2'])
        self.assertNotIn('evaluation_photos',result['reconstruction_request'])

    def test_duplicate_reencoding_cannot_cross_sets(self):
        copy=self.root/'reencoded.png'
        Image.open(self.photos[0]['path']).save(copy,compress_level=0)
        request={'schema_version':1,'photos':self.photos[:2],
                 'evaluation_photos':[{'id':'same','path':str(copy)}]}
        with self.assertRaisesRegex(ValueError,'Duplicate decoded'):
            self.reserve(request)
        self.assertFalse((self.root/'reservation').exists())

    def test_same_set_provider_duplicates_are_recorded_and_removed(self):
        duplicate={**self.photos[0],'id':'provider-copy'}
        result=self.reserve({**self.request,'initializer':{'kind':'meshy','provider_views':[duplicate]}})
        self.assertEqual(result['reconstruction_request']['initializer']['provider_views'],[])
        self.assertEqual(result['receipt']['aliases'],[{'id':'provider-copy','duplicate_of':'p0'}])

    def test_reservation_fails_before_io_if_two_training_photos_do_not_remain(self):
        with self.assertRaisesRegex(ValueError,'at least two'):
            self.reserve({**self.request,'reserved_photo_ids':['p1','p2']})
        self.assertFalse((self.root/'reservation').exists())

    def test_crop_reservation_reserves_its_parent_before_fit(self):
        path=self.root/'crop.png';Image.open(self.photos[2]['path']).crop((2,3,20,18)).save(path)
        crop={'id':'crop','path':str(path),'source_photo_id':'p2','crop_xyxy':[2,3,20,18]}
        result=self.reserve({'schema_version':1,'photos':self.photos[:3],'evaluation_photos':[crop]})
        self.assertEqual([p['id'] for p in result['reconstruction_request']['photos']],['p0','p1'])
        self.assertEqual(set(result['receipt']['evaluation_photo_ids']),{'p2','crop'})

    def test_forged_crop_does_not_pass_identity(self):
        request={'schema_version':1,'photos':self.photos[:3],
                 'evaluation_photos':[{**self.photos[3],'source_photo_id':'p2','crop_xyxy':[0,0,32,20]}]}
        with self.assertRaisesRegex(ValueError,'does not reproduce'):
            self.reserve(request)

    def test_reserved_usage_blocks_before_output_creation(self):
        result=self.reserve()
        with self.assertRaisesRegex(ValueError,'leaked'):
            record_photo_usage(result['receipt'],'semantic','semantic',[self.photos[2]],self.root/'usage')
        self.assertFalse((self.root/'usage').exists())

    def test_reserved_crop_usage_blocks_even_under_another_id(self):
        result=self.reserve();path=self.root/'crop.png'
        Image.open(self.photos[2]['path']).crop((2,3,20,18)).save(path)
        with self.assertRaisesRegex(ValueError,'leaked'):
            record_photo_usage(result['receipt'],'geometry','geometry',[
                {'id':'new-crop','path':str(path),'source_photo_id':'p2','crop_xyxy':[2,3,20,18]}],self.root/'usage')

    def test_unknown_usage_prevents_independence(self):
        result=self.reserve();history=self.history()
        provider=record_photo_usage(result['receipt'],'initializer','provider',self.photos[:2],self.root/'provider',external_history=history)
        frame=record_photo_usage(result['receipt'],'frame','frame',[self.photos[3]],self.root/'frame')
        verdict=verify_photo_usage(result['receipt'],[provider,frame],expected_stages={'initializer':'provider','frame':'frame'})
        self.assertFalse(verdict['independent_evaluation_eligible'])
        self.assertIn('unknown_image_source',[v['reason'] for v in verdict['issues']])

    def test_missing_stage_and_unknown_provider_cannot_claim_independence(self):
        result=self.reserve()
        stage=record_photo_usage(result['receipt'],'geometry','geometry',self.photos[:2],self.root/'geometry')
        verdict=verify_photo_usage(result['receipt'],[stage],expected_stages={'geometry':'geometry','provider':'provider'})
        self.assertEqual(verdict['status'],'unverified')
        self.assertIn('missing_stage_usage',[v['reason'] for v in verdict['issues']])

    def test_verified_history_and_all_stages_require_sealed_candidate(self):
        result=self.reserve();history=self.history()
        stage=record_photo_usage(result['receipt'],'initializer','provider',self.photos[:2],self.root/'usage',external_history=history)
        arguments=dict(expected_stages={'initializer':'provider'})
        verdict=verify_photo_usage(result['receipt'],[stage],**arguments)
        self.assertEqual(verdict['status'],'verified')
        self.assertFalse(verdict['independent_evaluation_eligible'])
        model=Path(history['model_path'])
        verdict=verify_photo_usage(result['receipt'],[stage],sealed_candidate={'path':str(model),'sha256':sha(model.read_bytes())},**arguments)
        self.assertTrue(verdict['independent_evaluation_eligible'])

    def test_tampered_snapshot_fails(self):
        result=self.reserve();stage=record_photo_usage(result['receipt'],'frame','frame',self.photos[:2],self.root/'usage')
        Path(stage['photos'][0]['snapshot_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'snapshot changed'):
            verify_photo_usage(result['receipt'],[stage],expected_stages={'frame':'frame'})

    def test_existing_glb_and_caller_declared_provider_provenance_stay_unknown(self):
        history=self.history();folder=Path(history['initializer_folder'])
        request=json.loads((folder/'request.json').read_bytes());request['kind']='existing_glb'
        save(folder/'request.json',request)
        self.assertEqual(verified_external_history(**history)['status'],'unverified')

    def test_transport_payload_tampering_invalidates_provider_history(self):
        history=self.history();self.assertEqual(verified_external_history(**history)['status'],'verified')
        file=Path(history['initializer_folder'])/'transport/http-post/request.json'
        value=json.loads(file.read_bytes());value['body_sha256']='0'*64;save(file,value)
        self.assertEqual(verified_external_history(**history)['status'],'unverified')

    def test_cached_provider_reserved_photo_is_a_detected_leak(self):
        result=self.reserve();history=self.history(selected=[self.photos[0],self.photos[2]])
        with self.assertRaisesRegex(ValueError,'cached provider'):
            record_photo_usage(result['receipt'],'initializer','provider',self.photos[:2],self.root/'usage',external_history=history)

    def test_external_boolean_is_not_a_provenance_receipt(self):
        result=self.reserve()
        with self.assertRaisesRegex(ValueError,'never a verified boolean'):
            record_photo_usage(result['receipt'],'initializer','provider',self.photos[:2],self.root/'usage',external_history={'verified':True})

    def test_later_provider_artifact_changes_do_not_rebind_a_verified_stage(self):
        result=self.reserve();history=self.history()
        stage=record_photo_usage(result['receipt'],'initializer','provider',self.photos[:2],self.root/'usage',external_history=history)
        Path(history['model_path']).write_bytes(b'changed model')
        with self.assertRaisesRegex(ValueError,'history changed'):
            verify_photo_usage(result['receipt'],[stage],expected_stages={'initializer':'provider'})

    def test_existing_source_change_is_detected_on_resume(self):
        result=self.reserve();Path(self.photos[0]['path']).write_bytes(b'different intent')
        with self.assertRaisesRegex(ValueError,'Original reserved input changed'):
            verify_reservation(result['receipt'])

    def test_no_transport_custom_backend_history_is_unverified(self):
        history=self.history()
        (Path(history['initializer_folder'])/'transport/submission.json').unlink()
        self.assertEqual(verified_external_history(**history)['status'],'unverified')

    def test_mutually_exclusive_reservation_interfaces_fail_early(self):
        with self.assertRaisesRegex(ValueError,'not both'):
            self.reserve({**self.request,'evaluation_photos':[self.photos[3]]})

    def test_prior_provider_selection_inventory_cannot_be_retroactively_reserved(self):
        result=self.reserve();history=self.history(unused=[self.photos[2]])
        self.assertEqual(len(verified_external_history(**history)['photos']),3)
        with self.assertRaisesRegex(ValueError,'cached provider'):
            record_photo_usage(result['receipt'],'initializer','provider',self.photos[:2],self.root/'usage',external_history=history)

    def test_clean_request_passes_existing_strict_intake(self):
        from reconstruction.input_bundle import prepare_input_bundle
        result=self.reserve()
        bundle=prepare_input_bundle(result['reconstruction_request'],self.root,self.root/'input')
        self.assertEqual([p['id'] for p in bundle['photos']],['p0','p1'])

    def test_two_crops_of_one_photo_are_not_two_reconstruction_views(self):
        path=self.root/'crop.png';Image.open(self.photos[0]['path']).crop((2,3,20,18)).save(path)
        crop={'id':'crop','path':str(path),'source_photo_id':'p0','crop_xyxy':[2,3,20,18]}
        with self.assertRaisesRegex(ValueError,'at least two'):
            self.reserve({'schema_version':1,'photos':[self.photos[0],crop],'evaluation_photos':[self.photos[2]]})


if __name__=='__main__':unittest.main()
