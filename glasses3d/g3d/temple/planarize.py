"""Joint planar fit for a declared group of mesh faces, without a mesh write.

This is an explicit geometry edit. Shared vertices constrain adjoining planes,
and moving the border must be an explicit choice. It is never invoked by an
export, inspection or a metadata repair. The caller owns selection, preview,
confirmation, solid/thickness checks and surface-relative marking transfer.
"""
from __future__ import annotations

import numpy as np


def fit_group(vertices, triangles, groups, *, axis=0, keep_boundary=True):
    """Return a new vertex array and a quantified joint plane-fit report.

    ``groups`` maps displayed face labels to triangle IDs. For an integer axis,
    coordinates transverse to ``axis`` remain exact. With ``axis=None``, each
    face's fitted normal stays fixed and shared vertices move in three dimensions
    to the intersection of those planes. The least-squares objective weights each source
    triangle by its area, so tessellation density cannot bias the plane. Shared
    vertices are exact equality constraints. A nonplanar fixed border is refused;
    flattening cannot both retain such a border and produce one plane.
    """
    v, f = np.asarray(vertices, float), np.asarray(triangles, int)
    if axis is None:
        return _fit_free_group(v, f, groups, keep_boundary=keep_boundary)
    if v.ndim != 2 or v.shape[1] != 3 or not np.isfinite(v).all() or axis not in (0, 1, 2):
        raise ValueError('finite 3D vertices and one coordinate axis are required')
    if f.ndim != 2 or f.shape[1] != 3 or not groups:
        raise ValueError('a nonempty labelled triangle group is required')
    labels = sorted(groups)
    dims = [i for i in range(3) if i != axis]
    chart_centre = v[:, dims].mean(0)
    chart_scale = np.maximum(np.ptp(v[:, dims], axis=0), 1.)
    chart = np.c_[np.ones(len(v)), (v[:, dims]-chart_centre)/chart_scale]
    rows, targets, constraints, fixed_values = [], [], [], []
    membership, boundaries, used_triangles = {}, {}, set()
    for gi, label in enumerate(labels):
        ids = np.asarray(groups[label], int)
        if ids.ndim != 1 or len(ids) == 0 or np.any(ids < 0) or np.any(ids >= len(f)):
            raise ValueError(f'face {label} has no valid triangle selection')
        if used_triangles.intersection(map(int, ids)):
            raise ValueError('a triangle cannot belong to two face labels')
        used_triangles.update(map(int, ids))
        selected = f[ids]
        points = v[selected]
        area = np.linalg.norm(np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0]), axis=1)/2
        if area.sum() <= 1e-10:
            raise ValueError(f'face {label} has no area')
        edge_counts = {}
        for tri, a in zip(selected, area):
            for vi in tri:
                row = np.zeros(3*len(labels)); row[gi*3:gi*3+3] = chart[vi]*np.sqrt(a/3)
                rows.append(row); targets.append(v[vi, axis]*np.sqrt(a/3))
                membership.setdefault(int(vi), set()).add(gi)
            for a, b in zip(tri, np.roll(tri, -1)):
                key = tuple(sorted((int(a), int(b)))); edge_counts[key] = edge_counts.get(key, 0)+1
        boundaries[label] = sorted({vi for edge, count in edge_counts.items() if count == 1 for vi in edge})
    pairs = {}
    for vi, faces in membership.items():
        ordered = sorted(faces)
        for a in range(len(ordered)):
            for b in range(a+1, len(ordered)):
                pairs.setdefault((ordered[a], ordered[b]), []).append(vi)
    for (ga, gb), ids in pairs.items():
        # A float32 mesh does not retain a long straight edge as an exactly
        # collinear point set. Constraining every rounded point independently
        # would force its two adjoining planes to be the same plane. Constrain
        # the two extremes when the chart deviations are below 0.0001 mm; the
        # shared vertices still receive one identical output position below.
        xy = v[ids][:, dims]
        if len(ids) > 2:
            centre=xy.mean(0); _, _, vh=np.linalg.svd(xy-centre, full_matrices=False)
            if np.max(np.abs((xy-centre)@vh[-1])) <= 1e-4:
                coordinate=(xy-centre)@vh[0]
                ids=[ids[int(np.argmin(coordinate))], ids[int(np.argmax(coordinate))]]
        for vi in ids:
            row=np.zeros(3*len(labels));row[ga*3:ga*3+3]=chart[vi];row[gb*3:gb*3+3]-=chart[vi]
            constraints.append(row);fixed_values.append(0.)
    if keep_boundary:
        for gi, label in enumerate(labels):
            for vi in boundaries[label]:
                row=np.zeros(3*len(labels)); row[gi*3:gi*3+3]=chart[vi]
                constraints.append(row); fixed_values.append(v[vi, axis])
    A, b = np.asarray(rows), np.asarray(targets)
    if constraints:
        C, d = np.asarray(constraints), np.asarray(fixed_values)
        start = np.linalg.lstsq(C, d, rcond=None)[0]
        if np.max(np.abs(C@start-d)) > 1e-7:
            raise ValueError('selected borders are not coplanar; retaining them and flattening is impossible')
        _, singular, vh = np.linalg.svd(C, full_matrices=True)
        # Chart coordinates are normalized to the part's span. Float32 source
        # rounding can add spurious independent constraints at a multi-face
        # junction; retaining those would collapse every adjoining plane into
        # one. The final shared-position residual is still checked in mm below.
        rank = np.sum(singular > max(1e-7, max(C.shape)*np.finfo(float).eps*singular[0]))
        null = vh[rank:].T
        coef = start + null@np.linalg.lstsq(A@null, b-A@start, rcond=None)[0]
    else:
        coef = np.linalg.lstsq(A, b, rcond=None)[0]
    out = v.copy()
    for vi, faces in membership.items():
        values = [float(chart[vi]@coef[gi*3:gi*3+3]) for gi in faces]
        if np.ptp(values) > 1e-4:
            raise ValueError('joint fit did not satisfy the shared border')
        out[vi, axis] = np.mean(values)
    moved = np.linalg.norm(out-v, axis=1)
    plane_rows = {}
    for gi, label in enumerate(labels):
        ids = np.unique(f[np.asarray(groups[label], int)])
        normalized = coef[gi*3:gi*3+3]
        c = np.r_[normalized[0]-np.sum(normalized[1:]*chart_centre/chart_scale), normalized[1:]/chart_scale]
        normal = np.zeros(3);normal[axis]=1.;normal[dims]=-c[1:];normal/=np.linalg.norm(normal)
        old = np.linalg.lstsq(chart[ids], v[ids, axis], rcond=None)[0]
        old[1:] /= chart_scale
        old_normal=np.zeros(3);old_normal[axis]=1.;old_normal[dims]=-old[1:];old_normal/=np.linalg.norm(old_normal)
        residual=(out[ids, axis]-chart[ids]@normalized)/np.sqrt(1+c[1:]@c[1:])
        plane_rows[label]={'normal':normal.tolist(),'axis_plane_coef':c.tolist(),
                           'normal_change_deg':float(np.degrees(np.arccos(np.clip(old_normal@normal,-1,1)))),
                           'max_residual_mm':float(np.max(np.abs(residual))),
                           'border_max_move_mm':float(moved[boundaries[label]].max()),
                           'max_move_mm':float(moved[ids].max()),'vertices':len(ids)}
    return out, {'informational_only':True,'axis':axis,'keep_boundary':bool(keep_boundary),
                 'selected_vertices':len(membership),'changed_vertices':int(np.sum(moved>1e-8)),
                 'max_move_mm':float(moved.max()),'faces':plane_rows,
                 'boundary_vertex_ids':boundaries,
                 'disclosure':'Moving a bent border and changing adjacent face angles is unavoidable for this joint fit.' if not keep_boundary else 'The selected borders remain fixed.'}


def _fit_free_group(v, f, groups, *, keep_boundary):
    """Keep each face's area-weighted best-fit normal; project shared vertices
    to the planes' common intersection. All three coordinates may move. This
    avoids forcing nearly collinear chart crease layouts into one plane.
    """
    if not groups or v.ndim != 2 or v.shape[1] != 3 or not np.isfinite(v).all():
        raise ValueError('finite 3D vertices and labelled triangle groups required')
    rows, membership, boundaries, seen = {}, {}, {}, set()
    for label, ids in groups.items():
        ids=np.asarray(ids, int)
        if not len(ids) or np.any(ids<0) or np.any(ids>=len(f)):
            raise ValueError(f'face {label} has no valid triangle selection')
        if seen.intersection(map(int,ids)):
            raise ValueError('a triangle cannot belong to two face labels')
        seen.update(map(int,ids));tri=f[ids];p=v[tri]
        cross=np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0]);area=np.linalg.norm(cross,axis=1)/2
        weights=np.repeat(area/3,3);points=p.reshape(-1,3)
        if weights.sum()<=1e-10:
            raise ValueError(f'face {label} has no area')
        centre=np.average(points,axis=0,weights=weights)
        _,_,vh=np.linalg.svd((points-centre)*np.sqrt(weights[:,None]),full_matrices=False)
        n=vh[-1];n*=1 if n@np.sum(cross,axis=0)>=0 else -1
        rows[label]=(n,float(n@centre))
        counts={}
        for face in tri:
            for vi in face:membership.setdefault(int(vi),set()).add(label)
            for a,b in zip(face,np.roll(face,-1)):
                key=tuple(sorted((int(a),int(b))));counts[key]=counts.get(key,0)+1
        boundaries[label]=sorted({i for edge,count in counts.items() if count==1 for i in edge})
    boundary={i for ids in boundaries.values() for i in ids};out=v.copy()
    for vi,labels in membership.items():
        N=np.array([rows[label][0] for label in sorted(labels)]);d=np.array([rows[label][1] for label in sorted(labels)])
        delta=np.linalg.lstsq(N,d-N@v[vi],rcond=None)[0]
        if keep_boundary and vi in boundary and np.linalg.norm(delta)>1e-7:
            raise ValueError('selected borders are not coplanar; retaining them and flattening is impossible')
        out[vi]+=delta
        if np.max(np.abs(N@out[vi]-d))>1e-7:
            raise ValueError('selected planes have no common intersection at a shared corner')
    moved=np.linalg.norm(out-v,axis=1);faces={}
    for label,ids in groups.items():
        used=np.unique(f[np.asarray(ids,int)]);n,d=rows[label]
        faces[label]={'normal':n.tolist(),'plane_offset_mm':d,'normal_change_deg':0.,
                      'max_residual_mm':float(np.max(np.abs(out[used]@n-d))),
                      'border_max_move_mm':float(moved[boundaries[label]].max()),
                      'max_move_mm':float(moved[used].max()),'vertices':len(used)}
    return out,{'informational_only':True,'axis':None,'keep_boundary':bool(keep_boundary),'selected_vertices':len(membership),
                'changed_vertices':int(sum(moved>1e-8)),'max_move_mm':float(moved.max()),'faces':faces,
                'boundary_vertex_ids':boundaries,'disclosure':'The original best-fit plane normals are kept; bent borders and shared corners move to the plane intersections.'}


def fit_chart_group(vertices, triangles, groups, footprints):
    """Fit X=a+b*d+c*y to explicit neighbouring chart footprints.

    Coordinates are (d,y,X), in millimetres. ``footprints`` maps the same labels
    as ``groups`` to named corners, e.g. {label: {"A": [d,y], ...}}. Equal corner
    names impose exact equal X values on adjoining planes; shared named edges
    consequently remain straight and joined. The actual input triangles supply
    area-weighted fit observations. This returns plane coefficients only: the
    caller must rebuild the surrounding rolls/base with the strict solid builder,
    transfer markings, preview border/angle changes and obtain confirmation.
    A saved chart must be validated against current geometry before calling.
    """
    v,f=np.asarray(vertices,float),np.asarray(triangles,int)
    if v.ndim!=2 or v.shape[1]!=3 or not np.isfinite(v).all() or f.ndim!=2 or f.shape[1]!=3:
        raise ValueError('finite (d,y,X) vertices and triangles are required')
    if not groups or set(groups)!=set(footprints):
        raise ValueError('one named footprint is required for each selected face')
    labels=sorted(groups);chart=np.c_[np.ones(len(v)),v[:,:2]];A=[];b=[];nodes={};before={}
    for gi,label in enumerate(labels):
        ids=np.asarray(groups[label],int)
        if ids.ndim!=1 or not len(ids) or np.any(ids<0) or np.any(ids>=len(f)):
            raise ValueError(f'face {label} has no valid triangle selection')
        p=v[f[ids]];area=np.linalg.norm(np.cross(p[:,1]-p[:,0],p[:,2]-p[:,0]),axis=1)/2
        if area.sum()<=1e-10:raise ValueError(f'face {label} has no area')
        rows=[];target=[]
        for tri,w in zip(f[ids],area):
            for vi in tri:
                row=np.zeros(3*len(labels));row[3*gi:3*gi+3]=chart[vi]*np.sqrt(w/3)
                A.append(row);b.append(v[vi,2]*np.sqrt(w/3));rows.append(chart[vi]*np.sqrt(w/3));target.append(v[vi,2]*np.sqrt(w/3))
        before[label]=np.linalg.lstsq(rows,target,rcond=None)[0]
        if len(footprints[label])<3:raise ValueError('a footprint needs three or more named corners')
        for name,xy in footprints[label].items():
            xy=np.asarray(xy,float)
            if xy.shape!=(2,) or not np.isfinite(xy).all():raise ValueError('chart corners must be finite pairs')
            if name in nodes and not np.allclose(nodes[name][0],xy,atol=1e-8,rtol=0):
                raise ValueError(f'shared corner {name} has inconsistent coordinates')
            nodes.setdefault(name,(xy,[]))[1].append(gi)
    C=[]
    for xy,owners in nodes.values():
        for gi in owners[1:]:
            row=np.zeros(3*len(labels));row[3*gi:3*gi+3]=np.r_[1,xy];row[3*owners[0]:3*owners[0]+3]-=np.r_[1,xy];C.append(row)
    A,b=np.asarray(A),np.asarray(b)
    if C:
        C=np.asarray(C);_,s,vh=np.linalg.svd(C,full_matrices=True);rank=np.sum(s>1e-9);null=vh[rank:].T
        coef=null@np.linalg.lstsq(A@null,b,rcond=None)[0]
        if abs(C@coef).max()>1e-7:raise ValueError('joint corner constraints did not converge')
    else:coef=np.linalg.lstsq(A,b,rcond=None)[0]
    planes={label:coef[3*gi:3*gi+3].tolist() for gi,label in enumerate(labels)};report={}
    for label,c in planes.items():
        c=np.asarray(c);old=before[label];n=np.r_[-c[1:],1.];n/=np.linalg.norm(n);oldn=np.r_[-old[1:],1.];oldn/=np.linalg.norm(oldn)
        ids=np.unique(f[groups[label]]);motion=chart[ids]@c-v[ids,2]
        report[label]={'normal_change_deg':float(np.degrees(np.arccos(np.clip(n@oldn,-1,1)))),
                       'fit_max_move_mm':float(abs(motion).max()),'fit_rms_move_mm':float(np.sqrt(np.mean(motion**2))),
                       'normal_dyX':n.tolist(),'plane_coef':c.tolist()}
    return planes,{'informational_only':True,'faces':report,'disclosure':'Chart corner heights, borders and adjacent facet angles can change; surrounding geometry must be rebuilt and checked.'}
