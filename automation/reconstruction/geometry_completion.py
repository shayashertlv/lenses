"""Construct bounded missing surfaces from open loops and photographic support.

Candidate construction is separate from photographic eligibility. This stage
never closes the outside of a lens or joins components merely by proximity.
It emits new geometry only when two diverse views observe the missing area.
"""
from dataclasses import asdict, dataclass
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation

from .camera import Camera
from .deform_glb import _read_bytes
from .lens_asset import _pack_glb
from .mesh import TriangleMesh, load_glb_bytes
from .mesh_components import component_face_labels
from .observations import observe_image
from .raster import rasterize


@dataclass(frozen=True)
class CompletionPolicy:
    maximum_gap_width_fraction: float = .10
    maximum_loop_radius_fraction: float = .025
    maximum_lens_hole_area_fraction: float = .04
    minimum_support_fraction: float = .98
    minimum_new_pixels: int = 3
    minimum_view_separation_degrees: float = 8.
    maximum_proposals: int = 24


def boundary_loops(mesh):
    """Exact-position weld for topology inspection; source arrays stay intact."""
    points, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
    faces = inverse[mesh.faces]
    edges = np.concatenate((faces[:,[0,1]], faces[:,[1,2]], faces[:,[2,0]]))
    edges = np.sort(edges, axis=1)
    unique, counts = np.unique(edges, axis=0, return_counts=True)
    edges = unique[counts == 1]
    adjacency = {}
    for a,b in edges:
        adjacency.setdefault(int(a), []).append(int(b)); adjacency.setdefault(int(b), []).append(int(a))
    visited, result = set(), []
    for first in sorted(adjacency):
        if first in visited or len(adjacency[first]) != 2:
            continue
        ring, previous, current = [], None, first
        while current not in ring and len(ring) <= 4096:
            if len(adjacency[current]) != 2:
                break
            ring.append(current); visited.add(current)
            nxt = next((n for n in adjacency[current] if n != previous), first)
            previous, current = current, nxt
        if current == first and 3 <= len(ring) <= 4096:
            result.append(points[ring])
    return result


def _normal(points):
    _,_,vh = np.linalg.svd(points-points.mean(axis=0), full_matrices=False)
    return vh[-1]


def _fan(loop):
    center = loop.mean(axis=0)
    vertices = np.concatenate((loop, center[None]))
    faces = np.array([[i,(i+1)%len(loop),len(loop)] for i in range(len(loop))], int)
    return TriangleMesh(vertices, faces, [])


def _resample(loop, n):
    closed = np.concatenate((loop,loop[:1]))
    length = np.r_[0.,np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))]
    if length[-1] <= 1e-12:
        raise ValueError('Degenerate boundary loop')
    t = np.arange(n)*length[-1]/n
    return np.column_stack([np.interp(t,length,closed[:,axis]) for axis in range(3)])


def bridge_boundary_curves(first, second, *, steps=8):
    """A ruled tube with fixed measured end loops and smooth intermediate rings."""
    first,second = np.asarray(first,float),np.asarray(second,float)
    if type(steps) is not int or not 2 <= steps <= 64:
        raise ValueError('Bridge steps must be an integer from 2 through 64')
    for loop in (first,second):
        if (loop.ndim != 2 or loop.shape[1] != 3 or not 3 <= len(loop) <= 4096
                or not np.isfinite(loop).all()
                or np.any(np.linalg.norm(np.roll(loop,-1,axis=0)-loop,axis=1) <= 1e-12)):
            raise ValueError('Bridge endpoints require finite nondegenerate closed boundary loops')
    n = min(128,max(12,len(first),len(second)))
    a,b = _resample(first,n),_resample(second,n)
    # Choose orientation/phase using bounded samples, then start the actual
    # measured second loop at its nearest existing vertex. Endpoint vertices
    # and edges are retained exactly; only intermediate rings are resampled.
    _,reverse,phase = min((float(np.sum((a-np.roll(x,k,axis=0))**2)),reverse,k)
                         for reverse,x in ((False,b),(True,b[::-1])) for k in range(n))
    sampled = np.roll(b[::-1] if reverse else b,phase,axis=0)
    endpoint = second[::-1] if reverse else second
    endpoint = np.roll(endpoint,-int(np.argmin(np.linalg.norm(endpoint-sampled[0],axis=1))),axis=0)
    b = _resample(endpoint,n)
    rings = [first]+[(1-t)*a+t*b for t in np.linspace(0,1,steps)[1:-1]]+[endpoint]
    vertices = np.concatenate(rings);offsets=np.r_[0,np.cumsum([len(r) for r in rings])]
    faces = []
    for ordinal,(left,right) in enumerate(zip(rings,rings[1:])):
        # Merge the two normalized perimeter parameter sequences. A zipper
        # joins unequal vertex counts without cutting measured polygon corners
        # or introducing T-junctions at either source boundary.
        lengths = [np.linalg.norm(np.roll(r,-1,axis=0)-r,axis=1) for r in (left,right)]
        ta,tb = [np.r_[0.,np.cumsum(length)]/length.sum() for length in lengths]
        i=j=0;na,nb=len(left),len(right);pa,pb=offsets[ordinal:ordinal+2]
        while i < na or j < nb:
            ai,bj=int(pa+i%na),int(pb+j%nb)
            if i < na and (j == nb or ta[i+1] <= tb[j+1]):
                faces.append([ai,int(pa+(i+1)%na),bj]);i+=1
            else:
                faces.append([ai,int(pb+(j+1)%nb),bj]);j+=1
    return TriangleMesh(vertices,np.asarray(faces,int),[])


def propose_missing_geometry(mesh, views, *, policy=CompletionPolicy()):
    span = float(np.ptp(mesh.vertices,axis=0).max())
    if not np.isfinite(span) or span <= 0:
        raise ValueError('Geometry must have positive finite extent')
    loops = boundary_loops(mesh)
    # Keep large outer loops as context; they are never fan-filled. A lens hole
    # must be enclosed in another coplanar loop and small relative to the frame.
    proposals = []
    for i,loop in enumerate(loops):
        normal = _normal(loop); center=loop.mean(axis=0)
        area = float(np.linalg.norm(np.cross(loop-center,np.roll(loop,-1,axis=0)-center).sum(axis=0))/2)
        if abs(normal[2]) < .8 or area > policy.maximum_lens_hole_area_fraction*span**2:
            continue
        enclosing = [other for j,other in enumerate(loops) if j != i and
                     np.all(center[:2] > other[:,:2].min(axis=0)) and np.all(center[:2] < other[:,:2].max(axis=0)) and
                     np.ptp(other,axis=0)[:2].prod() > np.ptp(loop,axis=0)[:2].prod()*1.5 and
                     abs((other.mean(axis=0)-center) @ normal) < span*.015]
        if enclosing:
            proposals.append({'kind':'lens_hole_patch','mesh':_fan(loop),'source_loops':[i]})
    small = [(i,l) for i,l in enumerate(loops) if np.linalg.norm(l-l.mean(axis=0),axis=1).max() <= policy.maximum_loop_radius_fraction*span]
    for at,(i,a) in enumerate(small):
        for j,b in small[at+1:]:
            direction=b.mean(axis=0)-a.mean(axis=0); distance=np.linalg.norm(direction)
            if distance <= span*.001 or distance > span*policy.maximum_gap_width_fraction:
                continue
            if min(abs(_normal(a) @ (direction/distance)),abs(_normal(b) @ (direction/distance))) < .80:
                continue
            ra=np.linalg.norm(a-a.mean(axis=0),axis=1).mean(); rb=np.linalg.norm(b-b.mean(axis=0),axis=1).mean()
            if min(ra,rb)/max(ra,rb) < .6:
                continue
            proposals.append({'kind':'frame_curve_bridge','mesh':bridge_boundary_curves(a,b),'source_loops':[i,j]})
    captured=[]
    for view in views:
        camera=view['camera'] if isinstance(view['camera'],Camera) else Camera(**view['camera'])
        raster=rasterize(view.get('mesh',mesh),camera,tuple(view['shape']))
        yaw,pitch=np.radians([camera.yaw,camera.pitch])
        forward=np.array([np.cos(pitch)*np.sin(yaw),np.sin(pitch),np.cos(pitch)*np.cos(yaw)])
        captured.append((view,camera,raster,forward))
    accepted, records, consumed = [], [], set()
    for proposal in proposals[:policy.maximum_proposals]:
        if proposal['kind']=='frame_curve_bridge' and any(v.get('moving_parts') for v in views):
            records.append({'kind':proposal['kind'],'source_loops':proposal['source_loops'],'eligible':False,
                            'reason':'Moving-part gap requires unambiguous endpoint hinge binding; rest-pose bridge is not valid evidence.'})
            continue
        support, measurements, contrary = [], [], False
        for view,camera,original,forward in captured:
            patch=rasterize(proposal['mesh'],camera,tuple(view['shape']))
            visible=patch.mask & (~original.mask | (patch.depth < original.depth+span*.001))
            target=np.asarray(view['lens_mask'] if proposal['kind']=='lens_hole_patch' else view['frame_mask'],bool)
            known=np.asarray(view.get('known_domain',np.ones_like(target)),bool)
            added=visible & ~binary_dilation(original.mask,iterations=1) & known
            footprint=visible & known
            count=int(footprint.sum()); new=int(added.sum())
            fraction=float(np.count_nonzero(footprint&target)/count) if count else None
            if count >= policy.minimum_new_pixels and fraction < policy.minimum_support_fraction:
                contrary=True
            if new >= policy.minimum_new_pixels and fraction is not None and fraction >= policy.minimum_support_fraction:
                support.append(forward)
            measurements.append({'view_id':view['id'],'source_sha256':view.get('source_sha256'),
                                 'visible_pixels':count,'new_pixels':new,'support_fraction':fraction})
        separation=max([float(np.degrees(np.arccos(np.clip(a@b,-1,1)))) for a in support for b in support] or [0.])
        passed=len(support)>=2 and not contrary and separation>=policy.minimum_view_separation_degrees and not consumed.intersection(proposal['source_loops'])
        record={'kind':proposal['kind'],'source_loops':proposal['source_loops'],'views':measurements,
                'supporting_views':len(support),'camera_separation_degrees':separation,'eligible':passed}
        records.append(record)
        if passed:
            accepted.append(proposal); consumed.update(proposal['source_loops'])
    return {'proposals':accepted,'report':{'schema_version':1,'method':'photo_supported_boundary_construction_v1',
        'policy':asdict(policy),'boundary_loops':len(loops),'candidates':records,'accepted':False,
        'status':'supported_geometry_constructed' if accepted else 'no_supported_completion',
        'limitations':['Closed source components with wholly absent parts need separate part reconstruction.',
                       'Only bounded internal lens holes and observed curve gaps can be constructed.',
                       'Photograph support does not measure unseen cross sections.']}}


def append_constructed_geometry(raw, proposals, *, center, extent):
    _,doc,binary=_read_bytes(raw); doc=deepcopy(doc); blob=bytearray(binary)
    source=load_glb_bytes(raw)
    def accessor(array,kind,component):
        while len(blob)%4:blob.append(0)
        offset=len(blob); blob.extend(array.tobytes())
        vi=len(doc.setdefault('bufferViews',[])); doc['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':array.nbytes})
        ai=len(doc.setdefault('accessors',[])); row={'bufferView':vi,'componentType':component,'count':len(array),'type':kind}
        if kind=='VEC3':row.update(min=array.min(axis=0).tolist(),max=array.max(axis=0).tolist())
        doc['accessors'].append(row); return ai
    for proposal in proposals:
        mesh=proposal['mesh']; world=mesh.vertices*extent+center
        center_point=world.mean(axis=0)
        nearest=min(source.parts,key=lambda p:float(np.linalg.norm(source.vertices[p['vertex_start']:p['vertex_start']+p['vertex_count']].mean(axis=0)-center_point)))
        material=deepcopy(doc.get('materials',[])[nearest['material_index']]) if nearest.get('material_index') is not None else {'pbrMetallicRoughness':{}}
        # New UVs cannot sample an unrelated source atlas. Retain factor/PBR,
        # identify this surface explicitly for the later photo appearance stage.
        for name in ('normalTexture','occlusionTexture','emissiveTexture'):material.pop(name,None)
        for name in ('baseColorTexture','metallicRoughnessTexture'):material.setdefault('pbrMetallicRoughness',{}).pop(name,None)
        material['name']='constructed-'+proposal['kind']
        mi=len(doc.setdefault('materials',[]));doc['materials'].append(material)
        cross=np.cross(world[mesh.faces[:,1]]-world[mesh.faces[:,0]],world[mesh.faces[:,2]]-world[mesh.faces[:,0]])
        normals=np.zeros_like(world)
        for k in range(3):np.add.at(normals,mesh.faces[:,k],cross)
        normals/=np.maximum(np.linalg.norm(normals,axis=1)[:,None],1e-20)
        primitive={'attributes':{'POSITION':accessor(world.astype('<f4'),'VEC3',5126),
                                 'NORMAL':accessor(normals.astype('<f4'),'VEC3',5126)},
                   'indices':accessor(mesh.faces.astype('<u4').ravel(),'SCALAR',5125),'material':mi,
                   'extras':{'constructionMethod':'photo_supported_boundary_construction_v1','partRole':'optical' if proposal['kind']=='lens_hole_patch' else 'frame'}}
        index=len(doc['meshes']);doc['meshes'].append({'primitives':[primitive]})
        node=len(doc['nodes']);doc['nodes'].append({'mesh':index,'name':material['name']})
        doc['scenes'][doc.get('scene',0)].setdefault('nodes',[]).append(node)
    doc['buffers'][0]['byteLength']=len(blob)
    return _pack_glb(doc,bytes(blob))


def run_geometry_completion(model, refinement, photos, output, *, aperture_engine, policy=CompletionPolicy()):
    output=Path(output);model=Path(model)
    if output.exists() and any(output.iterdir()):raise ValueError('Completion output must be fresh')
    raw=model.read_bytes();source=load_glb_bytes(raw);normalization=refinement['normalization']
    digest=hashlib.sha256(raw).hexdigest()
    expected=refinement.get('export',{}).get('output_sha256') if refinement.get('status')=='proposal_exported' and refinement.get('rerender_nonregression') is True else refinement.get('source_sha256')
    if digest!=expected:raise ValueError('Completion model does not match refined camera geometry')
    center=np.asarray(normalization['center']);extent=float(normalization['extent'])
    mesh=TriangleMesh((source.vertices-center)/extent,source.faces,source.parts)
    views=[]
    for photo in photos:
        record=next((v for v in refinement.get('views',[]) if v['view_id']==photo['id'] and 'camera_fit' in v),None)
        if record is None:continue
        image_raw=Path(photo['path']).read_bytes();digest=hashlib.sha256(image_raw).hexdigest()
        if digest != photo['sha256'] or record['source_sha256'] != digest:raise ValueError('Photo/camera source mismatch')
        image=Image.open(io.BytesIO(image_raw))
        if 'A' in image.getbands() and image.getchannel('A').getextrema()[0]<255:
            continue  # Alpha supports silhouette, not a known lens/background color.
        rgb=np.asarray(image.convert('RGB'));obs=observe_image(image)
        if obs.status!='measured':continue
        proposals=aperture_engine.propose(rgb)['variants'];masks=[np.asarray(p['mask'],bool) for p in proposals.values()]
        known=np.logical_and.reduce([np.asarray(p['known_domain'],bool) for p in proposals.values()])
        lens=np.logical_and.reduce(masks);frame=obs.mask & ~np.logical_or.reduce(masks)
        w,h=record['image_size_working'];nh,nw=lens.shape
        rows=np.minimum(nh-1,((np.arange(h)+.5)*nh/h).astype(int));cols=np.minimum(nw-1,((np.arange(w)+.5)*nw/w).astype(int))
        posed=mesh;moving=False
        if refinement.get('view_scene'):
            from .view_scene import pose_mesh_from_contract
            posed=pose_mesh_from_contract(mesh,refinement['view_scene'],photo['id'],photo_sha256=digest,normalization=normalization)['mesh']
            state=refinement['view_scene']['views'][photo['id']]
            moving=bool(abs(state['left_degrees'])>1e-6 or abs(state['right_degrees'])>1e-6)
        views.append({'id':photo['id'],'source_sha256':digest,'shape':(h,w),'camera':record['camera_fit']['camera'],
                      'mesh':posed,'moving_parts':moving,
                      'lens_mask':lens[np.ix_(rows,cols)],'frame_mask':frame[np.ix_(rows,cols)],'known_domain':known[np.ix_(rows,cols)]})
    result=propose_missing_geometry(mesh,views,policy=policy);report=result['report']
    output.mkdir(parents=True,exist_ok=True)
    report.update(source_sha256=hashlib.sha256(raw).hexdigest(),source=str(model.resolve()),normalization=normalization)
    if result['proposals']:
        generated=append_constructed_geometry(raw,result['proposals'],center=center,extent=extent)
        decoded=load_glb_bytes(generated)
        if not np.array_equal(decoded.faces[:len(source.faces)],source.faces) or not np.array_equal(decoded.vertices[:len(source.vertices)],source.vertices):
            raise ValueError('Completion changed original geometry')
        (output/'candidate.glb').write_bytes(generated)
        report['candidate']={'path':'candidate.glb','sha256':hashlib.sha256(generated).hexdigest(),
                             'added_faces':len(decoded.faces)-len(source.faces)}
    (output/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report
