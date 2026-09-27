import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from reconstruction.photo_semantics import build_image_manifest
from reconstruction.segmented_appearance_review import run_segmented_appearance_review, _response


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Client:
    def __init__(self,stable=True):self.calls=0;self.stable=stable
    def describe(self):return {'provider':'injected_test','maximum_calls':2}
    def infer(self,manifest,prompt,schema):
        winner='A' if self.calls==0 or not self.stable else 'B';self.calls+=1
        return {'response':{'winner':winner,'assessments':[{'candidate':c,'match':'plausible','defects':[],
            'evidence':['Controlled fixture.']} for c in ('A','B')],'limitations':['Not physical identification.']}}


class AppearanceReviewTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        photo=self.root/'source.png';Image.new('RGB',(32,32),'white').save(photo)
        manifest=build_image_manifest([{'id':'front','path':str(photo)}])
        (self.root/'semantics.json').write_text(json.dumps({'image_manifest':manifest}))
        candidates=[];cards=[]
        for i,color in enumerate(('red','green')):
            model=self.root/f'model-{i}.glb';model.write_bytes(bytes([i]))
            card=self.root/f'card-{i}.png';Image.new('RGB',(80,60),color).save(card)
            candidates.append({'candidate_id':str(i),'path':str(model),'sha256':sha(model),'accepted':False})
            cards.append({'id':str(i),'path':str(card),'sha256':sha(card),'model_sha256':sha(model),
                'environment':'broad','mode':'actual-ar','columns':['front'],'rows':['checker'],'blind':True})
        self.appearance=self.root/'appearance.json';self.appearance.write_text(json.dumps({'candidates':candidates}))
        self.cards=self.root/'cards.json';self.cards.write_text(json.dumps({'cards':cards}))

    def test_stable_choice_is_conditional_and_cached(self):
        client=Client();out=self.root/'review'
        result=run_segmented_appearance_review(self.appearance,self.cards,out,client=client)
        self.assertEqual(result['status'],'conditional_stable_preference');self.assertFalse(result['accepted'])
        self.assertEqual(result['selected_candidate_id'],'0');self.assertEqual(client.calls,2)
        self.assertEqual(result,run_segmented_appearance_review(self.appearance,self.cards,out,client=client))
        self.assertEqual(client.calls,2)

    def test_disagreement_returns_explicit_prior_fallback(self):
        result=run_segmented_appearance_review(self.appearance,self.cards,self.root/'review',client=Client(False))
        self.assertEqual(result['status'],'unresolved_prior_fallback');self.assertTrue(result['fallback_used'])
        self.assertIsNone(result['review_winner']);self.assertFalse(result['accepted'])

    def test_changed_card_and_unknown_winner_are_refused(self):
        Image.new('RGB',(80,60),'blue').save(self.root/'card-0.png')
        with self.assertRaisesRegex(ValueError,'card bytes'):
            run_segmented_appearance_review(self.appearance,self.cards,self.root/'review',client=Client())
        with self.assertRaisesRegex(ValueError,'not a shown candidate'):
            _response({'winner':'C'},{'A':'0','B':'1'})

    def test_unanimous_rejection_is_not_a_plausible_prior_fallback(self):
        class Rejected(Client):
            def infer(self,manifest,prompt,schema):
                value=super().infer(manifest,prompt,schema)
                value['response']['winner']='neither'
                for row in value['response']['assessments']:row['match']='poor'
                return value
        result=run_segmented_appearance_review(self.appearance,self.cards,self.root/'review',client=Rejected())
        self.assertEqual(result['status'],'no_supported_appearance_candidate')
        self.assertTrue(result['all_candidates_rejected'])
        self.assertFalse(result['supported_candidate_ids'])
        self.assertFalse(result['accepted'])


if __name__=='__main__':unittest.main()
