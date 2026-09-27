"""Numerical investigation of opaque source faces inside saved lens apertures."""
import json
from pathlib import Path
import numpy as np
from scipy.ndimage import binary_erosion
from reconstruction.component_scene import load_component_scene
from reconstruction.camera import Camera, project
from reconstruction.mesh import TriangleMesh
from reconstruction.raster import rasterize


def main():
    root = Path('data/jobs/oakley-fresh-v9/stages/physical_groups/attempt_1')
    report = json.loads((root / 'report.json').read_bytes())
    scene = load_component_scene(root / 'inventory/report.json', recompute_labels=False)
    refinement = json.loads(Path('data/jobs/oakley-fresh-v9/stages/refinement/attempt_1/report.json').read_bytes())
    normalization = refinement['normalization']
    mesh = scene['mesh']
    normal = TriangleMesh((mesh.vertices-normalization['center'])/normalization['extent'], mesh.faces, [])
    selected = next(h for h in report['hypotheses'] if h['index'] == report['selected_hypothesis']['index'])
    members = [m for g in selected['consensus_optical_groups'] for m in g['members']]
    optical = np.isin(scene['face_components'], members)
    vertices = mesh.vertices[np.unique(mesh.faces[optical])]
    center = np.mean(vertices,axis=0); scale = np.ptp(vertices,axis=0).max()
    def design(p):
        x,y = ((p-center)/scale)[:,:2].T
        return np.column_stack([np.ones(len(p)),x,y,x*x,x*y,y*y])
    matrix=design(vertices); target=(vertices[:,2]-center[2])/scale
    coef=np.linalg.lstsq(matrix,target,rcond=None)[0]
    for _ in range(5):
        residual=target-matrix@coef
        w=np.minimum(1,.006/np.maximum(np.abs(residual),1e-10))
        coef=np.linalg.lstsq(matrix*w[:,None],target*w,rcond=None)[0]
    centroids=mesh.vertices[mesh.faces].mean(axis=1)
    residual=np.abs((centroids[:,2]-center[2])/scale-design(centroids)@coef)
    print('members',members,'bend residual optical',np.quantile(residual[optical],[.5,.9,.95,.99]).tolist(),flush=True)
    from reconstruction.optical_membership import audit_optical_membership
    views=[]
    for v in report['views']:
        if v['status']!='projected': continue
        a=np.load(root/v['aperture_arrays']['path'])
        views.append({'id':v['id'],'shape':a['full_mask'].shape,'camera':v['camera'],
                      'interpretations':[{'mask':a[k+'_mask'],'known_domain':a[k+'_known_domain']} for k in ('full','contrast_crop')]})
    audit=audit_optical_membership(normal,scene['face_components'],selected['consensus_optical_groups'],views)
    print(json.dumps(next(c for c in audit['components'] if c['component_id']==3),indent=2))
    result=[]
    for v in report['views']:
        if v['status']!='projected': continue
        a=np.load(root/v['aperture_arrays']['path']); mask=a['full_mask']&a['contrast_crop_mask'];core=binary_erosion(mask,iterations=2)
        cam=Camera(**v['camera']); shape=mask.shape
        raster=rasterize(normal,cam,shape)
        ids=raster.face_index; hit=ids>=0; opaque=hit.copy();opaque[hit]=~optical[ids[hit]]
        xy=project((centroids-normalization['center'])/normalization['extent'],cam).round().astype(int)
        valid=(xy[:,0]>=0)&(xy[:,0]<shape[1])&(xy[:,1]>=0)&(xy[:,1]<shape[0])
        support=np.zeros(len(mesh.faces),bool);support[valid]=mask[xy[valid,1],xy[valid,0]]
        table=[]
        for row in scene['component_table']:
            cid=row['component_id']; faces=scene['face_components']==cid
            visible=hit.copy();visible[hit]=faces[ids[hit]]
            table.append(dict(component=cid,faces=int(faces.sum()),visible=int(visible.sum()),core=int((visible&core).sum()),visible_inside=[float((visible&a[k]).sum()/max(1,visible.sum())) for k in ('full_mask','contrast_crop_mask')],inside=float(support[faces].mean()),bend=np.quantile(residual[faces],[.1,.5,.9,.95,.99]).tolist(),near_fraction=float((residual[faces]<.025).mean())))
        result.append(dict(view=v['id'],core=int(core.sum()),opaque_core=int((opaque&core).sum()),table=table))
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
