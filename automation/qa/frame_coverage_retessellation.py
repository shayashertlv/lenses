"""Same plane and observed sample locations under different triangulations."""
import json
from pathlib import Path

import numpy as np

from reconstruction.frame_surface_coverage import assess_frame_surface_coverage
from reconstruction.mesh import TriangleMesh


def case(divisions, sample_width=65):
    axis=np.linspace(0,1,divisions+1)
    x,y=np.meshgrid(axis,axis)
    vertices=np.column_stack((x.ravel(),y.ravel(),np.zeros(x.size)))
    a=np.arange((divisions+1)**2).reshape(divisions+1,divisions+1)[:-1,:-1].ravel()
    faces=np.stack((np.column_stack((a,a+1,a+divisions+2)),
                    np.column_stack((a,a+divisions+2,a+divisions+1))),axis=1).reshape(-1,3)
    mesh=TriangleMesh(vertices,faces,[{'face_start':0,'face_count':len(faces)}])
    # Identical world sample coordinates and independent angular observations.
    s=(np.arange(sample_width)+.5)/sample_width
    sx,sy=np.meshgrid(s,s);xy=np.column_stack((sx.ravel(),sy.ravel()))
    cell=np.floor(xy*divisions).astype(int);local=xy*divisions-cell
    upper=local[:,1]>local[:,0]
    ids=2*(cell[:,1]*divisions+cell[:,0])+upper
    bary=np.column_stack((1-local[:,0],local[:,0]-local[:,1],local[:,1]))
    bary[upper]=np.column_stack((1-local[upper,1],local[upper,0],local[upper,1]-local[upper,0]))
    actual=np.einsum('ni,nij->nj',bary,vertices[faces[ids]])
    np.testing.assert_allclose(actual[:,:2],xy,atol=1e-12)
    angles=np.radians([0,15,35]);directions=np.column_stack((np.sin(angles),np.zeros(3),np.cos(angles)))
    evidence={'source_faces':ids,'barycentric':bary,'valid':np.ones((3,len(ids)),bool),
              'directions':np.broadcast_to(directions[:,None,:],(3,len(ids),3)).copy()}
    report=assess_frame_surface_coverage(mesh,[evidence])
    return {'triangles':len(faces),'tracks':len(ids),'physical_area':report['frame_area'],
            'observed_fraction':report['observed_fraction'],
            'angular_supported_tracks':report['angular_supported_tracks'],
            'covered_subtriangles':report['covered_subtriangles']}


if __name__=='__main__':
    report={'method':'same_world_tracks_retessellation_control_v1',
            'cases':[case(n) for n in (1,2,4,8,16,32,64)],
            'scope':'A measurement representation check; no product threshold is changed.'}
    path=Path('data/build-five/frame-coverage-retessellation.json')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
