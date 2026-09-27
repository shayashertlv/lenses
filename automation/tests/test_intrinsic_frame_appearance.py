"""Moving illumination is separable only with shared surface support."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.camera import Camera
from reconstruction.mesh import load_glb_bytes, TriangleMesh
from reconstruction.raster import rasterize
from reconstruction.observations import observe_image
from reconstruction.region_proposals import _region_result
from reconstruction.intrinsic_frame_appearance import (FrameAppearancePolicy, separate_frame_tracks,
    run_intrinsic_frame_stage, _linear, _image, _material_tracks)
from reconstruction.surface_transfer import _chunks, _pack
from reconstruction.optical_group_asset import write_optical_group_candidate, read_optical_group_candidate
from reconstruction.view_scene import (Hinge, PartBinding, ViewState, mesh_geometry_sha256,
    make_view_scene_contract, pose_scene)
from test_surface_transfer import generation_glb
from test_optical_group_asset import groups_for


def directions():
    a = np.radians([0,15,35])
    return np.column_stack((np.sin(a),np.zeros(3),np.cos(a)))


class FrameSeparationTests(unittest.TestCase):
    def inputs(self):
        baseline = np.full((100,3),.12)
        baseline[0:10] = .7  # Persistent bright logo.
        samples = np.broadcast_to(baseline,(3,100,3)).copy()
        baseline[20:30] = .6  # Source bake includes one view's highlight.
        samples[2,20:30] = .6
        return samples,np.ones((3,100),bool),baseline,directions()

    def test_removes_only_moving_positive_illumination_preserves_logo_and_pattern(self):
        result = separate_frame_tracks(*self.inputs())
        np.testing.assert_array_equal(np.flatnonzero(result['corrected']),np.arange(20,30))
        np.testing.assert_array_equal(result['ratio_rgb'][:10],np.ones((10,3)))
        self.assertTrue(result['persistent'][:10].all())
        self.assertLess(result['ratio_rgb'][25,0],.6)

    def test_global_exposure_is_nuisance_not_spatial_albedo(self):
        samples,valid,baseline,view = self.inputs()
        samples[1] *= 1.15
        result = separate_frame_tracks(samples,valid,baseline,view)
        self.assertAlmostEqual(result['exposure_gain'][1],1.15)
        np.testing.assert_array_equal(np.flatnonzero(result['corrected']),np.arange(20,30))

    def test_two_views_disagreement_weak_angle_and_bad_bake_match_remain_unknown(self):
        samples,valid,baseline,view = self.inputs()
        controls = [(samples[:2],valid[:2],baseline,view[:2]),
                    (samples,valid,baseline,np.tile(view[0],(3,1))),
                    (samples,valid,np.full_like(baseline,.12),view)]
        disagreement = samples.copy(); disagreement[1,20:30] = .35
        controls.append((disagreement,valid,baseline,view))
        for args in controls:
            result = separate_frame_tracks(*args)
            self.assertFalse(result['corrected'].any())


class IntrinsicFrameStageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.texture = np.full((64,64,4),255,np.uint8); self.texture[:,:,:3] = [80,70,60]
        self.texture[8:18,8:24,:3] = [220,220,220]  # Fixed bright logo.
        self.texture[44:52,7:40,:3] = [145,30,25]  # Fixed colored detail.
        self.baked = self.texture.copy(); self.baked[22:40,24:44,:3] = [205,200,195]
        doc,binary = _chunks(generation_glb())
        image = io.BytesIO(); Image.fromarray(self.baked).save(image,format='PNG')
        blob = image.getvalue(); binary = bytearray(binary); binary.extend(b'\0'*(-len(binary)%4))
        doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(blob)}); binary.extend(blob)
        doc['images'][0]['bufferView'] = len(doc['bufferViews'])-1
        doc['materials'][0]['pbrMetallicRoughness'].update(metallicFactor=0.,roughnessFactor=.5)
        doc['buffers'][0]['byteLength'] = len(binary)
        self.raw = _pack(doc,binary)
        self.model = self.root/'source.glb'; self.model.write_bytes(self.raw)

    def photos(self, source_sha, *, articulation=None):
        mesh = load_glb_bytes(self.raw); normalized = TriangleMesh((mesh.vertices-[1,.5,0])/2,mesh.faces,mesh.parts)
        uv = np.array([[0,0],[1,0],[1,1],[0,1]],float)
        if articulation is not None:
            binding = PartBinding(mesh_geometry_sha256(mesh),tuple('left_temple' for _ in mesh.faces),
                (Hinge('left',np.array([1,.5,0]),np.array([0,1.,0])),),'synthetic source-bound temple')
            states = {f'photo-{i}':ViewState(f'photo-{i}',angle,0) for i,angle in enumerate(articulation)}
        records = []
        for index,yaw in enumerate((0,15,35)):
            actual_uv = uv
            if articulation is not None:
                posed = pose_scene(mesh,binding,states[f'photo-{index}'],compact=True)
                normalized = TriangleMesh((posed.mesh.vertices-[1,.5,0])/2,posed.mesh.faces,posed.mesh.parts)
                actual_uv = uv[posed.source_vertex_ids]
            camera = Camera(yaw,0,0,0,105,64,64)
            hit = rasterize(normalized,camera,(128,128)); rows,cols = np.nonzero(hit.mask)
            weights = hit.barycentric[rows,cols]
            coords = np.einsum('ni,nij->nj',weights,actual_uv[normalized.faces[hit.face_index[rows,cols]]])
            coords = np.clip(np.rint(coords*63).astype(int),0,63)
            rgba = np.full((128,128,4),255,np.uint8)
            texture = self.baked if index == 2 else self.texture
            rgba[rows,cols] = texture[coords[:,1],coords[:,0]]
            # Provide independent image-only evidence at adequate native
            # resolution; projected frame visibility is no longer support.
            rgba = np.asarray(Image.fromarray(rgba).resize((256,256),Image.Resampling.NEAREST)).copy()
            path = self.root/f'photo-{index}.png'; Image.fromarray(rgba).save(path)
            records.append({'id':f'photo-{index}','source':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'image_size':[256,256],'candidate_projection':{'status':'candidate_conditioned_projection',
                    'candidate_sha256':source_sha,'working_size':[128,128],'camera':camera.to_dict(),
                    'normalization':{'center':[1,.5,0],'extent':2.}}})
            observation = observe_image(rgba)
            folder = self.root/f'photo-{index}'/'contrast-object';folder.mkdir(parents=True,exist_ok=True)
            predictions = [[{'mask':observation.mask.copy(),'predicted_quality':.9} for _ in range(3)] for _ in range(5)]
            region = _region_result({'id':'contrast-object','kind':'contrast_object_region','mask':observation.mask,
                'prior':{'basis':observation.method,'semantic_identity':'unknown_object'}},
                predictions,rgba,records[-1]['source_sha256'],folder)
            region['directory']='contrast-object';records[-1]['regions']=[region]
        if articulation is not None:
            contract = make_view_scene_contract(mesh,binding,states,source_model=self.model,source_sha256=source_sha,
                photo_sha256={row['id']:row['source_sha256'] for row in records},evidence={'fixture':True})
            for row in records:
                row['candidate_projection']['view_scene'] = {'contract':contract}
        path = self.root/'regions.json'; path.write_text(json.dumps({'candidate_sha256':source_sha,'photos':records}))
        return path

    def test_real_uv_photo_correspondences_export_selective_texture_without_geometry_changes(self):
        regions = self.photos(hashlib.sha256(self.raw).hexdigest())
        report = run_intrinsic_frame_stage(self.model,None,regions,self.root/'output',policy=FrameAppearancePolicy(atlas_resolution=64))
        self.assertEqual(report['status'],'frame_appearance_candidates_available')
        self.assertEqual(len(report['candidates']),4)
        self.assertTrue(report['surface_coverage']['denominator_complete'])
        self.assertGreater(report['surface_coverage']['observed_fraction'],.5)
        self.assertGreater(report['materials'][0]['corrected_tracks'],150)
        chosen = Path(report['selected']['path']).read_bytes(); doc,binary = _chunks(chosen)
        material = doc['meshes'][0]['primitives'][0]['material']
        texture = _image(doc,binary,doc['materials'][material]['pbrMetallicRoughness']['baseColorTexture']['index'])
        np.testing.assert_array_equal(texture[9:17,9:23],self.baked[9:17,9:23])
        np.testing.assert_array_equal(texture[45:51,8:39],self.baked[45:51,8:39])
        self.assertLess(int(texture[30,32,0]),int(self.baked[30,32,0]))
        np.testing.assert_array_equal(load_glb_bytes(chosen).vertices,load_glb_bytes(self.raw).vertices)
        self.assertFalse(report['accepted'])

    def test_two_camera_coverage_retains_exact_original_baseline(self):
        regions = self.photos(hashlib.sha256(self.raw).hexdigest()); data = json.loads(regions.read_bytes()); data['photos']=data['photos'][:2]
        regions.write_text(json.dumps(data))
        report = run_intrinsic_frame_stage(self.model,None,regions,self.root/'output')
        self.assertEqual(report['status'],'insufficient_multiview_frame_support')
        self.assertEqual(Path(report['selected']['path']).read_bytes(),self.raw)
        self.assertEqual(report['surface_coverage']['observed_fraction'],0.)

    def test_articulated_surface_tracks_project_posed_barycentric_points(self):
        regions = self.photos(hashlib.sha256(self.raw).hexdigest(),articulation=(-20,-5,5))
        report = run_intrinsic_frame_stage(self.model,None,regions,self.root/'output',policy=FrameAppearancePolicy(atlas_resolution=64))
        self.assertEqual(report['status'],'frame_appearance_candidates_available')
        self.assertGreater(report['materials'][0]['corrected_tracks'],150)
        self.assertEqual(set(report['view_scenes']),{'photo-0','photo-1','photo-2'})
        evidence = np.load(self.root/'output'/report['materials'][0]['evidence']['path'])
        self.assertEqual(evidence['rest_frame_view_directions'].shape,evidence['sample_linear_rgb'].shape)

    def test_articulation_cancelling_camera_rotation_is_not_directional_evidence(self):
        regions = self.photos(hashlib.sha256(self.raw).hexdigest(),articulation=(0,15,35))
        report = run_intrinsic_frame_stage(self.model,None,regions,self.root/'output',policy=FrameAppearancePolicy(atlas_resolution=64))
        self.assertEqual(report['status'],'insufficient_multiview_frame_support')
        self.assertGreater(report['materials'][0]['three_view_tracks'],1000)
        self.assertEqual(report['materials'][0]['corrected_tracks'],0)

    def test_optical_export_declaration_and_receipt_survive_frame_texture_rewrite(self):
        doc,binary = _chunks(self.raw)
        doc['nodes'].append({'mesh':0,'translation':[3,0,0]}); doc['scenes'][0]['nodes'].append(1)
        self.model.write_bytes(_pack(doc,binary))
        source_sha = hashlib.sha256(self.model.read_bytes()).hexdigest()
        # Photos observe the original frame; the separate lens is off screen.
        regions = self.photos(source_sha)
        optical = self.root/'optical.glb'
        receipt = write_optical_group_candidate(self.model,optical,groups_for(self.model,[('lens',[1])]),
                    source_sha256=source_sha,provenance={'method':'frame fixture'})
        report = run_intrinsic_frame_stage(optical,receipt,regions,self.root/'output',policy=FrameAppearancePolicy(atlas_resolution=64))
        self.assertEqual(report['status'],'frame_appearance_candidates_available')
        for row in report['candidates']:
            updated = json.loads(Path(row['export']['path']).read_bytes())
            actual = read_optical_group_candidate(Path(row['path']),updated)
            self.assertEqual(updated['groups'],receipt['groups'])
            self.assertEqual(updated['declaration_sha256'],receipt['declaration_sha256'])
            self.assertEqual(actual['groups'][0]['group_id'],'lens')

    def test_camera_or_photo_source_mismatch_is_rejected(self):
        regions = self.photos(hashlib.sha256(self.raw).hexdigest()); data=json.loads(regions.read_bytes())
        data['photos'][0]['candidate_projection']['candidate_sha256']='0'*64; regions.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'different candidate'):
            run_intrinsic_frame_stage(self.model,None,regions,self.root/'output')
        regions = self.photos(hashlib.sha256(self.raw).hexdigest())
        (self.root/'photo-1.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash changed'):
            run_intrinsic_frame_stage(self.model,None,regions,self.root/'output')

    def test_overlapped_uvs_on_distinct_surfaces_are_excluded(self):
        doc,binary = _chunks(self.raw); doc['nodes'].append({'mesh':0,'translation':[0,0,1]}); doc['scenes'][0]['nodes'].append(1)
        raw = _pack(doc,binary); mesh=load_glb_bytes(raw)
        tracks = _material_tracks(mesh,mesh.parts,doc,binary,0,self.baked,FrameAppearancePolicy(atlas_resolution=64))
        self.assertGreater(tracks['overlapping_uv_texels_excluded'],3900)


if __name__ == '__main__':
    unittest.main()
