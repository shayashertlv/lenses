import hashlib
import tempfile
from pathlib import Path
import unittest
import numpy as np
from PIL import Image
from reconstruction.multiview_intake import run_multiview_intake


class MultiviewIntakeTests(unittest.TestCase):
    def test_provider_views_are_owned_normalized_geometry_inputs_with_alpha(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);rows=[]
            for i,(name,view) in enumerate([('front','front'),('side','back_left'),('alpha','back')]):
                pixels=np.full((32,48,4),255,np.uint8);pixels[8:24,8:40,:3]=[30+i*50,80,120]
                if name=='alpha':pixels[:8,:,3]=0
                path=folder/(name+'.png');Image.fromarray(pixels).save(path)
                rows.append({'id':name,'view':view,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            report=run_multiview_intake(rows[:1],{'selected':rows[1:]},folder/'out')
            self.assertEqual([p['view'] for p in report['photos']],['front','angled','back'])
            self.assertTrue(report['photos'][2]['normalization_provenance']['normalized']['has_transparency'])
            self.assertTrue(all(Path(p['path']).parent==folder/'out'/'normalized' for p in report['photos']))
            rows[1]['sha256']='0'*64
            with self.assertRaises(ValueError):run_multiview_intake(rows[:1],{'selected':rows[1:]},folder/'bad')

    def test_pixel_duplicate_does_not_count_as_another_view(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);path=folder/'photo.png';Image.new('RGB',(32,32),'red').save(path)
            row={'id':'front','view':'front','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            report=run_multiview_intake([row],{'selected':[{**row,'id':'duplicate','view':'back'}]},folder/'out')
            self.assertEqual(len(report['photos']),1);self.assertEqual(len(report['omitted']),1)


if __name__=='__main__':unittest.main()
