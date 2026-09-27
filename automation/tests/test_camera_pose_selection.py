import unittest
import numpy as np
from reconstruction.camera_pose_selection import (choose_camera_with_pose_support,
    front_camera_split,measure_front_camera_witness)


def arms(left,right):
    return {side:{'status':'rest_retained' if supported else 'camera_or_arm_ambiguous','retained':False,
                  'baseline_correspondence_quality':{'supported':supported}}
            for side,supported in [('left',left),('right',right)]}


def front(mean,p95,count=20):return {'measured':True,'mean_px':mean,'p95_px':p95,'points':count}


class CameraSupportTests(unittest.TestCase):
    def test_sparse_front_anchor_routing_keeps_full_fit_without_claiming_guard(self):
        for count in range(12,16):
            xy=np.column_stack((np.arange(count)*5,np.full(count,20.)))
            split=front_camera_split(xy)
            self.assertFalse(split['supported'])
            self.assertTrue(split['fit'].all())
            self.assertEqual(split['report']['camera_fit_points'],count)
            self.assertEqual(split['report']['camera_fit_scope'],'all_front_points_without_guard')
    def test_supported_guard_routes_only_training_points_to_camera_fit(self):
        xy=np.column_stack((np.arange(30)*5,np.full(30,20.)))
        split=front_camera_split(xy)
        self.assertTrue(split['supported'])
        np.testing.assert_array_equal(split['fit'],split['train'])
        self.assertFalse(np.any(split['fit']&split['holdout']))
    def test_real_miu_back_support_loss_rejects_weak_front_only_gain(self):
        result=choose_camera_with_pose_support(front(1.040860630861392,1.8516011973499855,4),
            front(.8134758885187499,1.9454191768161484,4),arms(True,True),arms(False,False),111.)
        self.assertEqual(result['selected'],'whole_scene')
    def test_real_oakley_side_visibility_swap_preserves_front_camera(self):
        result=choose_camera_with_pose_support(front(.7635144918840123,1.7400013978763582,25),
            front(.6676052568326786,1.481270423945194,25),arms(False,True),arms(True,False),154.)
        self.assertEqual(result['selected'],'front_refined')
    def test_significant_front_evidence_is_reported_without_forcing_old_camera(self):
        result=choose_camera_with_pose_support(front(4.,6.),front(.2,.6),arms(True,True),arms(True,False),100.)
        self.assertEqual(result['selected'],'front_refined');self.assertTrue(result['supported_arm_count_decreased'])
    def test_width_scaling_keeps_the_same_choice(self):
        for scale in (1.,2.,4.):
            result=choose_camera_with_pose_support(front(1.*scale,2.*scale),front(.7*scale,1.8*scale),
                arms(True,True),arms(False,False),100.*scale)
            self.assertEqual(result['selected'],'whole_scene')
    def test_front_regression_is_vetoed_even_when_more_arms_match(self):
        result=choose_camera_with_pose_support(front(.2,.6),front(2.,4.),arms(False,False),arms(True,True),100.)
        self.assertEqual(result['selected'],'whole_scene');self.assertTrue(result['front_witness_regressed'])
    def test_tail_regression_is_vetoed_despite_improved_mean(self):
        result=choose_camera_with_pose_support(front(2.,3.),front(.1,4.),arms(True,True),arms(True,True),100.)
        self.assertEqual(result['selected'],'whole_scene');self.assertTrue(result['front_witness_regressed'])
    def test_insufficient_witnesses_cannot_move_camera(self):
        result=choose_camera_with_pose_support(front(2.,4.,3),front(0.,0.,3),arms(False,False),arms(True,True),100.)
        self.assertEqual(result['selected'],'whole_scene')
    def test_frozen_photo_split_is_reused_and_actual_boundary_is_scored(self):
        xy=np.array([[x,20.] for x in range(5,86,5)])
        split=front_camera_split(xy)
        self.assertTrue(np.array_equal(split['train'],~split['holdout']))
        mask=np.zeros((50,100),bool);mask[20:30,5:86]=True
        original=measure_front_camera_witness(mask,xy,split['holdout'])
        changed=measure_front_camera_witness(np.roll(mask,4,axis=0),xy,split['holdout'])
        self.assertEqual(original['mean_px'],0.);self.assertGreater(changed['mean_px'],3.)


if __name__=='__main__':unittest.main()
