"""Adversarial local tests for optical role proposals and evidence lineage."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.camera import Camera
from reconstruction.lens_asset import _pack_glb
from reconstruction.mesh import load_glb_bytes
from reconstruction.part_role_inference import (infer_part_roles, make_group_declarations, rasterize_view,
                                                verify_geometry_correspondence, boundary_contact, seed_decision, RolePolicy)


def quad(x0, x1, y0, y1, z=0):
    return np.array([[x0,y0,z], [x1,y0,z], [x1,y1,z], [x0,y1,z]], dtype='<f4')


def asset(parts, *, reversed_winding=False):
    doc = dict(asset={'version':'2.0'}, scenes=[{'nodes':list(range(len(parts)))}], scene=0,
               nodes=[], meshes=[], accessors=[], bufferViews=[], buffers=[])
    binary = bytearray()
    for index, points in enumerate(parts):
        indices = np.array([[0,1,2],[0,2,3]], dtype='<u4')
        if reversed_winding:
            indices = indices[:, ::-1].copy()
        position_accessor = len(doc['accessors'])
        for array, kind, component in ((points,'VEC3',5126),(indices.reshape(-1),'SCALAR',5125)):
            data = array.tobytes()
            view = len(doc['bufferViews'])
            doc['bufferViews'].append(dict(buffer=0, byteOffset=len(binary), byteLength=len(data)))
            binary.extend(data)
            doc['accessors'].append(dict(bufferView=view, componentType=component, count=len(array), type=kind))
        doc['meshes'].append(dict(primitives=[dict(attributes={'POSITION':position_accessor}, indices=position_accessor+1)]))
        doc['nodes'].append(dict(mesh=index))
    doc['buffers']=[dict(byteLength=len(binary))]
    return _pack_glb(doc, bytes(binary))


def pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class PartRoleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        # Deliberately place rear hardware first and lens last. IDs carry no roles.
        self.parts = [quad(-.08,.08,-.1,.1,-.2), quad(-.4,.4,.4,.43), quad(-.4,.4,-.4,.4)]
        self.raw = asset(self.parts)
        self.model = self.root/'model.glb'
        self.model.write_bytes(self.raw)
        self.camera = Camera(0,0,0,0,100,63.5,63.5)
        self.mesh = load_glb_bytes(self.raw)
        face_ids, _ = rasterize_view(self.mesh, self.camera, (128,128), np.eye(4))
        silhouette = face_ids >= 0
        # The mask covers the main lens and only one bottom row of its tiny rim.
        mask = face_ids >= 4
        rim = (face_ids >= 2) & (face_ids < 4)
        row = np.where(rim)[0].max()
        mask[row] |= rim[row]
        for name, pixels in [('mask',mask),('silhouette',silhouette)]:
            Image.fromarray(pixels.astype('uint8')*255).save(self.root/(name+'.png'))
        Image.new('RGB',(128,128),'white').save(self.root/'render.png')
        self.view = dict(id='known-front', model_sha256=pin(self.model)['sha256'], width=128,height=128,
            camera=self.camera.to_dict(), world_to_render=np.eye(4).tolist(), cull_mode='front',
            image=pin(self.root/'render.png'), masks=[dict(id='lens',**pin(self.root/'mask.png'))],
            mask_status='success', silhouette=pin(self.root/'silhouette.png'))

    def test_discovers_smooth_rim_alternative_without_promoting_hidden_hardware(self):
        report = infer_part_roles(self.model, [self.view], self.root/'out')
        self.assertEqual(report['primary_groups'], [[2]])
        self.assertIn([[1,2]], [h['groups'] for h in report['hypotheses']])
        self.assertTrue(all(0 not in group for h in report['hypotheses'] for group in h['groups']))
        self.assertFalse(report['accepted'])
        self.assertTrue(report['requires_review'])
        self.assertIn('optical_seed_has_only_one_reliable_view',report['review_reasons'])

    def test_missing_mask_view_is_not_negative_evidence(self):
        missing=deepcopy(self.view)
        missing.update(id='front-no-detection', masks=[], mask_status='no_detection')
        report=infer_part_roles(self.model,[missing,self.view],self.root/'out')
        self.assertEqual(report['selected_part_indices'],[2])
        self.assertIn('one_or_more_views_have_missing_mask_evidence',report['review_reasons'])

    def test_all_missing_masks_return_no_semantic_claim(self):
        missing=deepcopy(self.view)
        missing.update(masks=[],mask_status='no_detection')
        report=infer_part_roles(self.model,[missing],self.root/'out')
        self.assertEqual(report['status'],'needs_evidence')
        self.assertEqual(report['hypotheses'],[])
        self.assertFalse(report['accepted'])

    def test_cannot_enlarge_boundary_tolerance_to_merge_whole_model(self):
        with self.assertRaisesRegex(ValueError,'bridge substantial geometry'):
            infer_part_roles(self.model,[self.view],self.root/'out',policy={'boundary_contact_relative_tolerance':1.})

    def test_one_lens_detector_dropout_does_not_veto_distinct_multiview_support(self):
        rows=[dict(view_id=name,visible_pixels=1000,mask_hits=hits,mask_purity=hits/1000)
              for name,hits in [('front',0),('left',900),('right',910)]]
        directions={'front':np.array([0.,0.,1.]),'left':np.array([.6,0.,.8]),'right':np.array([-.6,0.,.8])}
        result=seed_decision(rows,directions,RolePolicy())
        self.assertTrue(result['seed'])
        self.assertTrue(result['detector_dropout_hypothesis'])
        self.assertEqual(result['dropout_view_ids'],['front'])
        # A partially masked surface may mix frame and lens; strong other views
        # must not turn this contradiction into a detector-omission exemption.
        rows[0].update(mask_hits=350,mask_purity=.35)
        self.assertFalse(seed_decision(rows,directions,RolePolicy())['seed'])

    def test_duplicate_camera_views_cannot_rescue_an_omitted_hardware_part(self):
        rows=[dict(view_id=name,visible_pixels=1000,mask_hits=hits,mask_purity=hits/1000)
              for name,hits in [('front',0),('copy-a',900),('copy-b',900)]]
        directions={name:np.array([0.,0.,1.]) for name in ('front','copy-a','copy-b')}
        result=seed_decision(rows,directions,RolePolicy())
        self.assertFalse(result['seed'])
        self.assertEqual(result['distinct_supporting_views'],1)

    def test_hash_mutation_and_unbound_model_are_rejected(self):
        wrong=deepcopy(self.view)
        wrong['model_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'another model'):
            infer_part_roles(self.model,[wrong],self.root/'out')
        (self.root/'mask.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'Bound input changed'):
            infer_part_roles(self.model,[self.view],self.root/'out')

    def test_alignment_failure_prevents_role_inference(self):
        wrong=deepcopy(self.view)
        wrong['camera']['center_x']+=30
        with self.assertRaisesRegex(ValueError,'alignment failed'):
            infer_part_roles(self.model,[wrong],self.root/'out')

    def test_immutable_resume_verifies_artifacts_and_declarations_remain_unverified(self):
        output=self.root/'out'
        report=infer_part_roles(self.model,[self.view],output)
        self.assertEqual(report,infer_part_roles(self.model,[self.view],output))
        frame=dict(id='test-frame',units='unitless',up_axis='+Y',forward_axis='+Z',provenance={'method':'fixture'})
        declarations=make_group_declarations(report,frame,hypothesis_id='edge-alternative-001')
        self.assertEqual([m['source_part_index'] for m in declarations['groups'][0]['members']],[1,2])
        self.assertFalse(declarations['provenance']['automatic_semantic_acceptance'])
        Path(report['artifacts'][0]['path']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'Bound input changed'):
            infer_part_roles(self.model,[self.view],output)

    def test_same_location_folded_boundary_is_not_smooth_contact(self):
        common=dict(boundary_midpoints=np.array([[0.,0.,0.]]),boundary_lengths=np.array([1.]),boundary_total_length=1.)
        a=dict(common,boundary_normals=np.array([[0.,0.,1.]]))
        b=dict(common,boundary_normals=np.array([[0.,1.,0.]]))
        contact=boundary_contact(a,b,1e-5,.85)
        self.assertEqual(contact['shared_boundary_fraction'],1)
        self.assertEqual(contact['folded_fraction'],1)
        self.assertEqual(contact['normal_cosine'],0)

    def test_transfer_requires_full_bijection_and_preserved_winding(self):
        mapping=self.root/'mapping.npy'
        np.save(mapping,np.arange(6))
        declaration=dict(source_model=pin(self.model),candidate_to_source_faces=pin(mapping),max_relative_corner_error=1e-6)
        receipt=verify_geometry_correspondence(self.mesh,pin(self.model)['sha256'],declaration)
        self.assertTrue(receipt['complete_bijection'])
        reversed_mesh=load_glb_bytes(asset(self.parts,reversed_winding=True))
        with self.assertRaisesRegex(ValueError,'corners or winding'):
            verify_geometry_correspondence(reversed_mesh,'0'*64,declaration)
        np.save(mapping,np.zeros(6,dtype=int))
        declaration['candidate_to_source_faces']=pin(mapping)
        with self.assertRaisesRegex(ValueError,'unique face bijection'):
            verify_geometry_correspondence(self.mesh,'0'*64,declaration)


if __name__ == '__main__':
    unittest.main()
