"""Source hue coverage and explicit camera ambiguity for angular coatings."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.lens_appearance import srgb_to_linear
from reconstruction.segmented_coating import derive_coating_coverage


class Aperture:
    def propose(self,rgb):
        mask=np.ones(rgb.shape[:2],bool)
        return {'variants':{'full':{'mask':mask,'known_domain':mask},'crop':{'mask':mask,'known_domain':mask}}}


class SegmentedCoatingTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.root=Path(folder.name)
        self.photo=self.root/'front.png'
        rgb=np.zeros((100,200,3),np.uint8);rgb[:,:80]=[40,180,80];rgb[:,80:]=[180,40,200]
        Image.fromarray(rgb).save(self.photo)
        self.source={'photo_id':'front','local_path':str(self.photo),'sha256':hashlib.sha256(self.photo.read_bytes()).hexdigest()}
        self.rows=[{'status':'conditional_supported','view':'front','photo_id':'front','region_id':inc,
                    'role':'colored_coating_reflection','incidence_class':inc,
                    'linear_rgb_median':srgb_to_linear(np.array(color)/255.).tolist()}
                   for inc,color in [('near_normal',[40,180,80]),('oblique',[180,40,200])]]
        x,y=np.meshgrid(np.linspace(-1,1,41),[-.4,.4]);positions=np.column_stack((x.ravel(),y.ravel(),np.zeros(x.size)))
        normal=np.column_stack((np.sin(positions[:,0]),np.zeros(x.size),np.cos(positions[:,0])))
        faces=[]
        for i in range(40):faces.extend([[i,i+1,i+41],[i+1,i+42,i+41]])
        self.prepared={'shield':{'primitives':[{'positions':positions,'indices':np.array(faces),'normals':normal}]}}

    def test_source_coverage_survives_erosion_and_camera_alternatives_are_explicit(self):
        result=derive_coating_coverage(self.prepared,{'image_manifest':[self.source]}, {'observations':self.rows},Aperture())
        self.assertEqual(result['status'],'conditional_coverage_hypothesis')
        self.assertAlmostEqual(result['source_near_hue_fraction'],75/190)
        self.assertFalse(result['physical_angle_identification'])
        self.assertEqual(len(result['camera_hypotheses']),2)
        self.assertGreater(result['camera_hypotheses'][0]['proposed_plateau_end_degrees'],result['proposed_plateau_end_degrees'])
        self.assertLess(result['camera_hypotheses'][0]['proposed_oblique_peak_degrees'],89)

    def test_changed_photo_is_rejected_and_missing_hue_is_unsupported(self):
        result=derive_coating_coverage(self.prepared,{'image_manifest':[self.source]}, {'observations':self.rows[:1]},Aperture())
        self.assertEqual(result['status'],'unsupported')
        Image.new('RGB',(200,100),'white').save(self.photo)
        with self.assertRaisesRegex(ValueError,'Coverage photo changed'):
            derive_coating_coverage(self.prepared,{'image_manifest':[self.source]},{'observations':self.rows},Aperture())


if __name__=='__main__':unittest.main()
