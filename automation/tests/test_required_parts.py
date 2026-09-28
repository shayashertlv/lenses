import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.automatic_articulation import infer_automatic_part_bindings
from reconstruction.camera import Camera
from reconstruction.compact_glb import compact_glb_bytes
from reconstruction.lens_asset import _pack_glb
from reconstruction.mesh import TriangleMesh, load_glb
from reconstruction.raster import rasterize
from reconstruction.view_scene import (PartBinding, ViewState, make_view_scene_contract,
                                      mesh_geometry_sha256, pose_scene)
from reconstruction.required_parts import (aggregate_required_parts,
    build_alpha_object_support, exact_final_role_binding, exclusive_edge_matches,
    build_region_object_support, measure_required_parts, measure_view_parts, object_support_arrays, read_object_support,
    replay_required_parts, run_required_parts_stage)


def fixture():
    vertices, faces = [], []
    template = np.array([[0,1,3],[0,3,2],[4,6,7],[4,7,5],[0,4,5],[0,5,1],
                         [2,3,7],[2,7,6],[0,2,6],[0,6,4],[1,5,7],[1,7,3]])
    for i, (lo, hi) in enumerate([((-.5,-.13,-.035),(.5,.13,.035)),
        ((-.48,.04,-.90),(-.44,.08,-.035)),((.44,.04,-.90),(.48,.08,-.035))]):
        vertices.extend([[x,y,z] for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])])
        faces.extend(template+8*i)
    mesh = TriangleMesh(np.array(vertices), np.array(faces),
        [{'face_start':i*12,'face_count':12,'vertex_start':i*8,'vertex_count':8,'name':'lens'} for i in range(3)])
    return mesh


def write_mesh(path, mesh):
    p = np.asarray(mesh.vertices, '<f4'); f = np.asarray(mesh.faces, '<u4').ravel()
    binary = p.tobytes()+f.tobytes()
    doc = {'asset':{'version':'2.0'},'buffers':[{'byteLength':len(binary)}],
        'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':p.nbytes},
                       {'buffer':0,'byteOffset':p.nbytes,'byteLength':f.nbytes}],
        'accessors':[{'bufferView':0,'componentType':5126,'type':'VEC3','count':len(p)},
                     {'bufferView':1,'componentType':5125,'type':'SCALAR','count':len(f)}],
        'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1}]}],
        'nodes':[{'mesh':0}],'scenes':[{'nodes':[0]}],'scene':0}
    Path(path).write_bytes(_pack_glb(doc,binary))
    return load_glb(path)


def pin(path):
    return {'path':str(Path(path).resolve()),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def cameras():
    return [Camera(145.,25.,0.,0.,150.,160.,130.),Camera(215.,25.,0.,0.,150.,160.,130.)]


def supported_views(mesh, binding):
    rows = []
    for i, camera in enumerate(cameras()):
        state = ViewState(str(i))
        mask = rasterize(pose_scene(mesh,binding,state,compact=True).mesh,camera,(260,320)).mask
        support = object_support_arrays(np.where(mask,1,2).astype(np.uint8))
        result, arrays = measure_view_parts(mesh,binding,mesh,binding,camera,state,support)
        rows.append(result)
    return rows


def region_fixture(root, photo, mask, photo_id='photo'):
    from reconstruction.observations import observe_image
    from reconstruction.region_proposals import _region_result
    pixels = np.asarray(Image.open(photo).convert('RGBA'))
    observation = observe_image(pixels)
    folder = root/photo_id/'contrast-object'; folder.mkdir(parents=True)
    variants = [[{'mask':mask.copy(),'predicted_quality':.99} for _ in range(3)] for _ in range(5)]
    row = _region_result({'id':'contrast-object','kind':'contrast_object_region','mask':observation.mask,
        'prior':{'basis':observation.method,'semantic_identity':'unknown_object'}},variants,pixels,pin(photo)['sha256'],folder)
    row['directory']='contrast-object'
    report = {'photos':[{'id':photo_id,'source':str(photo),'source_sha256':pin(photo)['sha256'],
        'image_size':[pixels.shape[1],pixels.shape[0]],'regions':[row]}]}
    path=root/'regions.json';path.write_text(json.dumps(report))
    return pin(path)


class RequiredPartsTests(unittest.TestCase):
    def test_explicit_null_role_contract_routes_to_replayable_unmeasured_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'source.glb';write_mesh(model,fixture())
            refinement=root/'refinement.json'
            refinement.write_text(json.dumps({'view_scene':None,'views':[{'view_id':'front',
                'camera_fit':{'camera':cameras()[0].to_dict()},'image_size_working':[320,260]}]}))
            regions=root/'regions.json';regions.write_text(json.dumps({'photos':[{'id':'front'}]}))
            result=run_required_parts_stage(model,pin(refinement),pin(regions),root/'parts')
            self.assertIsNone(result['required_parts_present'])
            self.assertEqual(len(result['omitted_views']),1)
            self.assertTrue(all(row['status']=='unmeasured' for row in result['roles'].values()))
            replayed=replay_required_parts(model,result['measurement'],pin(refinement))
            self.assertIsNone(replayed['required_parts_present'])

    def test_jpeg_delivery_helper_and_trusted_region_replay_reach_parts_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source_path=root/'source.glb';mesh=write_mesh(source_path,fixture())
            binding=infer_automatic_part_bindings(mesh)['bindings'][0]
            states,hashes,views,photos={},{},[],[]
            for i,camera in enumerate(cameras()):
                name='photo'+str(i);states[name]=ViewState(name)
                mask=rasterize(mesh,camera,(260,320)).mask
                rgb=np.full((260,320,3),255,np.uint8);rgb[mask]=30
                photo=root/(name+'.jpg');Image.fromarray(rgb).save(photo,quality=98)
                hashes[name]=pin(photo)['sha256']
                region_ref=region_fixture(root,photo,mask,photo_id=name)
                photos.extend(json.loads(Path(region_ref['path']).read_bytes())['photos'])
                views.append({'view_id':name,'camera_fit':{'camera':camera.to_dict()},'image_size_working':[320,260]})
            region_path=root/'regions.json';region_path.write_text(json.dumps({'photos':photos}));region_ref=pin(region_path)
            contract=make_view_scene_contract(mesh,binding,states,source_model=source_path,
                source_sha256=pin(source_path)['sha256'],photo_sha256=hashes,evidence={})
            refinement=root/'refinement.json';refinement.write_text(json.dumps({'view_scene':contract,'views':views}))
            result=run_required_parts_stage(source_path,pin(refinement),region_ref,root/'parts')
            self.assertTrue(result['required_parts_present'],result['roles'])
            replayed=replay_required_parts(source_path,result['measurement'],pin(refinement),trusted_region_references=[region_ref])
            self.assertTrue(replayed['required_parts_present'])
            with self.assertRaisesRegex(ValueError,'trusted current-job region'):
                replay_required_parts(source_path,result['measurement'],pin(refinement))

    def test_jpeg_region_replay_uses_all_image_only_alternatives_and_excludes_line(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);rgb=np.full((128,128,3),255,np.uint8)
            rgb[35:90,20:108]=40;rgb[10:16,30:95]=30
            photo=root/'photo.jpg';Image.fromarray(rgb).save(photo,quality=96)
            mask=np.zeros((128,128),bool);mask[35:90,20:108]=True
            reference=region_fixture(root,photo,mask)
            receipt=build_region_object_support(reference,'photo',root/'support')
            support,report=read_object_support(receipt)
            self.assertEqual(support['membership'][50,50],1)
            self.assertEqual(support['membership'][12,50],0)
            self.assertEqual(support['membership'][5,5],0)
            self.assertEqual(len(report['basis']['alternatives']),15)
            # Naming the dark studio line "temple" cannot add foreground pixels.
            doc=json.loads(Path(reference['path']).read_bytes())
            doc['photos'][0]['regions'][0]['semantic_identity']='right_temple'
            Path(reference['path']).write_text(json.dumps(doc))
            with self.assertRaisesRegex(ValueError,'input changed'):
                read_object_support(receipt)

    def test_positive_two_views_require_actual_edge_extent(self):
        mesh = fixture(); binding = infer_automatic_part_bindings(mesh)['bindings'][0]
        result = aggregate_required_parts(binding,supported_views(mesh,binding))
        self.assertTrue(result['required_parts_present'],result)

    def test_missing_arm_fails_despite_surviving_named_parts(self):
        source = fixture(); binding = infer_automatic_part_bindings(source)['bindings'][0]
        missing = TriangleMesh(source.vertices,source.faces[:24],source.parts)
        final_binding, _ = exact_final_role_binding(source,binding,missing)
        result = aggregate_required_parts(final_binding,[])
        self.assertFalse(result['required_parts_present'])
        self.assertEqual(result['roles']['right_temple']['status'],'absent')

    def test_copied_arm_cannot_reuse_source_triangle_inventory(self):
        source = fixture(); binding = infer_automatic_part_bindings(source)['bindings'][0]
        copied = TriangleMesh(source.vertices,np.concatenate([source.faces[:24],source.faces[12:24]]),[])
        result, report = exact_final_role_binding(source,binding,copied)
        self.assertIsNone(result); self.assertIn('duplicated_final',report['reason'])

    def test_coincident_conflicting_roles_are_ambiguous(self):
        source = fixture(); binding = infer_automatic_part_bindings(source)['bindings'][0]
        source.faces[24:] = source.faces[12:24]
        binding = PartBinding(mesh_geometry_sha256(source),binding.face_roles,binding.hinges,binding.provenance)
        result, report = exact_final_role_binding(source,binding,source)
        self.assertIsNone(result); self.assertIn('conflicting_roles',report['reason'])

    def test_lod_or_shifted_new_geometry_does_not_receive_nearest_roles(self):
        source = fixture(); binding = infer_automatic_part_bindings(source)['bindings'][0]
        lod = TriangleMesh(source.vertices.copy(),source.faces.copy(),[]); lod.vertices[10,0] += .001
        result, report = exact_final_role_binding(source,binding,lod)
        self.assertIsNone(result); self.assertIn('no_exact_source',report['reason'])

    def test_one_photographed_edge_cannot_support_two_arms(self):
        support = {'edge_xy':np.array([[5.,5.]]),'edge_normals':np.array([[1.,0.]])}
        ids,_ = exclusive_edge_matches(np.array([[5.,5.],[5.,5.]]),np.array([[1.,0.],[1.,0.]]),
                                      np.array(['left_temple','right_temple']),support,1.,.9)
        self.assertEqual(np.count_nonzero(ids>=0),1)

    def test_background_line_has_no_positive_object_membership(self):
        mesh = fixture(); binding = infer_automatic_part_bindings(mesh)['bindings'][0]
        camera = cameras()[0]
        actual = rasterize(mesh,camera,(260,320)).mask
        support = object_support_arrays(np.where(actual,1,2))
        # Keep perfectly aligned gradient coordinates, but mark their membership
        # unknown. They could be studio lines; gradient distance cannot pass.
        support['membership'][:] = 0
        row,_ = measure_view_parts(mesh,binding,mesh,binding,camera,ViewState('line'),support)
        self.assertTrue(all(not r['supported_bins'] for r in row['roles'].values()))
        self.assertIsNone(aggregate_required_parts(binding,[row,row])['required_parts_present'])

    def test_wholly_occluded_arm_is_unmeasured_and_has_no_first_hit_samples(self):
        mesh = fixture(); binding = infer_automatic_part_bindings(mesh)['bindings'][0]
        camera = Camera(0.,0.,0.,0.,150.,160.,130.)
        mask = rasterize(mesh,camera,(260,320)).mask
        support = object_support_arrays(np.where(mask,1,2))
        row, arrays = measure_view_parts(mesh,binding,mesh,binding,camera,ViewState('front'),support)
        self.assertFalse(np.isin(arrays['role'],['left_temple','right_temple']).any())
        result = aggregate_required_parts(binding,[row])
        self.assertEqual(result['roles']['left_temple']['status'],'unmeasured')

    def test_source_fixed_extent_does_not_certify_only_a_tip_or_hinge(self):
        mesh = fixture(); binding = infer_automatic_part_bindings(mesh)['bindings'][0]
        rows = supported_views(mesh,binding)
        for row in rows:
            row['roles']['left_temple']['supported_bins'] = [0,1]
        result = aggregate_required_parts(binding,rows)
        self.assertEqual(result['roles']['left_temple']['status'],'unmeasured')

    def test_known_background_contradiction_is_failure(self):
        mesh = fixture(); binding = infer_automatic_part_bindings(mesh)['bindings'][0]
        camera = cameras()[0]
        support = object_support_arrays(np.full((260,320),2,np.uint8))
        row,_ = measure_view_parts(mesh,binding,mesh,binding,camera,ViewState('wrong'),support)
        self.assertTrue(row['roles']['front']['contradicted'])
        self.assertFalse(aggregate_required_parts(binding,[row])['required_parts_present'])

    def test_replayable_alpha_receipt_traverses_exact_lossless_final_asset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source_path = root/'source.glb'
            mesh = write_mesh(source_path,fixture())
            # Single-primitive GLB still supports connected-component inference.
            binding = infer_automatic_part_bindings(mesh)['bindings'][0]
            states, hashes, views, supports = {}, {}, [], {}
            for i,camera in enumerate(cameras()):
                name = str(i); states[name] = ViewState(name)
                mask = rasterize(mesh,camera,(260,320)).mask
                rgba = np.zeros((260,320,4),np.uint8); rgba[mask,:3]=30; rgba[mask,3]=255
                photo = root/(name+'.png'); Image.fromarray(rgba).save(photo)
                hashes[name] = pin(photo)['sha256']
                supports[name] = build_alpha_object_support(photo,root/('support'+name))
                views.append({'view_id':name,'camera_fit':{'camera':camera.to_dict()},'image_size_working':[320,260]})
            contract = make_view_scene_contract(mesh,binding,states,source_model=source_path,
                source_sha256=pin(source_path)['sha256'],photo_sha256=hashes,evidence={})
            refinement = root/'refinement.json'; refinement.write_text(json.dumps({'view_scene':contract,'views':views}))
            final = root/'final.glb'; final.write_bytes(compact_glb_bytes(source_path.read_bytes())[0])
            result = measure_required_parts(final,pin(refinement),supports,root/'measurement')
            self.assertTrue(result['required_parts_present'],result['roles'])
            replayed = replay_required_parts(final,pin(root/'measurement'/'report.json'),pin(refinement))
            self.assertTrue(replayed['required_parts_present'])
            # A forged scalar does not alter replayed geometry/pixels.
            receipt = root/'measurement'/'report.json'; forged=json.loads(receipt.read_bytes())
            forged['required_parts_present']=False; receipt.write_text(json.dumps(forged))
            self.assertTrue(replay_required_parts(final,pin(receipt),pin(refinement))['required_parts_present'])
            # Byte tampering and re-hashed but ungrounded arrays are both rejected.
            support_ref=supports['0']; report=json.loads(Path(support_ref['path']).read_bytes())
            array_path=Path(report['arrays']['path'])
            with np.load(array_path) as arrays: changed={k:arrays[k] for k in arrays.files}
            changed['membership'][:]=1; np.savez_compressed(array_path,**changed)
            report['arrays']=pin(array_path); Path(support_ref['path']).write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'do not replay'):
                read_object_support(pin(support_ref['path']))


if __name__ == '__main__':
    unittest.main()
