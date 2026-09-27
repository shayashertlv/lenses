"""Source-bound material proposals, immutable recovery, and geometry invariants."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reconstruction.segmented_appearance import (validate_sampling_response, sample_regions,
    propose_appearances, run_segmented_appearance, rebind_sampling_report)
from reconstruction.photo_semantics import build_image_manifest, validate_product_hypotheses
from reconstruction.lens_appearance import LensAppearance
from reconstruction.prepare_optical_groups import run_optical_group_preparation
from reconstruction.mesh import load_glb
from test_deform_glb import fixture
from test_prepare_optical_groups import declarations
from test_photo_semantics import response


def region(box=(100,100,900,900),role='transmitted_background',incidence='unknown',height='middle'):
    return {'image_number':1,'box_yxyx_1000':list(box),'role':role,'incidence_class':incidence,
        'lens_height':height,'confidence':'high','evidence':'Controlled synthetic patch.'}


def sampling(rows):
    return {'confidence_is_uncalibrated':True,'regions':rows,'limitations':['Uncalibrated test scene.']}


class Client:
    def __init__(self):self.calls=0
    def describe(self):return {'provider':'injected_test','maximum_calls':1}
    def infer(self,manifest,prompt,schema):
        self.calls+=1
        return {'source_images':manifest,'response':sampling([region()])}


class SegmentedAppearanceTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        self.photo=self.root/'photo.png';Image.new('RGB',(80,60),'white').save(self.photo)
        self.photos=[{'id':'front','path':str(self.photo),'view':'front'}]
        self.manifest=build_image_manifest(self.photos)

    def semantics(self):
        raw=response();raw['material_interpretations'][0].update(absorption='clear',coating='ordinary',gradient_direction='none')
        return validate_product_hypotheses(raw,self.manifest)

    def test_sampling_rejects_wrong_sources_and_invalid_boxes(self):
        with self.assertRaisesRegex(ValueError,'different images'):
            validate_sampling_response({'response':sampling([region()]),'source_images':[{'sha256':'0'*64}]},self.manifest)
        with self.assertRaisesRegex(ValueError,'valid image'):
            validate_sampling_response(sampling([region(box=(900,100,100,800))]),self.manifest)

    def test_sampling_rebinds_reordered_photos_only_by_exact_pixels(self):
        second=self.root/'other.png';Image.new('RGB',(80,60),'green').save(second)
        photos=self.photos+[{'id':'angled','path':str(second)}]
        original=build_image_manifest(photos)
        spec=validate_sampling_response(sampling([region()]),original)
        target=build_image_manifest([{'id':'renamed-side','path':str(second)},self.photos[0]])
        rebound=rebind_sampling_report(spec,target)
        self.assertEqual(rebound['regions'][0]['image_number'],2)
        self.assertEqual(rebound['regions'][0]['photo_id'],'front')
        Image.new('RGB',(80,60),'blue').save(second)
        with self.assertRaisesRegex(ValueError,'unique exact-pixel'):
            rebind_sampling_report(spec,build_image_manifest([{'id':'side','path':str(second)},self.photos[0]]))

    def test_highlights_are_excluded_and_pixels_not_words_drive_colors(self):
        rgb=np.full((60,80,3),[60,160,90],np.uint8);rgb[20:40,30:50]=255
        Image.fromarray(rgb).save(self.photo);manifest=build_image_manifest(self.photos)
        spec=validate_sampling_response(sampling([region(),region((333,375,667,625),'white_highlight')]),manifest)
        measured=sample_regions(spec,{'front':'front'},self.root/'sampling')
        self.assertGreater(measured['observations'][0]['support_pixels'],32)
        self.assertEqual(measured['observations'][0]['code_rgb_median'],[60.,160.,90.])
        self.assertEqual(measured['observations'][1]['status'],'unsupported')
        self.assertIsNone(measured['observations'][0]['incidence_degrees'])

    def test_angular_hues_derive_from_measured_pixels_and_keep_control(self):
        semantics=self.semantics();semantics['hypotheses'][0].update(absorption='uncertain',coating='colored_mirror')
        rows=[]
        for ordinal,color in [('near_normal',[.05,.8,.1]),('oblique',[.7,.02,.5])]:
            rows.append({'region_id':ordinal,'status':'conditional_supported','role':'colored_coating_reflection',
                'incidence_class':ordinal,'linear_rgb_median':color})
        proposed=propose_appearances(semantics,{'observations':rows},['shield'],4)
        self.assertEqual(len(proposed['candidates']),4)
        angular=LensAppearance.from_dict(proposed['candidates'][0]['appearances']['shield'])
        self.assertGreater(angular.evaluate(.5,0).reflectance_rgb[1],angular.evaluate(.5,0).reflectance_rgb[0])
        self.assertGreater(angular.evaluate(.5,45).reflectance_rgb[0],angular.evaluate(.5,45).reflectance_rgb[1])
        self.assertEqual(proposed['candidates'][-1]['origin'],'neutral_contrary_control')

    def test_gradient_follows_sampled_lens_height_not_color_name(self):
        semantics=self.semantics();semantics['hypotheses'][0].update(absorption='gradient_tint')
        rows=[{'region_id':h,'status':'conditional_supported','role':'transmitted_background','view':'front',
            'lens_height':h,'backdrop_linear_rgb':[1,1,1],'linear_rgb_median':[c,c,c]} for h,c in [('top',.12),('bottom',.6)]]
        proposed=propose_appearances(semantics,{'observations':rows},['left','right'])
        app=LensAppearance.from_dict(proposed['candidates'][0]['appearances']['left'])
        self.assertGreater(app.evaluate(1.).optical_density_rgb[0],app.evaluate(0.).optical_density_rgb[0])
        self.assertEqual(proposed['candidates'][0]['appearances']['left'],proposed['candidates'][0]['appearances']['right'])

    def test_missing_transmission_does_not_force_colored_mirrors_to_clear_absorption(self):
        semantics=self.semantics();semantics['hypotheses'][0].update(absorption='uniform_tint',coating='colored_mirror')
        rows=[{'region_id':inc,'status':'conditional_supported','role':'colored_coating_reflection',
               'incidence_class':inc,'linear_rgb_median':color}
              for inc,color in [('near_normal',[.001,.19,.45]),('oblique',[.08,.16,.55])]]
        proposed=propose_appearances(semantics,{'observations':rows},['shield'],6)
        self.assertEqual(len(proposed['candidates']),6)
        self.assertEqual(proposed['transmission_evidence'],{'supported_patches':0,'absence_implies_clear':False,'status':'unmeasured'})
        densities=[]
        for candidate in proposed['candidates'][:4]:
            self.assertEqual(candidate['origin'],'unmeasured_transmission_bounded_absorption')
            app=LensAppearance.from_dict(candidate['appearances']['shield'])
            sample=app.evaluate(.5,0)
            self.assertGreater(sample.reflectance_rgb[2],sample.reflectance_rgb[0])
            self.assertFalse(candidate['physical_identification'])
            densities.append(sample.optical_density_rgb[0])
        self.assertEqual(densities,[1.5,3.,1.5,3.])
        self.assertEqual(proposed['candidates'][-2]['origin'],'sampled_coating_hues')
        self.assertEqual(proposed['candidates'][-1]['origin'],'neutral_contrary_control')

    def prepared(self):
        source=self.root/'source.glb';fixture(source)
        run_optical_group_preparation(source,self.root/'prepared',grouping_mode='explicit_declarations',declarations=declarations(source))
        semantics=self.root/'semantic.json';semantics.write_text(json.dumps(self.semantics()))
        return self.root/'prepared/report.json',semantics

    def test_real_export_resume_and_tamper_rejection(self):
        preparation,semantic=self.prepared();client=Client();out=self.root/'appearance'
        result=run_segmented_appearance(preparation,self.photos,out,semantic_report=semantic,client=client,maximum_candidates=3)
        self.assertEqual(client.calls,1);self.assertFalse(result['accepted'])
        before=load_glb(self.root/'prepared/prepared-neutral.glb')
        after=load_glb(result['candidates'][0]['path'])
        # Compaction may renumber vertices but every drawn triangle agrees.
        np.testing.assert_array_equal(before.vertices[before.faces],after.vertices[after.faces])
        again=run_segmented_appearance(preparation,self.photos,out,semantic_report=semantic,client=client,maximum_candidates=3)
        self.assertEqual(result,again);self.assertEqual(client.calls,1)
        candidate=Path(result['candidates'][0]['path']);candidate.write_bytes(candidate.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'artifact changed'):
            run_segmented_appearance(preparation,self.photos,out,semantic_report=semantic,client=client,maximum_candidates=3)

    def test_failed_local_export_resumes_without_repeating_inference(self):
        preparation,semantic=self.prepared();client=Client();out=self.root/'appearance'
        with patch('reconstruction.optical_group_asset.write_optical_group_candidate',side_effect=RuntimeError('interrupted')):
            with self.assertRaisesRegex(RuntimeError,'interrupted'):
                run_segmented_appearance(preparation,self.photos,out,semantic_report=semantic,client=client,maximum_candidates=3)
        self.assertEqual(client.calls,1)
        result=run_segmented_appearance(preparation,self.photos,out,semantic_report=semantic,client=client,maximum_candidates=3)
        self.assertEqual(client.calls,1);self.assertTrue(result['candidates'])


if __name__=='__main__':unittest.main()
