import copy
from dataclasses import replace
import hashlib
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction import joint_photo_lens_fit as joint
from reconstruction import photo_lens_fit as single
from reconstruction.grounded_fit_support import freeze_grounded_fit_support,apply_grounded_fit_support
from reconstruction.rear_correspondence import find_rear_template_correspondences,register_rear_template,collect_rear_transmission_anchors
from reconstruction.lens_appearance import linear_to_srgb,LensAppearance,DensityKeyframe,ReflectanceKeyframe
from test_semantic_photo_lens_fit import fixture,policy,SOURCE


class GroundedOpticalEvidenceTests(unittest.TestCase):
    def test_frozen_support_precedes_split_and_cannot_change_with_optical_residuals(self):
        groups,priors,_=fixture()
        mask=np.ones((16,32),bool);mask[:4]=False
        semantic={'image_manifest':[{'photo_id':'front','sha256':SOURCE,'image_size':[32,16]}]}
        grounding={'report':{'request_sha256':'b'*64},'fit_masks':{'front':mask}}
        priors['frozen_image_fit_support']=freeze_grounded_fit_support(semantic,grounding)
        p=replace(policy(),photo_policy=replace(policy().photo_policy,max_nfev=5))
        report=joint.fit_joint_photo_lens_candidates(groups,policy=p,appearance_priors=priors)
        self.assertTrue(all(r['excluded_image_grounding']==64 for r in report['coverage']))
        self.assertTrue(all(r['eligible']==192 for r in report['coverage']))
        self.assertNotIn('image_eligible',groups[0]['observations'][0])
        changed=copy.deepcopy(priors);changed['frozen_image_fit_support']['photos']['front']['packed_mask_sha256']='f'*64
        with patch.object(joint,'least_squares',side_effect=AssertionError('must fail before fit')):
            with self.assertRaisesRegex(ValueError,'checksum'):
                joint.fit_joint_photo_lens_candidates(groups,policy=p,appearance_priors=changed)
        self.assertIs(apply_grounded_fit_support(groups,None),groups)

    def test_nonopaque_samples_stay_excluded_even_when_grounded(self):
        groups,priors,_=fixture()
        groups[0]['observations'][0]['alpha_code']=np.full(256,127,np.uint8)
        _,records,_,_,_=joint._prepare_joint(groups,policy())
        self.assertFalse(next(r for r in records if r['group_id']=='g0')['eligible'].any())

    def test_observed_continuation_recovers_transmission_and_censors_clipped_reference(self):
        for background in (.8,1.):
            image=np.full((140,400,3),background);image[62:71]=.12
            aperture=np.zeros((140,400),bool);aperture[20:120,110:300]=True
            truth=np.array([.7,.45,.3]);image[aperture]=image[aperture]*truth+.04
            rgb=np.rint(linear_to_srgb(image)*255).astype(np.uint8)
            matches=find_rear_template_correspondences(rgb,aperture)
            self.assertTrue(matches)
            self.assertEqual(find_rear_template_correspondences(rgb,aperture,opaque_mask=np.zeros_like(aperture)),[])
            best=matches[0]
            np.testing.assert_allclose(best['transmission']['transmission_rgb'],truth,atol=.025)
            interval=np.asarray(best['transmission']['transmission_interval_rgb'])
            self.assertTrue(np.all(interval[:,0]<=truth)&np.all(interval[:,1]>=truth))
            self.assertEqual(best['template_source'],'observed_outside_aperture_pixels_only')
            if background==1:
                np.testing.assert_array_equal(interval[:,0],0)

    def test_registration_does_not_create_evidence_without_contrast(self):
        target=np.full((24,24,3),.4);template=np.full_like(target,.7)
        result=register_rear_template(target,template,np.ones((24,24),bool))
        self.assertEqual(result['status'],'unsupported')

    def test_geometry_must_independently_establish_rear_support(self):
        image=np.full((140,400,3),.8);image[62:71]=.12
        aperture=np.zeros((140,400),bool);aperture[20:120,110:300]=True
        image[aperture]=image[aperture]*[.7,.45,.3]+.04
        rgb=np.rint(linear_to_srgb(image)*255).astype(np.uint8)
        rgba=np.dstack((rgb,np.full(aperture.shape,255,np.uint8)))
        yy,xx=np.mgrid[35:100:2,115:295:2];xy=np.column_stack((xx.ravel(),yy.ravel()))
        source='a'*64
        observation={'photo_id':'front','source_sha256':source,'xy':xy,'intrinsic_v':xy[:,1]/140.,
                     'incidence_degrees':np.full(len(xy),20.),'rear_weight':((xy[:,1]>=62)&(xy[:,1]<71)).astype(float)}
        groups=[{'surface_binding':{'material_group_id':'lens','prepared_glb_sha256':'b'*64},'observations':[observation]}]
        semantic={'image_manifest':[{'photo_id':'front','sha256':source}],'regions':[]}
        grounding={'images':{'front':rgba},'aperture_masks':{'front':aperture}}
        result=collect_rear_transmission_anchors(semantic,grounding,groups)
        self.assertTrue(result['anchors_by_group']['lens'])
        observation['rear_weight'][:]=0
        result=collect_rear_transmission_anchors(semantic,grounding,groups)
        self.assertEqual(result['anchors_by_group'],{})

    def test_registered_back_photo_has_independent_reflection_and_reciprocal_transmission(self):
        groups,priors,_=fixture(mirror=True)
        groups=groups[:1];priors['groups']={'g0':priors['groups']['g0']};priors['groups']['g0']['density_anchors']=[]
        priors['photos']['front']['interface']='rear';priors['photos']['front']['reflection_regions']=[]
        p=replace(policy(),photo_policy=replace(policy().photo_policy,families=('angular_mirror',)))
        bindings,records,branches,_,_=joint._prepare_joint(groups,p)
        priors=single.validate_appearance_priors(priors,records,bindings)
        model=joint._JointModel(branches[0],{'g0':'angular_mirror'},'semantic_softbox',{'g0':'rear_content_excluded'},p.photo_policy,{},priors)
        x=model.initial(1);local=model.models['g0'];indices=model.maps['g0'];vector=x[indices]
        vector[local.slices['density']]=[.2,.5,.7]
        vector[local.slices['reflection']]=[.3,.6,.2,.5,.3,.4,.8,.9,.7]
        vector[local.slices['rear_reflection_fraction']]=[.1,.8,.3]
        vector[local.slices[('front','environment')]]=.4
        data=local.data[0];T=local.transmission(vector,data['arrays']['v'],data['arrays']['angle'])
        expected=T*data['arrays']['background']+(1-T)*[.1,.8,.3]*.4
        np.testing.assert_allclose(local.predict(vector,data),single._encode_unclamped(expected),rtol=0,atol=1e-10)
        appearance=local.appearance(vector,.05)
        np.testing.assert_allclose(appearance.normal_reflectance_rgb,[.3,.6,.2])
        self.assertIn(('material','g0','rear_reflection_fraction'),model.blocks)
        np.testing.assert_allclose(appearance.rear_reflection_fraction_rgb,[.1,.8,.3])
        exported=appearance.evaluate(data['arrays']['v'],data['arrays']['angle'],side='rear')
        np.testing.assert_allclose(exported.compose(data['arrays']['background'],[.4]*3),expected,atol=1e-12)

    def test_image_only_rear_constraint_retains_angle_and_reflection_nuisance(self):
        groups,priors,_=fixture();groups=groups[:1];priors['groups']={'g0':priors['groups']['g0']}
        priors['groups']['g0']['density_anchors']=[]
        priors['photos']['back']={**copy.deepcopy(priors['photos']['front']),'source_sha256':'c'*64}
        priors['source_image_sha256'].append('c'*64)
        priors['groups']['g0']['rear_image_constraints']=[{'photo_id':'back','source_sha256':'c'*64,'prepared_glb_sha256':'a'*64,
            'region_id':'r','linear_rgb_interval':[[.5,.6],[.15,.25],[.25,.35]],'backdrop_linear_rgb_interval':[[.9,1.]]*3,
            'incidence_range_degrees':[0.,75.],'maximum_reflected_radiance':.3,'maximum_exposure_ratio':2.,
            'sigma_linear_rgb':.04,'weight':1.,'status':'conditional_radiance_interval_not_calibrated_transmission'}]
        p=replace(policy(),photo_policy=replace(policy().photo_policy,families=('angular_mirror',),max_nfev=10))
        report=joint.fit_joint_photo_lens_candidates(groups,policy=p,appearance_priors=priors)
        self.assertFalse(report['exploration']['failed_runs'])
        self.assertEqual({c['assumptions']['rear_response_hypothesis'] for c in report['candidates']},
                         {'free_rear_reflection','weak_rear_reflection'})
        from reconstruction.joint_photo_lens_stage import joint_preview_representatives
        self.assertEqual(len(joint_preview_representatives(report,include_material_relations=True)),2)
        for candidate in report['candidates']:
            rear=next(n for n in candidate['shared_nuisance_by_photo'] if n.get('nuisance_kind')=='rear_radiance_constraint')
            self.assertEqual(rear['rear_reflection_exported_as'],'rear_reflection_fraction_rgb')
            self.assertTrue(0<=rear['incidence_hypothesis_degrees']<=75)
            if candidate['assumptions']['rear_response_hypothesis']=='weak_rear_reflection':
                self.assertTrue(all(v<=.15 for v in candidate['groups']['g0']['appearance']['rear_reflection_fraction_rgb']))
        bindings,records,branches,_,_=joint._prepare_joint(groups,p)
        model=single._Model(branches[0],'angular_mirror','semantic_softbox','rear_content_excluded',p.photo_policy,priors,'g0',
                            rear_response_hypothesis='weak_rear_reflection')
        first=model.initial(0);contrary=model.initial(2)
        self.assertGreater(np.ptp(first[model.slices['density']]),.5)
        self.assertEqual(np.ptp(contrary[model.slices['density']]),0)
        priors['groups']['g0']['rear_image_constraints'][0]['prepared_glb_sha256']='e'*64
        with self.assertRaisesRegex(ValueError,'geometry'):
            joint.fit_joint_photo_lens_candidates(groups,policy=p,appearance_priors=priors)


if __name__=='__main__':
    unittest.main()
