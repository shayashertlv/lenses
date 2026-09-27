import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image

from reconstruction.input_bundle import prepare_input_bundle
from reconstruction.job import _inventory
from reconstruction.semantic_appearance_stage import load_job_semantic_photos, _snapshot_semantic_photos, _evidence_view_label, _with_normal_control


class SemanticReplayTests(unittest.TestCase):
    def test_optional_id_and_relative_original_use_verified_snapshot_after_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); source=root/'originals';source.mkdir()
            Image.new('RGB',(32,32),(120,40,20)).save(source/'front.png')
            Image.new('RGB',(32,32),(130,40,20)).save(source/'angled.png')
            job=root/'job';folder=job/'stages/input/attempt_1'
            bundle=prepare_input_bundle({'schema_version':1,'photos':[{'path':'front.png','view':'front'},
                {'path':'angled.png','view':'angled'}]},source,folder)
            (job/'job.json').write_text(json.dumps({'stages':{'input':[{'status':'complete',
                'directory':folder.relative_to(job).as_posix(),'artifacts':_inventory(job,folder)}]}}))
            (source/'front.png').unlink()
            photos=load_job_semantic_photos(job)
            self.assertEqual(photos[0]['id'],bundle['photos'][0]['id'])
            self.assertEqual(photos[0]['view'],'front')
            result=_snapshot_semantic_photos(photos,root/'semantic')
            self.assertEqual(result[0]['sha256'],hashlib.sha256(Path(result[0]['path']).read_bytes()).hexdigest())
            Path(photos[0]['path']).write_bytes(b'changed')
            with self.assertRaises(ValueError):load_job_semantic_photos(job)

    def test_oblique_and_top_views_do_not_get_silently_treated_as_axial_back(self):
        self.assertEqual(_evidence_view_label('back_left'),'angled')
        self.assertEqual(_evidence_view_label('front-right'),'angled')
        self.assertEqual(_evidence_view_label('top'),'unknown')
        self.assertEqual(_evidence_view_label('back'),'back')

    def test_three_slots_keep_baseline_contrary_material_and_normal_control(self):
        candidates=[{'candidate_id':str(i),'family_assignment':{'g':family}} for i,family in
                    enumerate(('uniform_tint','gradient_tint','angular_mirror'))]
        control={**candidates[0],'candidate_id':'control'}
        result=_with_normal_control(candidates,control,3)
        self.assertEqual([c['candidate_id'] for c in result],['0','2','control'])
        self.assertEqual(len(candidates),3)


if __name__=='__main__':unittest.main()
