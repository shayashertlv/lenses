"""Frozen-photo camera alternatives: separate front holdout and arm support."""
import argparse
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.optimize import least_squares

from reconstruction.automatic_articulation import infer_automatic_part_bindings,fit_photo_arm_states
from reconstruction.camera import Camera,fit_camera,project,render_mask
from reconstruction.mesh import load_glb,TriangleMesh
from reconstruction.observations import observe_image
from reconstruction.raster import rasterize
from reconstruction.refine_photos import geometry_rgb,implementation_manifest,compare_image
from reconstruction.view_scene import PartBinding,Hinge,mesh_geometry_sha256,pose_scene


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refinement',type=Path,required=True)
    parser.add_argument('--view',default='back')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();source=implementation_manifest()
    report=json.loads(args.refinement.read_bytes());view=next(v for v in report['views'] if v['view_id']==args.view)
    world=load_glb(report['source_model']);norm=report['normalization'];center=np.asarray(norm['center']);extent=norm['extent']
    mesh=TriangleMesh((world.vertices-center)/extent,world.faces,world.parts)
    binding=infer_automatic_part_bindings(world)['bindings'][0]
    binding=PartBinding(mesh_geometry_sha256(mesh),binding.face_roles,
        tuple(Hinge(h.side,(h.origin-center)/extent,h.axis,h.minimum_degrees,h.maximum_degrees) for h in binding.hinges),binding.provenance)
    front_ids=np.flatnonzero(np.asarray(binding.face_roles)=='front')
    front=TriangleMesh(mesh.vertices,mesh.faces[front_ids],[])
    image=Image.open(view['source']);size=tuple(view['image_size_working'])
    rgb=np.asarray(geometry_rgb(image).resize(size,Image.Resampling.LANCZOS))
    mask=np.asarray(Image.fromarray(observe_image(image).mask).resize(size,Image.Resampling.NEAREST),bool)
    print('Reproducing whole-scene seed',flush=True)
    coarse=fit_camera(mesh,mask,view=view['view_label'],max_evaluations=report['settings']['camera_evaluations'])
    camera=Camera(**coarse['camera']);production=Camera(**view['camera_fit']['camera'])
    frozen=json.loads((args.refinement.parent/args.view/'front-edge-hypotheses.json').read_bytes())
    selected=[p for p in frozen['points'] if p['status']=='selected']
    xy=np.asarray([p['initial_xy'] for p in selected],int);targets=np.asarray([p['matched_xy'] for p in selected])
    normals=np.asarray([p['normal_xy'] for p in selected]);initial=rasterize(front,camera,rgb.shape[:2])
    face=initial.face_index[xy[:,1],xy[:,0]];valid=face>=0
    if not valid.all():raise ValueError('Reproduced seed differs from frozen front surface anchors')
    bary=initial.barycentric[xy[:,1],xy[:,0]]
    points=np.einsum('ni,nij->nj',bary,front.vertices[front.faces[face]])
    holdout=((targets[:,0].astype(int)//5)+(targets[:,1].astype(int)//5))%3==0
    bank=[('whole-scene',camera),('front-only-production',production),('initial-geometric',Camera(**coarse['initial_camera']))]
    for offset in (-20.,-10.,0.,10.,20.):
        seed=replace(camera,pitch=float(np.clip(camera.pitch+offset,-20.,55.)))
        def unpack(v):return replace(seed,scale=float(np.exp(v[0])),center_x=float(v[1]),center_y=float(v[2]))
        def objective(v):return np.sum((project(points[~holdout],unpack(v))-targets[~holdout])*normals[~holdout],axis=1)
        fit=least_squares(objective,[np.log(seed.scale),seed.center_x,seed.center_y],loss='soft_l1',max_nfev=60)
        bank.append((f'pitch-offset-{offset:+.0f}',unpack(fit.x)))
    args.output.mkdir(parents=True,exist_ok=False);rows=[]
    baseline=rasterize(mesh,production,rgb.shape[:2])
    for name,c in bank:
        print(name,flush=True)
        mask=render_mask(front,c,rgb.shape[:2]);boundary=mask&~ndimage.binary_erosion(mask)
        distance=ndimage.distance_transform_edt(~boundary)
        error=ndimage.map_coordinates(distance,targets.T[::-1],order=1,mode='constant',cval=100.)
        residual=np.abs(np.sum((project(points,c)-targets)*normals,axis=1))
        pose=fit_photo_arm_states(mesh,binding,rgb,c,args.view)
        arms={}
        for side,arm in pose['report'].items():
            retained=arm.get('retained',False);m=arm.get('proposal' if retained else 'baseline',{})
            quality=arm.get('absolute_correspondence_quality' if retained else 'baseline_correspondence_quality',{})
            arms[side]={'status':arm['status'],'angle':getattr(pose['state'],side+'_degrees'),
                'error_px':m.get('holdout_error_px'),'near_fraction':m.get('holdout_near_edge_fraction'),
                'absolute_supported':quality.get('supported',False)}
        row={'id':name,'camera':c.to_dict(),'front_holdout_count':int(holdout.sum()),
             'front_holdout_boundary_mean_px':float(error[holdout].mean()),
             'front_holdout_boundary_p95_px':float(np.quantile(error[holdout],.95)),
             'front_holdout_anchor_mean_px':float(residual[holdout].mean()),'arms':arms}
        rows.append(row)
        posed=pose_scene(mesh,binding,pose['state'],compact=True).mesh
        compare_image(rgb,baseline,rasterize(posed,c,rgb.shape[:2]),targets[holdout],args.output/(name+'.png'))
    result={'source_model':report['source_model'],'view':args.view,'coarse':coarse,'candidates':rows,
            'scope':'Fixed front image-edge anchors with spatial holdout; pitch-bank scale/translation fit train cells only. Production camera had seen all anchors; listed as non-independent control.',
            'implementation_stable':source==implementation_manifest(),'accepted':False}
    (args.output/'report.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(rows),flush=True)


if __name__=='__main__':main()
