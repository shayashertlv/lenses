"""No-network provider recovery: paid tasks, receipts and binary mask lineage."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction import segmented_providers as providers
from qa import provider_benchmark as transport
from test_provider_benchmark import triangle_glb


def png(value=255, size=(32,24)):
    buffer=BytesIO()
    Image.new('L',size,value).save(buffer,format='PNG')
    return buffer.getvalue()


class Client:
    def __init__(self,replies=(),artifact=None):
        self.replies=list(replies)
        self.artifact=png() if artifact is None else artifact
        self.calls=[]
        self.downloads=[]

    def api(self,method,url,provider,**kwargs):
        self.calls.append((method,url,provider))
        if not self.replies:
            raise AssertionError('Unexpected provider API call')
        return deepcopy(self.replies.pop(0))

    def download(self,url,destination,**kwargs):
        self.downloads.append(url)
        destination.write_bytes(self.artifact)
        return providers.pin(destination)


class ProviderHardeningTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        source=self.root/'render.png'
        source.write_bytes(png())
        self.image=providers.pin(source)
        self.directory=self.root/'provider'

    def complete_mask(self,artifact=None):
        client=Client([(200,{'request_id':'sam-task'}),(200,{'status':'COMPLETED'}),
                       (200,{'masks':[{'url':'https://artifact.invalid/mask.png'}]})],artifact)
        result=providers.advance_mask(self.directory,self.image,client=client,budget=providers.SubmissionBudget(1))
        self.assertEqual(result['status'],'complete')
        return result,client

    def test_mask_completion_reuses_without_network_and_detects_result_mutation(self):
        result,client=self.complete_mask()
        self.assertEqual(providers.advance_mask(self.directory,self.image),result)
        self.assertEqual(len(client.downloads),1)
        path=self.directory/'result.json'
        receipt=transport.read_json(path)
        receipt['response']['masks']=[]
        transport.write_json(path,receipt)
        with self.assertRaisesRegex(ValueError,'changed'):
            providers.advance_mask(self.directory,self.image,client=Client())

    def test_zero_one_provider_mask_normalized_and_original_bytes_preserved(self):
        result,_=self.complete_mask(png(value=1))
        item=result['masks'][0]
        with Image.open(item['path']) as image:
            self.assertEqual(set(np.asarray(image).ravel()),{255})
        with Image.open(item['provider_image']['path']) as image:
            self.assertEqual(set(np.asarray(image).ravel()),{1})

    def test_existing_result_bound_legacy_completion_is_read_only_compatible(self):
        result,_=self.complete_mask()
        (self.directory/'completion.json').unlink()
        path=self.directory/'artifacts.json'
        artifacts=transport.read_json(path)
        for mask in artifacts['masks']:
            mask.pop('provider_image'); mask.pop('source_url_sha256')
        transport.write_json(path,artifacts)
        before={p.name:p.read_bytes() for p in self.directory.iterdir() if p.is_file()}
        self.assertEqual(providers.advance_mask(self.directory,self.image,client=Client())['status'],'complete')
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.directory.iterdir() if p.is_file()})

    def test_legacy_mask_hash_is_not_enough_when_dimensions_are_wrong(self):
        self.complete_mask()
        (self.directory/'completion.json').unlink()
        path=self.directory/'artifacts.json'; artifacts=transport.read_json(path)
        mask=Path(artifacts['masks'][0]['path']); mask.write_bytes(png(size=(8,8)))
        artifacts['masks'][0]=dict(id='mask-00',**providers.pin(mask))
        transport.write_json(path,artifacts)
        with self.assertRaisesRegex(ValueError,'dimensions'):
            providers.advance_mask(self.directory,self.image)

    def test_failed_sam_task_is_terminal_and_never_polled_or_submitted_again(self):
        client=Client([(200,{'request_id':'sam-task'}),(200,{'status':'FAILED'})])
        result=providers.advance_mask(self.directory,self.image,client=client,budget=providers.SubmissionBudget(1))
        self.assertEqual(result['status'],'provider_failed')
        again=Client()
        self.assertEqual(providers.advance_mask(self.directory,self.image,client=again,budget=providers.SubmissionBudget(4)),result)
        self.assertEqual(again.calls,[])

    def test_transient_result_failure_retries_only_get_on_exact_existing_task(self):
        client=Client([(200,{'request_id':'sam-task'}),(200,{'status':'COMPLETED'}),(503,{'error':'temporary'})])
        self.assertEqual(providers.advance_mask(self.directory,self.image,client=client,budget=providers.SubmissionBudget(1))['status'],'pending')
        self.assertFalse((self.directory/'result.json').exists())
        later=Client([(200,{'status':'COMPLETED'}),(200,{'masks':[]})])
        budget=providers.SubmissionBudget(0)
        result=providers.advance_mask(self.directory,self.image,client=later,budget=budget)
        self.assertEqual(result['mask_status'],'no_detection')
        self.assertTrue(all(method=='GET' and 'sam-task' in url for method,url,_ in later.calls))
        self.assertEqual(budget.used,0)

    def test_reservation_payload_mismatch_rejected_before_network(self):
        client=Client([(200,{'request_id':'sam-task'}),(200,{'status':'IN_QUEUE'})])
        providers.advance_mask(self.directory,self.image,client=client,budget=providers.SubmissionBudget(1))
        path=self.directory/'submission-reserved.json'
        receipt=transport.read_json(path); receipt['payload_sha256']='0'*64
        transport.write_json(path,receipt)
        with self.assertRaisesRegex(ValueError,'payload changed'):
            providers.advance_mask(self.directory,self.image,client=Client())

    def test_unreceipted_existing_mask_is_not_adopted_as_provider_output(self):
        client=Client([(200,{'request_id':'sam-task'}),(200,{'status':'IN_QUEUE'})])
        providers.advance_mask(self.directory,self.image,client=client,budget=providers.SubmissionBudget(1))
        (self.directory/'mask-00.png').write_bytes(png(value=0))
        later=Client([(200,{'status':'COMPLETED'}),(200,{'masks':[{'url':'https://artifact.invalid/mask.png'}]})])
        with self.assertRaisesRegex(ValueError,'differs from receipted'):
            providers.advance_mask(self.directory,self.image,client=later,budget=providers.SubmissionBudget(0))
        self.assertEqual(len(later.downloads),1)
        self.assertTrue(all(c[0]=='GET' for c in later.calls))

    def test_tripo_completion_task_identity_checked_before_cached_reuse(self):
        request=providers.tripo_request('fixture','segment',input_task='source-task')
        client=Client([(200,{'code':0,'data':{'task_id':'seg-task'}}),
                       (200,{'code':0,'data':{'task_id':'seg-task','status':'success','credits_consumed':40,
                                             'output':{'model_url':'https://artifact.invalid/model.glb'}}})],triangle_glb())
        self.assertEqual(providers.advance_tripo(self.directory,request,client=client,budget=providers.SubmissionBudget(1))['status'],'complete')
        (self.directory/'completion.json').unlink()
        path=self.directory/'result.json'; result=transport.read_json(path)
        result['response']['data']['task_id']='another-task'
        transport.write_json(path,result)
        with self.assertRaisesRegex(ValueError,'successful task'):
            providers.advance_tripo(self.directory,request,client=Client())

    def test_tripo_failed_task_is_durable_and_http_rejection_with_id_is_not_polled(self):
        request=providers.tripo_request('fixture','segment',input_task='source-task')
        failed=Client([(200,{'code':0,'data':{'task_id':'seg-task'}}),
                       (200,{'code':0,'data':{'task_id':'seg-task','status':'failed'}})])
        result=providers.advance_tripo(self.directory,request,client=failed,budget=providers.SubmissionBudget(1))
        self.assertEqual(result['status'],'provider_failed')
        self.assertEqual(providers.advance_tripo(self.directory,request,client=Client()),result)
        rejected=Client([(403,{'code':0,'data':{'task_id':'not-accepted'}})])
        other=self.root/'rejected'
        self.assertEqual(providers.advance_tripo(other,request,client=rejected,budget=providers.SubmissionBudget(1))['status'],'rejected')
        self.assertEqual(providers.advance_tripo(other,request,client=Client())['status'],'rejected')
        self.assertEqual(len(rejected.calls),1)


if __name__=='__main__':
    unittest.main()
