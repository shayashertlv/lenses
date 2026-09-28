import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.observations import observe_image
from reconstruction.region_proposals import _region_result
from reconstruction.frame_image_support import build_frame_image_support


def add_independent_regions(root, photo, *, masks=None):
    pixels = np.asarray(Image.open(photo['source']).convert('RGBA')).copy()
    observation = observe_image(pixels)
    mask = observation.mask
    folder = root/photo['id']/'contrast-object'; folder.mkdir(parents=True,exist_ok=True)
    predictions = [[{'mask':(mask if masks is None else masks[v][i]).copy(), 'predicted_quality':.9}
                    for i in range(3)] for v in range(5)]
    row = _region_result({'id':'contrast-object','kind':'contrast_object_region','mask':mask,
                         'prior':{'basis':observation.method,'semantic_identity':'unknown_object'}},
                        predictions,pixels,photo['source_sha256'],folder)
    row['directory'] = 'contrast-object'; photo['regions'] = [row]
    return row


class FrameImageSupportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup); self.root=Path(tmp.name)
        self.rgba = np.full((128,128,4),255,np.uint8)
        self.rgba[35:90,20:108,:3] = 40

    def photo(self, pixels=None):
        pixels=self.rgba if pixels is None else pixels
        path=self.root/'photo.png'; Image.fromarray(pixels).save(path)
        return {'id':'photo','source':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'image_size':[128,128]}

    def test_contrast_and_projected_geometry_alone_never_supply_positive_support(self):
        photo=self.photo(); photo['candidate_projection']={'mask_is_foreground':True}
        result=build_frame_image_support(photo,self.root)
        self.assertFalse((result['membership']==1).any())
        self.assertIn('no_independent_object_region_alternatives',result['report']['reasons'])

    def test_all_image_only_alternatives_and_source_contrast_are_required(self):
        photo=self.photo(); add_independent_regions(self.root,photo)
        result=build_frame_image_support(photo,self.root,self.root/'support')
        self.assertEqual(result['membership'][50,50],1)
        self.assertEqual(result['membership'][5,5],0)
        self.assertEqual(len(result['report']['alternatives']),15)
        self.assertEqual(result['report']['counts']['authored_exterior'],0)
        saved=Path(result['report']['membership']['path'])
        self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(),result['report']['membership']['sha256'])

    def test_dark_background_line_outside_object_consensus_is_unknown(self):
        pixels=self.rgba.copy(); pixels[10:16,30:95,:3]=30
        photo=self.photo(pixels); object_mask=np.zeros((128,128),bool);object_mask[35:90,20:108]=True
        add_independent_regions(self.root,photo,masks=[[object_mask]*3]*5)
        result=build_frame_image_support(photo,self.root)
        self.assertEqual(result['membership'][12,50],0)
        self.assertEqual(result['membership'][50,50],1)

    def test_background_or_disagreeing_alternative_is_not_selected_away(self):
        photo=self.photo(); object_mask=observe_image(self.rgba).mask
        masks=[[object_mask]*3 for _ in range(5)]; masks[4]=[object_mask,object_mask,np.ones_like(object_mask)]
        add_independent_regions(self.root,photo,masks=masks)
        result=build_frame_image_support(photo,self.root)
        self.assertFalse((result['membership']==1).any())
        self.assertIn('empty_border_or_excessive_object_alternative',result['report']['reasons'])

    def test_white_logo_and_clear_jpeg_interior_stay_unknown_without_erasing_pixels(self):
        pixels=self.rgba.copy(); pixels[45:63,45:72,:3]=255
        photo=self.photo(pixels); object_mask=np.zeros((128,128),bool);object_mask[35:90,20:108]=True
        add_independent_regions(self.root,photo,masks=[[object_mask]*3]*5)
        result=build_frame_image_support(photo,self.root)
        self.assertEqual(result['membership'][50,50],0)
        self.assertEqual(result['membership'][75,50],1)
        np.testing.assert_array_equal(np.asarray(Image.open(photo['source'])),pixels)

    def test_authored_alpha_supports_white_logo_but_excludes_translucent_and_hidden_rgb(self):
        pixels=self.rgba.copy(); pixels[:,:,3]=0;pixels[35:90,20:108,3]=255
        pixels[43:60,42:70,:3]=255
        pixels[68:85,42:70,3]=128
        photo=self.photo(pixels)
        result=build_frame_image_support(photo,self.root)
        self.assertEqual(result['membership'][50,50],1)
        self.assertEqual(result['membership'][75,50],0)
        self.assertEqual(result['membership'][5,5],2)

    def test_mask_mutation_or_projected_prompt_is_rejected(self):
        photo=self.photo(); row=add_independent_regions(self.root,photo)
        row['prompts'][0]['bbox_xyxy'][0]+=1
        with self.assertRaisesRegex(ValueError,'image-only'):
            build_frame_image_support(photo,self.root)
        row=add_independent_regions(self.root,photo)
        mask=self.root/'photo'/'contrast-object'/row['alternatives'][0][0]['mask']['path']
        Image.fromarray(np.zeros((128,128),np.uint8)).save(mask)
        with self.assertRaisesRegex(ValueError,'hash changed'):
            build_frame_image_support(photo,self.root)


# The frozen production fixture is reused only as an image/mesh generator.

from test_intrinsic_frame_appearance import IntrinsicFrameStageTests as _OriginalFixture
from reconstruction.intrinsic_frame_appearance import run_intrinsic_frame_stage,FrameAppearancePolicy
_fixture_setup, _fixture_photos = _OriginalFixture.setUp, _OriginalFixture.photos
del _OriginalFixture


class GuardedFrameIntegrationTests(unittest.TestCase):
    setUp=_fixture_setup

    def photos(self, *, independent=True):
        regions=_fixture_photos(self,hashlib.sha256(self.raw).hexdigest())
        report=json.loads(regions.read_bytes())
        for row in report['photos']:
            # Preserve the source fixture's exact pixel centers while providing
            # native resolution above the existing observation quality floor.
            path=Path(row['source'])
            with Image.open(path) as loaded:
                image=loaded.resize((256,256),Image.Resampling.NEAREST)
            image.save(path);row['image_size']=[256,256]
            row['source_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            if independent:
                add_independent_regions(self.root,row)
            else:
                row['regions']=[]
        regions.write_text(json.dumps(report))
        return regions

    def test_missing_image_support_preserves_glb_and_cannot_inflate_area(self):
        report=run_intrinsic_frame_stage(self.model,None,self.photos(independent=False),self.root/'out',
                                        policy=FrameAppearancePolicy(atlas_resolution=64))
        self.assertEqual(Path(report['selected']['path']).read_bytes(),self.raw)
        self.assertEqual(report['surface_coverage']['observed_fraction'],0.)
        self.assertTrue(report['surface_coverage']['denominator_complete'])
        material=report['materials'][0]
        self.assertEqual(material['corrected_tracks'],0)
        self.assertGreater(sum(p['unknown_image_membership'] for p in material['image_membership_exclusions']),1000)
        evidence=np.load(self.root/'out'/material['evidence']['path'])
        self.assertGreater(evidence['geometric_eligible'].sum(),1000)
        self.assertFalse(evidence['valid'].any())

    def test_supported_tracks_retain_selective_correction_and_persist_membership_arrays(self):
        report=run_intrinsic_frame_stage(self.model,None,self.photos(),self.root/'out',
                                        policy=FrameAppearancePolicy(atlas_resolution=64))
        self.assertGreater(report['materials'][0]['corrected_tracks'],150)
        self.assertGreater(report['surface_coverage']['observed_fraction'],.3)
        evidence=np.load(self.root/'out'/report['materials'][0]['evidence']['path'])
        self.assertTrue(np.all(evidence['image_membership'][evidence['valid']]==1))
        self.assertTrue(np.all(evidence['source_alpha'][evidence['valid']]==255))
        self.assertTrue(np.all(evidence['geometric_eligible'][evidence['valid']]))
        self.assertEqual(len(report['photo_image_support']),3)

    def test_misregistered_frame_over_known_white_backdrop_cannot_be_corrected_or_counted(self):
        regions=self.photos(); data=json.loads(regions.read_bytes())
        for row in data['photos']:
            row['candidate_projection']['camera']['center_x']=-42
        regions.write_text(json.dumps(data))
        report=run_intrinsic_frame_stage(self.model,None,regions,self.root/'out',
                                        policy=FrameAppearancePolicy(atlas_resolution=64))
        self.assertEqual(Path(report['selected']['path']).read_bytes(),self.raw)
        evidence=np.load(self.root/'out'/report['materials'][0]['evidence']['path'])
        self.assertGreater(evidence['geometric_eligible'].sum(),0)
        self.assertFalse(evidence['valid'].any())
        self.assertEqual(report['surface_coverage']['observed_fraction'],0.)

    def test_photo_white_pattern_is_not_darkened_or_counted_as_observed_albedo(self):
        # A display-white pattern is deliberately ambiguous with the backdrop.
        # Retain the source pattern while still correcting supported highlights.
        self.texture[8:18,8:24,:3]=255;self.baked[8:18,8:24,:3]=255
        from reconstruction.surface_transfer import _chunks,_pack
        from reconstruction.intrinsic_frame_appearance import _image
        import io
        doc,binary=_chunks(self.raw);buffer=bytearray(binary)
        stream=io.BytesIO();Image.fromarray(self.baked).save(stream,format='PNG');blob=stream.getvalue()
        buffer.extend(b'\0'*(-len(buffer)%4))
        doc['bufferViews'].append({'buffer':0,'byteOffset':len(buffer),'byteLength':len(blob)});buffer.extend(blob)
        doc['images'][0]['bufferView']=len(doc['bufferViews'])-1
        self.raw=_pack(doc,buffer);self.model.write_bytes(self.raw)
        report=run_intrinsic_frame_stage(self.model,None,self.photos(),self.root/'out',
                                        policy=FrameAppearancePolicy(atlas_resolution=64))
        self.assertGreater(report['materials'][0]['corrected_tracks'],150)
        chosen=Path(report['selected']['path']).read_bytes();doc,binary=_chunks(chosen)
        material=doc['meshes'][0]['primitives'][0]['material']
        corrected=_image(doc,binary,doc['materials'][material]['pbrMetallicRoughness']['baseColorTexture']['index'])
        np.testing.assert_array_equal(corrected[9:17,9:23],self.baked[9:17,9:23])
        evidence=np.load(self.root/'out'/report['materials'][0]['evidence']['path'])
        xy=evidence['atlas_xy'];logo=(xy[:,0]>=9)&(xy[:,0]<23)&(xy[:,1]>=9)&(xy[:,1]<17)
        self.assertFalse(evidence['valid'][:,logo].any())

    def test_missing_uv_or_untextured_frame_stays_in_area_denominator(self):
        from reconstruction.surface_transfer import _chunks,_pack
        original=self.raw
        for kind in ('uv','texture'):
            with self.subTest(kind=kind):
                doc,binary=_chunks(original)
                if kind=='uv':
                    doc['meshes'][0]['primitives'][0]['attributes'].pop('TEXCOORD_0')
                else:
                    doc['materials'][0]['pbrMetallicRoughness'].pop('baseColorTexture')
                self.raw=_pack(doc,binary);self.model.write_bytes(self.raw)
                report=run_intrinsic_frame_stage(self.model,None,self.photos(),self.root/f'out-{kind}',
                                                policy=FrameAppearancePolicy(atlas_resolution=64))
                self.assertGreater(report['surface_coverage']['frame_area'],0)
                self.assertTrue(report['surface_coverage']['denominator_complete'])
                self.assertEqual(report['surface_coverage']['authored_eligible_samples'],0)
                self.assertEqual(report['surface_coverage']['observed_fraction'],0.)
                self.assertEqual(Path(report['selected']['path']).read_bytes(),self.raw)


if __name__=='__main__':
    unittest.main()
