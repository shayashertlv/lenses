"""Exact graph clearance for surface edits whose rim can have zero thickness.

Float64 graph checks still validate coverage. Rational arithmetic distinguishes
true crossings from cancellation at shared zero-clearance rim edges; no physical
thickness tolerance or threshold is changed.
"""
from fractions import Fraction as F
from collections import Counter
import hashlib
import numpy as np


def cross(a,b):return a[0]*b[1]-a[1]*b[0]
def sub(a,b):return (a[0]-b[0],a[1]-b[1])


def intersection(subject,clip):
    result=list(subject)
    for a,b in zip(clip,clip[1:]+clip[:1]):
        if not result:break
        old,result=result,[];previous=old[-1];dp=cross(sub(b,a),sub(previous,a))
        for current in old:
            dc=cross(sub(b,a),sub(current,a))
            if (dc>=0)!=(dp>=0):
                t=dp/(dp-dc)
                result.append((previous[0]+(current[0]-previous[0])*t,previous[1]+(current[1]-previous[1])*t))
            if dc>=0:result.append(current)
            previous,dp=current,dc
    return result


def plane(triangle):
    a,b,c=triangle
    denominator=cross(sub(b,a),sub(c,a))
    x=((b[2]-a[2])*(c[1]-a[1])-(c[2]-a[2])*(b[1]-a[1]))/denominator
    y=((b[0]-a[0])*(c[2]-a[2])-(c[0]-a[0])*(b[2]-a[2]))/denominator
    return a[2]-x*a[0]-y*a[1],x,y


def _minimum(vertices,triangles, *, footprint=None, wall_angle=0., position_error=0.):
    points=[tuple(F(v) for v in row) for row in vertices]
    outer,inner=[],[]
    xyz=np.asarray(vertices)
    for ids in triangles:
        p=[points[i] for i in ids]
        area=cross(sub(p[1],p[0]),sub(p[2],p[0]))
        # Even a very narrow, representable triangle may contain a crossing.
        # Only an exactly vertical projection has no graph domain.
        if area==0:continue
        if area>0:outer.append((ids,p,plane(p)))
        elif area<0:
            p=p[::-1];inner.append((ids,p,plane(p)))
    if not outer or not inner:raise ValueError('the exact surface has no opposing graph pair')
    lo=np.array([xyz[list(row[0]),:2].min(0) for row in inner])
    hi=np.array([xyz[list(row[0]),:2].max(0) for row in inner])
    active_inner=(np.ones(len(lo),bool) if footprint is None else
                  np.all(hi>=footprint[0],axis=1)&np.all(lo<=footprint[1],axis=1))
    # Spatial acceleration only: every bounding-box candidate is retained.
    cell=max(float(np.median(np.max(hi-lo,axis=1))),.1)
    bins={}
    for j,(mn,mx) in enumerate(zip(lo,hi)):
        a,b=np.floor(mn/cell).astype(int),np.floor(mx/cell).astype(int)
        for x in range(a[0],b[0]+1):
            for y in range(a[1],b[1]+1):bins.setdefault((x,y),[]).append(j)
    smallest=None;overlaps=0
    for ids,p,pc in outer:
        mn=xyz[list(ids),:2].min(0);mx=xyz[list(ids),:2].max(0)
        active=footprint is None or (np.all(mx>=footprint[0]) and np.all(mn<=footprint[1]))
        a,b=np.floor(mn/cell).astype(int),np.floor(mx/cell).astype(int)
        candidates=set()
        for x in range(a[0],b[0]+1):
            for y in range(a[1],b[1]+1):candidates.update(bins.get((x,y),()))
        for j in sorted(candidates):
            if (not active and not active_inner[j]) or np.any(hi[j]<mn) or np.any(lo[j]>mx):continue
            _,q,qc=inner[j]
            poly=intersection([(v[0],v[1]) for v in p],[(v[0],v[1]) for v in q])
            if len(poly)<3:continue
            area=sum(cross(a,b) for a,b in zip(poly,poly[1:]+poly[:1]))
            if area==0:continue
            delta=[a-b for a,b in zip(pc,qc)]
            value=min(delta[0]+delta[1]*x+delta[2]*y for x,y in poly)
            if smallest is None or value<smallest:smallest=value
            overlaps+=1
    if smallest is None:raise ValueError('no exact positive-area opposing surface overlap')
    return smallest,overlaps


def minimum(vertices,triangles, *, footprint=None, wall_angle=0., position_error=0.):
    # owner_mesh graph coordinates [X thickness,Y height,Z -horizontal].
    xyz=np.asarray(vertices,float)
    chart=np.c_[-xyz[:,2],xyz[:,1],xyz[:,0]]
    value,count=_minimum(chart,triangles,footprint=footprint,wall_angle=wall_angle,position_error=position_error)
    return value,{'minimum_mm':float(value),'exact_fraction':str(value),'overlap_polygons':count,
                  'method':'exact rational projected-triangle intersection'}


# Digest keys and small certificates only: no retained mesh/triangle tuples.
_certificates={}


def source_certificate(coordinates,triangles, *, wall_angle=0., position_error=0.):
    """Reuse an immutable source proof across options/rechecks in one process.

    A new source, topology or frozen chart necessarily has a new digest. The
    trusted worker independently checks it once; no caller-supplied certificate
    can bypass a physical refusal.
    """
    from . import owner_mesh as M
    v=np.ascontiguousarray(coordinates,dtype=np.float64)
    t=np.ascontiguousarray(triangles,dtype=np.int64)
    key=hashlib.sha256(repr((v.shape,t.shape,wall_angle,position_error)).encode()+v.tobytes()+t.tobytes()).hexdigest()
    if key not in _certificates:
        try:M.graph_thickness(v,t,1,1e-12,wall_angle=wall_angle,position_error=position_error)
        except M.OwnerMeshError as exc:
            if exc.code!='E_TOO_THIN':raise
        _certificates[key]=minimum(v,t,wall_angle=wall_angle,position_error=position_error)
    value,report=_certificates[key]
    return value,{**report,'source_certificate_sha256':key}


def changed_footprint(source,target,source_chart,target_chart):
    """Bounds of every changed triangle; the complement is proven unchanged.

    Compare actual tessellations as well as vertices. A topology-changing
    operation cannot borrow an unchanged-remainder certificate from old fans.
    """
    def rows(obj,chart):
        result=Counter()
        for ids in obj['triangles']:
            points=chart[np.asarray(ids,int)];key=tuple(map(tuple,points))
            key=min(key,key[1:]+key[:1],key[2:]+key[:2])
            result[key]+=1
        return result
    before,after=rows(source,source_chart),rows(target,target_chart)
    changed=[np.asarray(k)[:,:2] for k in (before-after)]+[np.asarray(k)[:,:2] for k in (after-before)]
    if not changed:return None
    xy=np.concatenate(changed)
    return xy.min(0),xy.max(0)


def wall_polygons(xyz,polygons,angle,position_error,*,with_caps=False):
    """Identify planar rims joining both graph surfaces, never narrow bevels.

    A near-vertical bevel can still determine a strictly positive wall floor.
    Angular similarity alone therefore grants no exemption: a rim must meet
    both opposing non-wall surfaces in the captured topology.
    """
    walls=set();positive=set();negative=set()
    for i,p in enumerate(polygons):
        points=np.asarray(xyz)[p];center=points.mean(0)
        normal=np.cross(points-center,np.roll(points,-1,axis=0)-center).sum(0)
        length=np.linalg.norm(normal)
        if length==0:continue
        normal/=length
        if abs(normal[2])<=angle and np.max(abs((points-center)@normal))<=position_error:
            walls.add(i)
        elif normal[2]>0:positive.update(p)
        elif normal[2]<0:negative.update(p)
    rims={i for i in walls if positive.intersection(polygons[i]) and negative.intersection(polygons[i])}
    return (rims,positive|negative) if with_caps else rims


def vertical_chart(xyz,polygons,angle,*,walls=None,surface_vertices=None):
    """Represent float32-vertical edges by one projected coordinate.

    Only projection coordinates are coalesced, within the declared input
    representation bound. Heights and every physical thickness floor stay exact.
    Connected nearly vertical edges share one mean, independent of winding/order.
    """
    xyz=np.asarray(xyz,float).copy();parent=list(range(len(xyz)))
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    edge_faces={}
    for i,p in enumerate(polygons):
        for a,b in zip(p,p[1:]+p[:1]):edge_faces.setdefault(tuple(sorted((a,b))),set()).add(i)
    for (a,b),faces in edge_faces.items():
        # A boundary shared with a graph surface carries real clearance, even
        # when its width is below the wall-classification angular bound.
        if walls is not None and not faces.issubset(walls):continue
        if surface_vertices is not None and not {a,b}.issubset(surface_vertices):continue
        delta=xyz[a]-xyz[b]
        if np.linalg.norm(delta[:2])<=angle*abs(delta[2]):parent[find(a)]=find(b)
    groups={}
    for i in range(len(xyz)):groups.setdefault(find(i),[]).append(i)
    for ids in groups.values():
        if len(ids)>1:xyz[ids,:2]=xyz[ids,:2].mean(0)
    return xyz


def marking_gap(mark_vertices,mark_triangles,surface_vertices,surface_triangles,normal):
    """Exact minimum under every marking triangle in a fixed local normal chart."""
    normal=np.asarray(normal,float);normal/=np.linalg.norm(normal)
    seed=np.eye(3)[np.argmin(abs(normal))];u=np.cross(seed,normal);u/=np.linalg.norm(u);v=np.cross(normal,u)
    axes=np.array([u,v,normal]);center=np.asarray(mark_vertices).mean(0)
    def rows(vertices,triangles,front_only):
        xyz=(np.asarray(vertices)-center) @ axes.T
        rational=[tuple(F(float(x)) for x in p) for p in xyz];result=[]
        for ids in triangles:
            p=[rational[i] for i in ids];area=cross(sub(p[1],p[0]),sub(p[2],p[0]))
            if area==0 or (front_only and area<0):continue
            if area<0:p=p[::-1]
            result.append((xyz[list(ids),:2].min(0),xyz[list(ids),:2].max(0),p,plane(p)))
        return result
    marks=rows(mark_vertices,mark_triangles,False);surface=rows(surface_vertices,surface_triangles,True)
    if not marks or not surface:raise ValueError('marking clearance has no projected surface')
    lo=np.array([r[0] for r in surface]);hi=np.array([r[1] for r in surface]);smallest=None
    for mn,mx,p,pc in marks:
        for j in np.flatnonzero(np.all(hi>=mn,axis=1)&np.all(lo<=mx,axis=1)):
            _,_,q,qc=surface[j]
            poly=intersection([(x[0],x[1]) for x in p],[(x[0],x[1]) for x in q])
            if len(poly)<3 or sum(cross(a,b) for a,b in zip(poly,poly[1:]+poly[:1]))==0:continue
            delta=[a-b for a,b in zip(pc,qc)]
            value=min(delta[0]+delta[1]*x+delta[2]*y for x,y in poly)
            if smallest is None or value<smallest:smallest=value
    if smallest is None:raise ValueError('marking no longer projects onto its surface')
    return smallest


def _area2(poly):
    return sum(cross(a,b) for a,b in zip(poly,poly[1:]+poly[:1])) if len(poly)>=3 else F(0)


def _on_segment(point,a,b):
    return cross(sub(point,a),sub(b,a))==0 and all(min(a[k],b[k])<=point[k]<=max(a[k],b[k]) for k in (0,1))


def local_patches(source,target,faces,*,preserve_original=True,minimum_mm=None):
    """Exact local wall checks when the complete part is not one graph solid.

    The selected patch boundary and every unselected polygon must remain exact.
    Each connected selected patch must itself be an injective projected graph,
    completely covered once by an unchanged opposing graph. Every overlapping
    unselected surface keeps its original side and contact locus, including
    surfaces which are not the opposing wall. Unsupported vertical obstacles or
    ambiguous/crossing source charts refuse rather than being omitted.
    Separately selected patches whose projected bounds overlap refuse: another
    moving patch cannot be certified as an unchanged obstacle.
    """
    from . import owner_surface as S
    floor=minimum_floor(minimum_mm,preserve_original=preserve_original)
    v=np.asarray(source['vertices_world_mm']);w=np.asarray(target['vertices_world_mm'])
    polygons=source['polygons'];chosen=set(faces)
    for i,p in enumerate(polygons):
        if i not in chosen and not np.array_equal(v[p],w[p]):
            raise ValueError('local wall check requires every unselected polygon to stay unchanged')
    parents=target.get('_surface_polygon_parents',list(range(len(target['polygons']))))
    def polygon_ids(obj):
        if 'triangle_polygons' in obj:return obj['triangle_polygons']
        fans=[[p[0],p[k],p[k+1]] for p in obj['polygons'] for k in range(1,len(p)-1)]
        if obj['triangles']!=fans:raise ValueError('local wall proof requires actual triangle-to-polygon correspondence')
        return [i for i,p in enumerate(obj['polygons']) for _ in range(len(p)-2)]
    source_tri=np.asarray(source['triangles'],int);source_pid=np.asarray(polygon_ids(source),int)
    target_tri=np.asarray(target['triangles'],int)
    target_pid=np.asarray([parents[i] for i in polygon_ids(target)],int)
    orientation=S.orientation_sign(source)
    if orientation<0:source_tri=source_tri[:,::-1];target_tri=target_tri[:,::-1]
    by_vertex={}
    for i in faces:
        for vertex in polygons[i]:by_vertex.setdefault(vertex,set()).add(i)
    components=[];pending=set(faces)
    while pending:
        component={pending.pop()};queue=list(component)
        while queue:
            i=queue.pop()
            for vertex in polygons[i]:
                additions=by_vertex[vertex]&pending
                pending-=additions;component|=additions;queue.extend(additions)
        components.append(sorted(component))
    reports=[]
    for component in components:
        edges={}
        for i in component:
            p=polygons[i]
            for a,b in zip(p,p[1:]+p[:1]):
                edge=tuple(sorted((a,b)));edges[edge]=edges.get(edge,0)+1
        boundary=[edge for edge,count in edges.items() if count==1]
        fixed=sorted({i for edge in boundary for i in edge})
        if not boundary or not np.array_equal(v[fixed],w[fixed]):
            raise ValueError('local wall check needs an unchanged, nonempty patch boundary')
        center,basis,_=S._chart(v,polygons,component,orientation=orientation)
        xyz=(v-center)@basis.T;after_xyz=(w-center)@basis.T
        exact=[tuple(F(float(x)) for x in p) for p in xyz]
        exact_after=[tuple(F(float(x)) for x in p) for p in after_xyz]
        border=[(exact[a][:2],exact[b][:2]) for a,b in boundary]
        selected=np.flatnonzero(np.isin(source_pid,component))
        after_selected=np.flatnonzero(np.isin(target_pid,component))
        others=np.flatnonzero(~np.isin(source_pid,component))
        footprint=np.vstack([xyz[source_tri[selected].ravel(),:2],after_xyz[target_tri[after_selected].ravel(),:2]])
        footprint_low,footprint_high=footprint.min(0),footprint.max(0)
        # Other selected components can move too. Never certify clearance to
        # their stale source state. Both source and target bounds are checked,
        # since a previously distant component could move into this chart.
        other_faces=chosen-set(component)
        for points,triangles,pids in ((xyz,source_tri,source_pid),(after_xyz,target_tri,target_pid)):
            moving=np.flatnonzero(np.isin(pids,list(other_faces)))
            if len(moving):
                other=points[triangles[moving],:2]
                if np.any(np.all(other.max(1)>=footprint_low,axis=1)&np.all(other.min(1)<=footprint_high,axis=1)):
                    raise ValueError('separately selected patches overlap this local chart; edit separately from fresh snapshots')
        obstacle_points=xyz[source_tri[others],:2]
        others=others[np.all(obstacle_points.max(1)>=footprint_low,axis=1)&
                      np.all(obstacle_points.min(1)<=footprint_high,axis=1)]

        def rows(points,triangles,ids):
            result=[]
            for index in ids:
                ids_v=triangles[index];p=[points[i] for i in ids_v];area=cross(sub(p[1],p[0]),sub(p[2],p[0]))
                xy=[q[:2] for q in p]
                if area<0:p=p[::-1];xy=xy[::-1]
                result.append({'id':int(index),'xy':xy,'area':area,'plane':plane(p) if area else None,
                               'lo':np.min(np.asarray(xy,float),axis=0),'hi':np.max(np.asarray(xy,float),axis=0)})
            return result

        patch=rows(exact,source_tri,selected);updated=rows(exact_after,target_tri,after_selected)
        obstacles=rows(exact,source_tri,others)
        def overlap(a,b):
            return not (np.any(a['hi']<b['lo']) or np.any(b['hi']<a['lo']))

        def graph(rows_):
            if any(r['area']<=0 for r in rows_):raise ValueError('selected patch folds in its frozen source chart')
            for i,a in enumerate(rows_):
                for b in rows_[i+1:]:
                    if overlap(a,b) and _area2(intersection(a['xy'],b['xy']))>0:
                        raise ValueError('selected patch overlaps itself in its frozen source chart')
        graph(patch);graph(updated)
        if sum(r['area'] for r in patch)!=sum(r['area'] for r in updated):
            raise ValueError('selected patch projected footprint changed')
        relations={};contacts={}

        def measure(rows_,baseline):
            smallest=None;overlaps=0
            for a in rows_:
                opposing=[]
                for b in obstacles:
                    if not overlap(a,b):continue
                    if b['area']==0:
                        if not any(all(_on_segment(point,p,q) for point in b['xy']) for p,q in border):
                            raise ValueError('an unproved vertical obstacle overlaps the local patch chart')
                        continue
                    poly=intersection(a['xy'],b['xy'])
                    if _area2(poly)==0:continue
                    difference=[x-y for x,y in zip(a['plane'],b['plane'])]
                    gaps=[difference[0]+difference[1]*x+difference[2]*y for x,y in poly]
                    low,high=min(gaps),max(gaps)
                    if low<0<high or low==high==0:
                        raise ValueError('local patch intersects or coincides with an unchanged surface')
                    relation=1 if low>=0 else -1
                    zero=[point for point,gap in zip(poly,gaps) if gap==0]
                    if baseline:
                        if b['id'] in relations and relations[b['id']]!=relation:
                            raise ValueError('unchanged surface crosses the selected source chart')
                        relations[b['id']]=relation
                        if zero:contacts.setdefault(b['id'],[]).append((zero[0],zero[-1]))
                    else:
                        if relations.get(b['id'])!=relation:
                            raise ValueError('local edit crossed an unchanged surface')
                        allowed=contacts.get(b['id'],[])
                        if zero and not any(all(_on_segment(point,p,q) for point in zero) for p,q in allowed):
                            raise ValueError('local edit creates a new contact with an unchanged surface')
                    if b['area']<0 and relation==1:
                        opposing.append(poly);overlaps+=1
                        smallest=low if smallest is None else min(smallest,low)
                if sum(_area2(p) for p in opposing)!=a['area']:
                    raise ValueError('local patch is not completely covered once by its opposing surface')
                for i,p in enumerate(opposing):
                    for q in opposing[i+1:]:
                        if _area2(intersection(p,q))>0:
                            raise ValueError('opposing surfaces overlap below the selected patch')
            if smallest is None:raise ValueError('local patch has no opposing surface')
            return smallest,overlaps

        before_min,before_count=measure(patch,True)
        after_min,after_count=measure(updated,False)
        required=max(before_min if preserve_original else F(0),floor)
        if before_min<0 or after_min<required:
            raise ValueError('local edit reduces opposing clearance below its required minimum')
        reports.append({'source_polygon_ids':component,'boundary_vertex_ids':fixed,
                        'before_minimum_mm':float(before_min),'after_minimum_mm':float(after_min),
                        'before_exact':str(before_min),'after_exact':str(after_min),
                        'required_minimum_mm':float(required),'required_minimum_exact':str(required),
                        'opposing_overlaps_before':before_count,'opposing_overlaps_after':after_count,
                        'unchanged_surface_relations':len(relations)})
    return {'method':'exact selected local graph patches with fixed boundary and unchanged obstacles',
            'patches':reports,'original_minimum_preserved':bool(preserve_original),
            'new_intersections_or_contacts':False,'unselected_geometry_unchanged':True}


def minimum_floor(minimum_mm,*,preserve_original):
    """Return the exact explicit floor; generic thinning never defaults to zero."""
    if minimum_mm is None:
        if not preserve_original:
            raise ValueError('generic thinning requires an explicit positive minimum_mm')
        return F(0)
    if isinstance(minimum_mm,bool) or not isinstance(minimum_mm,(int,float)) or not np.isfinite(minimum_mm) or minimum_mm<=0:
        raise ValueError('minimum_mm must be finite and strictly positive')
    return F(float(minimum_mm))
