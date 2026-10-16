"""Exact graph clearance for surface edits whose rim can have zero thickness.

Float64 graph checks still validate coverage. Rational arithmetic distinguishes
true crossings from cancellation at shared zero-clearance rim edges; no physical
thickness tolerance or threshold is changed.
"""
from fractions import Fraction as F
from functools import lru_cache
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


@lru_cache(maxsize=24)
def _minimum(vertices,triangles):
    points=[tuple(F(v) for v in row) for row in vertices]
    outer,inner=[],[]
    for ids in triangles:
        p=[points[i] for i in ids]
        area=cross(sub(p[1],p[0]),sub(p[2],p[0]))
        if area>0:outer.append((ids,p,plane(p)))
        elif area<0:
            p=p[::-1];inner.append((ids,p,plane(p)))
    if not outer or not inner:raise ValueError('the exact surface has no opposing graph pair')
    xyz=np.asarray(vertices)
    lo=np.array([xyz[list(row[0]),:2].min(0) for row in inner])
    hi=np.array([xyz[list(row[0]),:2].max(0) for row in inner])
    smallest=None;overlaps=0
    for ids,p,pc in outer:
        mn=xyz[list(ids),:2].min(0);mx=xyz[list(ids),:2].max(0)
        candidates=np.flatnonzero(np.all(hi>=mn,axis=1)&np.all(lo<=mx,axis=1))
        for j in candidates:
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


def minimum(vertices,triangles):
    # owner_mesh graph coordinates [X thickness,Y height,Z -horizontal].
    xyz=np.asarray(vertices,float)
    chart=np.c_[-xyz[:,2],xyz[:,1],xyz[:,0]]
    value,count=_minimum(tuple(tuple(float(x) for x in p) for p in chart),tuple(tuple(int(i) for i in t) for t in triangles))
    return value,{'minimum_mm':float(value),'exact_fraction':str(value),'overlap_polygons':count,
                  'method':'exact rational projected-triangle intersection'}


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
    source_tri=np.asarray(source['triangles'],int);source_pid=np.asarray(source['triangle_polygons'],int)
    target_tri=np.asarray(target['triangles'],int)
    target_pid=np.asarray([parents[i] for i in target['triangle_polygons']],int)
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
        center,basis,_=S._chart(v,polygons,component)
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
