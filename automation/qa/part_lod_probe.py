"""Per-part meshoptimizer LOD plus CPU geometry/silhouette measurements.

The segmented provider asset remains untouched. New triangle topology cannot
reuse old face IDs or optical bindings. Frame UV/material/image bytes are kept.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw

from qa.provider_benchmark import digest, read_json, write_json
from reconstruction.deform_glb import _read_bytes
from reconstruction.mesh import load_glb_bytes


def scene(vertices, faces):
    result = o3d.t.geometry.RaycastingScene(nthreads=2)
    result.add_triangles(o3d.core.Tensor(np.asarray(vertices, dtype=np.float32)),
                         o3d.core.Tensor(np.asarray(faces, dtype=np.uint32)))
    return result


def sample_surface(vertices, faces, count=8000):
    triangles = vertices[faces]
    areas = np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),axis=1)
    rng = np.random.default_rng(230923)
    indices = rng.choice(len(faces),size=count,p=areas/areas.sum())
    uv = rng.random((count,2)); flip=uv.sum(axis=1)>1; uv[flip]=1-uv[flip]
    tri=triangles[indices]
    return tri[:,0]+uv[:,:1]*(tri[:,1]-tri[:,0])+uv[:,1:]*(tri[:,2]-tri[:,0])


def distance_summary(values, width):
    return dict(sample_count=len(values), mean=float(np.mean(values)),p95=float(np.quantile(values,.95)),
                maximum_sampled=float(np.max(values)),p95_at_145mm_width_mm=float(np.quantile(values,.95)/width*145),
                maximum_sampled_at_145mm_width_mm=float(np.max(values)/width*145),
                caveat="Deterministic sampled surface distance, not Hausdorff bound; 145mm is display convention")


def diagnose(source_raw, candidate_raw, output, optical):
    source,candidate=load_glb_bytes(source_raw),load_glb_bytes(candidate_raw)
    width=float(np.ptp(source.vertices,axis=0)[2])  # original Tripo lateral axis, before yaw=-90
    distances=[]
    for index,(before,after) in enumerate(zip(source.parts,candidate.parts)):
        old=source.faces[before['face_start']:before['face_start']+before['face_count']]
        new=candidate.faces[after['face_start']:after['face_start']+after['face_count']]
        old_scene,new_scene=scene(source.vertices,old),scene(candidate.vertices,new)
        a=sample_surface(source.vertices,old,count=12000 if index in optical else 3000)
        b=sample_surface(candidate.vertices,new,count=12000 if index in optical else 3000)
        d1=new_scene.compute_distance(o3d.core.Tensor(a.astype(np.float32))).numpy()
        d2=old_scene.compute_distance(o3d.core.Tensor(b.astype(np.float32))).numpy()
        distances.append(dict(part_index=index,optical=index in optical,source_to_lod=distance_summary(d1,width),lod_to_source=distance_summary(d2,width)))
        del old_scene,new_scene
    old_scene,new_scene=scene(source.vertices,source.faces),scene(candidate.vertices,candidate.faces)
    lo,hi=source.vertices.min(axis=0),source.vertices.max(axis=0)
    center=(lo+hi)/2; extent=float(np.max(hi-lo)); scale=extent*1.3
    views={'front':[1,0,0],'back':[-1,0,0],'left':[0,0,1],'right':[0,0,-1],'angled':[.8,.25,.55]}
    width_px,height_px=480,360
    sheet=Image.new('RGB',(width_px*4,(height_px+30)*len(views)),'white');draw=ImageDraw.Draw(sheet)
    silhouettes=[]
    for row,(name,direction) in enumerate(views.items()):
        forward=np.asarray(direction,dtype=float);forward/=np.linalg.norm(forward)
        right=np.cross([0,1,0],forward);right/=np.linalg.norm(right);up=np.cross(forward,right)
        xx,yy=np.meshgrid((np.arange(width_px)+.5-width_px/2)*scale/width_px,(height_px/2-np.arange(height_px)-.5)*scale/width_px)
        origins=center+forward*extent*3+xx[...,None]*right+yy[...,None]*up
        rays=np.concatenate((origins,np.broadcast_to(-forward,origins.shape)),axis=2).astype(np.float32)
        answers=[s.cast_rays(o3d.core.Tensor(rays)) for s in (old_scene,new_scene)]
        masks=[np.isfinite(a['t_hit'].numpy()) for a in answers]
        normals=[a['primitive_normals'].numpy() for a in answers]
        overlap=masks[0]&masks[1];union=masks[0]|masks[1]
        diff=np.full((height_px,width_px,3),240,dtype=np.uint8)
        diff[overlap]=[40,40,40];diff[masks[0]&~masks[1]]=[220,40,60];diff[masks[1]&~masks[0]]=[25,140,230]
        cosine=np.clip(np.sum(normals[0][overlap]*normals[1][overlap],axis=1),-1,1)
        angles=np.degrees(np.arccos(cosine))
        silhouettes.append(dict(view=name,iou=float(overlap.sum()/max(1,union.sum())),before_pixels=int(masks[0].sum()),
            after_pixels=int(masks[1].sum()),lost_pixels=int((masks[0]&~masks[1]).sum()),added_pixels=int((masks[1]&~masks[0]).sum()),
            visible_geometric_normal_angle_median_degrees=float(np.median(angles)),visible_geometric_normal_angle_p95_degrees=float(np.quantile(angles,.95)),
            normal_note='Face normals at matching rays, not authored smooth shading normals'))
        images=[]
        for mask in masks:
            a=np.full((height_px,width_px,3),240,dtype=np.uint8);a[mask]=35;images.append(a)
        images.append(diff)
        normal=np.full_like(diff,240);normal[masks[1]]=np.clip((normals[1][masks[1]]+1)*127.5,0,255).astype(np.uint8);images.append(normal)
        for col,(label,a) in enumerate(zip(('source silhouette','LOD silhouette','lost red / added blue','LOD geometric normals'),images)):
            sheet.paste(Image.fromarray(a),(col*width_px,row*(height_px+30)+30));draw.text((col*width_px+5,row*(height_px+30)+8),name+' | '+label,fill='black')
    sheet.save(output/'geometry-contact-sheet.png')
    result=dict(distances=distances,silhouettes=silhouettes,contact_sheet=str(output/'geometry-contact-sheet.png'),accepted=False)
    write_json(output/'diagnostics.json',result)
    return result


def run(source_path,output,optical,lens_target=20000,frame_target=100000,error_limit=.002):
    source_path,output=Path(source_path).resolve(),Path(output).resolve()
    if output.exists() and any(output.iterdir()):raise ValueError('Fresh output required')
    raw=source_path.read_bytes();mesh=load_glb_bytes(raw)
    if not optical or any(type(i) is not int or not 0<=i<len(mesh.parts) for i in optical):raise ValueError('Explicit valid optical indices required')
    optical=set(optical);old_lens=sum(p['face_count'] for i,p in enumerate(mesh.parts) if i in optical)
    old_frame=len(mesh.faces)-old_lens
    specifications=[]
    for index,part in enumerate(mesh.parts):
        budget=lens_target if index in optical else frame_target;total=old_lens if index in optical else old_frame
        specifications.append(dict(part_index=index,mesh_index=part['mesh_index'],primitive_index=part['primitive_index'],
            optical=index in optical,target_triangles=max(4,int(part['face_count']*min(1,budget/max(1,total))))))
    output.mkdir(parents=True)
    config=dict(source=str(source_path),source_sha256=digest(raw),optical_parts=sorted(optical),lens_target=lens_target,
                frame_target=frame_target,error_limit=error_limit,primitives=specifications)
    write_json(output/'request.json',config,exclusive=True)
    script=Path(__file__).with_suffix('.mjs')
    result=subprocess.run(['node',str(script),str(source_path),str(output/'candidate.glb'),str(output/'geometry.json'),str(output/'request.json')],capture_output=True,text=True,timeout=240)
    (output/'simplifier.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode:raise ValueError('Simplifier failed; see log')
    candidate_raw=(output/'candidate.glb').read_bytes();candidate=load_glb_bytes(candidate_raw)
    _,old_doc,old_bin=_read_bytes(raw);_,new_doc,new_bin=_read_bytes(candidate_raw)
    if not np.array_equal(mesh.vertices,candidate.vertices) or len(mesh.parts)!=len(candidate.parts):raise ValueError('Vertex values/order or primitive count changed')
    if new_bin[:len(old_bin)]!=old_bin:raise ValueError('Original attribute/image bytes changed')
    for key in ('materials','textures','images','samplers','nodes','scenes'):
        if old_doc.get(key)!=new_doc.get(key):raise ValueError('Scene/material state changed')
    for before,after in zip(mesh.parts,candidate.parts):
        p=old_doc['meshes'][before['mesh_index']]['primitives'][before['primitive_index']]
        q=new_doc['meshes'][after['mesh_index']]['primitives'][after['primitive_index']]
        if p['attributes']!=q['attributes'] or p.get('material')!=q.get('material'):raise ValueError('Appearance binding changed')
    proof=dict(source_sha256=digest(raw),candidate_sha256=digest(candidate_raw),vertices_exact=True,source_binary_prefix_exact=True,
        material_uv_bindings_exact=True,new_triangle_topology=True,old_optical_receipts_invalid=True,source_triangles=len(mesh.faces),
        candidate_triangles=len(candidate.faces),optical_triangles=sum(p['face_count'] for i,p in enumerate(candidate.parts) if i in optical),accepted=False)
    write_json(output/'verification.json',proof)
    print(json.dumps(dict(state='candidate_ready',path=str(output/'candidate.glb'),**proof)),flush=True)
    diagnose(raw,candidate_raw,output,optical)
    return proof


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--optical-parts',required=True,help='JSON list');p.add_argument('--lens-target',type=int,default=20000);p.add_argument('--frame-target',type=int,default=100000);p.add_argument('--error-limit',type=float,default=.002)
    a=p.parse_args();print(json.dumps(run(a.source,a.output,json.loads(a.optical_parts),a.lens_target,a.frame_target,a.error_limit)))
