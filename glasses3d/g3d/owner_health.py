"""Conservative mesh-health planning shared by main runs and confirmed edits.

Cleanup welds exact duplicates and removes exactly zero-area faces only. Controlled
decimation removes complete planar interior fans without moving retained points;
it preserves silhouette, sharp edges, UV/material seams and marking attachments.
Symmetry uses explicit source/destination vertex pairs about asset X=0. No helper
relaxes closed-solid or minimum-thickness checks, and inputs are never modified.
"""
from __future__ import annotations
import copy
import math
from fractions import Fraction
import numpy as np

OPERATION_KINDS=frozenset({'mesh_cleanup','consistent_normals','symmetrize','controlled_decimate'})


def _sharp(obj):
    values=next((a['values'] for a in obj.get('attributes',[]) if a['name']=='sharp_edge'),None)
    return {tuple(sorted(e)) for i,e in enumerate(obj.get('edges',[])) if values is not None and values[i][0]}


def _records(obj):
    records=[];cursor=0
    for i,p in enumerate(obj['polygons']):
        n=len(p);records.append({'p':list(p),'parent':i,'normal':copy.deepcopy(obj['corner_normals'][cursor:cursor+n]),
            'uv':[copy.deepcopy(u['uv'][cursor:cursor+n]) for u in obj.get('uv_layers',[])],
            'smooth':obj['polygon_smooth'][i],'material':obj['polygon_material'][i]});cursor+=n
    return records


def _cross(v,p):
    a=v[p]-v[p[0]];return np.sum(np.cross(a,np.roll(a,-1,axis=0)),axis=0)


def _zero_polygon(v,indices):
    """Exact collinearity; a self-crossing polygon's cancelling area is not zero geometry."""
    p=[[Fraction(float(x)) for x in v[i]] for i in indices]
    direction=next(([q[k]-p[0][k] for k in range(3)] for q in p[1:] if q!=p[0]),None)
    if direction is None:return True
    a=direction
    for q in p[1:]:
        b=[q[k]-p[0][k] for k in range(3)]
        if a[1]*b[2]!=a[2]*b[1] or a[2]*b[0]!=a[0]*b[2] or a[0]*b[1]!=a[1]*b[0]:return False
    return True


def _rebuild(obj,records,vertices,remap=None):
    """Keep source vertex identities unless a topology edit explicitly compacts."""
    result=copy.deepcopy(obj);original=np.asarray(obj['vertices_world_mm'],float)
    used=[i for i in range(len(vertices)) if i not in (remap or {})]
    index={old:new for new,old in enumerate(used)}
    world=np.asarray(vertices,float)[used]
    matrix=np.asarray(obj.get('matrix_world',np.eye(4)),float)
    origin=original[0]-np.asarray(obj['vertices_local'][0])@matrix[:3,:3].T-matrix[:3,3]
    local=((world-origin-matrix[:3,3])@np.linalg.inv(matrix[:3,:3]).T).astype(np.float32).astype(float)
    for j,i in enumerate(used):
        if np.array_equal(world[j],original[i]):local[j]=obj['vertices_local'][i]
    world=local@matrix[:3,:3].T+matrix[:3,3]+origin
    polys=[[index[i] for i in r['p']] for r in records]
    result.update(vertices_world_mm=world.tolist(),vertices_local=local.tolist(),polygons=polys,
        triangles=[[p[0],p[k],p[k+1]] for p in polys for k in range(1,len(p)-1)],
        triangle_polygons=[j for j,p in enumerate(polys) for _ in range(len(p)-2)],
        corner_normals=[n for r in records for n in r['normal']],polygon_smooth=[r['smooth'] for r in records],
        polygon_material=[r['material'] for r in records],
        uv_layers=[{**layer,'uv':[uv for r in records for uv in r['uv'][j]]} for j,layer in enumerate(obj.get('uv_layers',[]))],
        _surface_polygon_parents=[r['parent'] for r in records])
    if remap is not None:
        from . import owner_mesh as M
        constraints=M.temple_constraints(obj)
        result['_surface_vertex_parents']=used
        result['_surface_vertex_remap']=[index.get(remap.get(i,i)) for i in range(len(original))]
        result['aliases']={'status':'unavailable','reason':'explicit selected mesh-health topology edit; prior builder correspondence is stale'}
        if constraints is not None:
            result['aliases']['declared_constraints']=constraints
    return result


def _attributes(obj):
    from . import owner_surface as S
    if obj.get('attributes') is None:S.error('mesh health needs a fresh snapshot with complete attributes')
    supported={'position','.edge_verts','.corner_vert','.corner_edge','material_index','sharp_face','sharp_edge',
               'custom_normal','g3d_owner_normal_intent','.select_vert','.select_edge','.select_poly',*[u['name'] for u in obj.get('uv_layers',[])]}
    if any(a['name'] not in supported for a in obj['attributes']):S.error('mesh health cannot preserve an unsupported mesh attribute')


def _cleanup(obj,records,v,selected,strength,support):
    from . import owner_surface as S
    adjacency={i:set() for i in range(len(v))}
    for j,r in enumerate(records):
        for i in r['p']:adjacency[i].add(j)
    groups={}
    for i,p in enumerate(v):
        if i in support and adjacency[i]<=selected:groups.setdefault(tuple(p),[]).append(i)
    pairs=[(i,ids[0]) for ids in groups.values() for i in ids[1:]]
    limit=math.ceil(len(pairs)*strength);remap={i:j for i,j in pairs[:limit]};pending={i for i,j in pairs[limit:]}
    rows=[];removed=[]
    for j,r in enumerate(records):
        if j in selected:
            r['p']=[remap.get(i,i) for i in r['p']]
            if _zero_polygon(np.asarray(obj['vertices_local']),r['p']) and not pending.intersection(r['p']):
                removed.append(j);continue
            if len(set(r['p']))!=len(r['p']):S.error('duplicate weld would create a self-touching polygon')
        rows.append(r)
    welded=len(remap);used={i for r in rows for i in r['p']}
    for i in range(len(v)):
        if i not in used and adjacency[i] and adjacency[i]<=selected:remap.setdefault(i,None)
    return rows,remap,{'welded_duplicate_vertices':welded,'removed_zero_area_polygon_ids':removed,
                       'retained_surface_positions_exact':True,'tolerance_mm':0.}


def _consistent(records,v,selected,strength,sharp):
    from . import owner_surface as S
    edges={}
    for j,r in enumerate(records):
        for a,b in zip(r['p'],r['p'][1:]+r['p'][:1]):edges.setdefault(tuple(sorted((a,b))),[]).append((j,a<b))
    orientation={};components=[]
    for start in sorted(selected):
        if start in orientation:continue
        orientation[start]=False;queue=[start];component=[]
        while queue:
            j=queue.pop();component.append(j);p=records[j]['p']
            for a,b in zip(p,p[1:]+p[:1]):
                users=edges[tuple(sorted((a,b)))]
                if len(users)!=2:S.error('consistent normals requires closed two-face edge incidence')
                other=next((q for q in users if q[0]!=j),None)
                if other is None:S.error('self-adjacent face cannot be oriented')
                k,direction=other
                if k not in selected:S.error('select the complete connected shell to repair its orientation')
                wanted=orientation[j]^((a<b)==direction)
                if k in orientation and orientation[k]!=wanted:S.error('selected shell is not orientable')
                if k not in orientation:orientation[k]=wanted;queue.append(k)
        components.append(component)
    for component in components:
        volume=0.
        for j in component:
            p=records[j]['p'];p=p[::-1] if orientation[j] else p
            for k in range(1,len(p)-1):volume+=float(v[p[0]]@np.cross(v[p[k]],v[p[k+1]]))/6
        if volume==0:S.error('zero-volume shell cannot be oriented outward')
        if volume<0:
            for j in component:orientation[j]=not orientation[j]
    for j,flip in orientation.items():
        if flip:
            for key in ['p','normal']:records[j][key].reverse()
            for uv in records[j]['uv']:uv.reverse()
    normals={j:_cross(v,records[j]['p']) for j in selected}
    if any(np.linalg.norm(n)<=1e-12 for n in normals.values()):S.error('clean zero-area faces before consistent normals')
    # Keep the authored smooth/split field wherever it already points outward.
    # Orientation repair changes only backwards vectors; smoothing remains the
    # dedicated repair_shading operation and never becomes blanket flat shading.
    corrected=0
    for j in selected:
        r=records[j];n=normals[j]/np.linalg.norm(normals[j]);old=np.asarray(r['normal'],float)
        bad=old@n<0;corrected+=int(sum(bad));changed=old.copy()
        for k in np.flatnonzero(bad):
            old[k]/=np.linalg.norm(old[k])
            # Follow the face-normal plane toward the outward hemisphere.
            # Double-cross projection stays perpendicular even near antipodes.
            tangent=np.cross(np.cross(old[k],n),old[k])
            if not np.any(tangent):
                edges=np.roll(v[r['p']],-1,axis=0)-v[r['p']]
                tangents=edges-(edges@old[k])[:,None]*old[k]
                tangent=tangents[int(np.argmax(np.linalg.norm(tangents,axis=1)))]
            tangent/=np.linalg.norm(tangent)
            changed[k]=-old[k] if strength==1 else old[k]*math.cos(math.pi*strength)+tangent*math.sin(math.pi*strength)
        r['normal']=changed.tolist()
    return records,{'reversed_polygon_ids':sorted(j for j,x in orientation.items() if x),
                    'closed_components':len(components),'geometry_positions_unchanged':True,
                    'orientation_is_indivisible':True,'shading_blend_strength':strength,
                    'valid_authored_smooth_and_split_normals_preserved':True,'backward_corner_normals':corrected}


def _decimate(obj,records,v,selected,strength,maximum,operation):
    from . import owner_surface as S
    sharp=_sharp(obj);protected={i for edge in sharp for i in edge}|set(operation['scope'].get('fixed_vertex_ids',[]))
    # Preserve the exact source triangles used to attach markings, even though
    # removing a planar fan would leave its geometric surface unchanged.
    for attachment in operation.get('marking_attachments',[]):
        for binding in attachment.get('bindings',[]):
            protected.update(binding.get('surface_vertex_ids',[]))
    limit=math.ceil(strength*maximum);removed=[];automatic_flat_normals=0
    for vid in range(len(v)):
        if len(removed)>=limit:break
        if vid in protected:continue
        incident=[j for j,r in enumerate(records) if vid in r['p']]
        if len(incident)<3 or any(records[j]['parent'] not in selected or len(records[j]['p'])!=3 for j in incident):continue
        rows=[records[j] for j in incident]
        if len({(r['smooth'],r['material']) for r in rows})!=1:continue
        boundary={};valid=True
        for r in rows:
            p=r['p'];k=p.index(vid);a,b=p[(k+1)%3],p[(k+2)%3]
            if a in boundary:valid=False;break
            boundary[a]=b
        if not valid or set(boundary)!=set(boundary.values()):continue
        ring=[min(boundary)]
        while boundary[ring[-1]]!=ring[0] and len(ring)<=len(boundary):ring.append(boundary[ring[-1]])
        if len(ring)!=len(boundary):continue
        cross=np.array([_cross(v,r['p']) for r in rows]);length=np.linalg.norm(cross,axis=1)
        if np.any(length<=1e-12):continue
        n=cross[0]/length[0]
        # Stored local coordinates avoid transform roundoff. A tilted plane
        # authored in float32 is recognized only within representation ULPs.
        local=np.asarray(obj['vertices_local'],float)
        if not _exact_planar(local[ring+[vid]]):continue
        if np.min(cross@n/length)<0:continue
        turns=[np.dot(np.cross(v[ring[(i+1)%len(ring)]]-v[ring[i]],v[ring[(i+2)%len(ring)]]-v[ring[(i+1)%len(ring)]]),n) for i in range(len(ring))]
        if min(turns)<=1e-10:continue
        automatic=not obj.get('has_custom_normals',False) and not any(r['smooth'] for r in rows)
        values={}
        for r in rows:
            for k,i in enumerate(r['p']):
                # Blender computes flat face normals in float32 local space;
                # a translated planar fan can differ by a few ULPs. Automatic
                # flat normals are defined by the actual plane, not authored
                # shading data. Recompute that plane's normal on the new face.
                local_normal=n@np.asarray(obj.get('matrix_world',np.eye(4)))[:3,:3]
                local_normal/=np.linalg.norm(local_normal)
                value=(local_normal.tolist() if automatic else r['normal'][k],[uv[k] for uv in r['uv']])
                if i in values and values[i]!=value:valid=False;break
                values[i]=value
        if not valid:continue
        # Removing an interior vertex must also preserve its affine UV and
        # shading fields. A nonconstant normal field is conservatively protected.
        if any(values[i][0]!=values[vid][0] for i in ring):continue
        for layer in range(len(obj.get('uv_layers',[]))):
            ids=ring+[vid];uv=np.asarray([values[i][1][layer] for i in ids])
            localnormal=_cross(local,ring);localnormal/=np.linalg.norm(localnormal)
            if not _exact_affine(local[ids],uv,localnormal):valid=False;break
        if not valid:continue
        replacement={**rows[0],'p':ring,'normal':[values[i][0] for i in ring],
                     'uv':[[values[i][1][layer] for i in ring] for layer in range(len(obj.get('uv_layers',[])))]}
        first=incident[0];records=[replacement if j==first else r for j,r in enumerate(records) if j==first or j not in incident]
        removed.append(vid)
        automatic_flat_normals+=int(automatic)
    return records,{'removed_interior_vertex_ids':removed,'requested_maximum':maximum,
                    'surface_positions_unchanged':True,'silhouette_sharp_edges_material_uv_and_markings_preserved':True,
                    'automatic_flat_faces_recomputed':automatic_flat_normals,
                    'method':'planar convex fans within float32 coordinate representation error; curved or protected areas remain unchanged'}


def _exact_planar(points):
    from .owner_surface import representation_tolerance
    centered=np.asarray(points,float)-np.mean(points,axis=0)
    _,singular,axes=np.linalg.svd(centered,full_matrices=False)
    tolerance=representation_tolerance(points)
    return singular[1]>tolerance and np.max(abs(centered@axes[-1]))<=tolerance


def _exact_affine(points,values,normal):
    dims=[i for i in range(3) if i!=int(np.argmax(abs(normal)))];p=[[Fraction(float(row[i])) for i in dims] for row in points]
    val=[[Fraction(float(x)) for x in row] for row in values]
    u=[p[1][i]-p[0][i] for i in range(2)];w=[p[2][i]-p[0][i] for i in range(2)];den=u[0]*w[1]-u[1]*w[0]
    if den==0:return False
    for k in range(len(val[0])):
        a,b=val[1][k]-val[0][k],val[2][k]-val[0][k];x=(a*w[1]-b*u[1])/den;y=(u[0]*b-w[0]*a)/den
        if any(val[j][k]-val[0][k]!=x*(p[j][0]-p[0][0])+y*(p[j][1]-p[0][1]) for j in range(3,len(p))):return False
    return True


def plan_object(obj,operation):
    from . import owner_surface as S,owner_mesh as M
    kind=operation['kind'];params=operation['params'];required={'strength'}
    if kind=='symmetrize':required|={'pairs'}
    if kind=='controlled_decimate':required|={'max_removed_vertices'}
    strength=S.amount(params,required)
    if kind not in OPERATION_KINDS:S.error('unknown mesh health operation')
    ev=obj.get('evaluated',{});users=obj.get('shared_mesh_users',1)
    if not ev.get('topology_matches_source') or not ev.get('positions_match_source') or (len(users) if isinstance(users,list) else users)!=1 or obj.get('unsupported_edit_state'):
        S.error('mesh health needs a unique editable source without unsupported state')
    _attributes(obj);selected=set(S.selected(obj,operation));v=np.asarray(obj['vertices_world_mm'],float).copy();rows=_records(obj);remap=None
    if kind=='mesh_cleanup':
        support={r['vertex_id'] for r in operation['scope']['vertex_weights'] if r['weight']==1}
        rows,remap,report=_cleanup(obj,rows,v,selected,strength,support)
    elif kind=='consistent_normals':
        # Both winding and corner vectors are authored in local space. Avoid
        # world reflections and leave each already-correct vector bit-exact.
        rows,report=_consistent(rows,np.asarray(obj['vertices_local'],float),selected,strength,_sharp(obj))
    elif kind=='controlled_decimate':
        maximum=params['max_removed_vertices']
        if type(maximum) is not int or not 1<=maximum<=10000:S.error('max_removed_vertices must be an integer from1 through10000')
        rows,report=_decimate(obj,rows,v,selected,strength,maximum,operation)
        remap={i:None for i in report['removed_interior_vertex_ids']}
    else:
        pairs=params['pairs'];support={i for j in selected for i in obj['polygons'][j]};seen=set()
        if not isinstance(pairs,list) or not pairs:S.error('symmetry requires explicit source/destination vertex pairs')
        for pair in pairs:
            if not isinstance(pair,list) or len(pair)!=2 or any(type(i) is not int or i not in support for i in pair) or pair[0]==pair[1]:S.error('each symmetry pair must be two distinct selected vertex IDs')
            a,b=pair
            if a in seen or b in seen:S.error('symmetry pairs must be disjoint')
            if v[a,0]*v[b,0]>=0:S.error('symmetry pairs must span asset X=0')
            seen.update(pair);target=v[a]*[-1,1,1];v[b]+=strength*(target-v[b])
        result=_rebuild(obj,rows,v)
        result['corner_normals']=S.transport_normals(obj,result['vertices_local'])
        report={'pairs':pairs,'plane':'asset X=0','maximum_move_mm':float(np.linalg.norm(v-np.asarray(obj['vertices_world_mm']),axis=1).max()),
                'minimum_thickness_preservation':S.preserve_minimum_thickness(obj,result,sorted(selected))}
        return result,{**report,'strength':strength,'selected_polygon_ids':sorted(selected),'physical':M.physical(result,reference=obj)}
    result=_rebuild(obj,rows,v,remap)
    constraints=M.temple_constraints(obj)
    if remap is not None and constraints is not None:
        # Geometry-preserving topology changes still re-run the declared graph
        # minimum; dropping stale aliases must never silently drop this check.
        minimum=constraints['options']['t_min_mm']
        triangles=np.asarray(result['triangles'],int)
        if S.orientation_sign(result)<0:triangles=triangles[:,::-1]
        report['declared_thickness']=M.graph_thickness(np.asarray(result['vertices_world_mm']),triangles,obj['side'],minimum)
    return result,{**report,'strength':strength,'selected_polygon_ids':sorted(selected),'physical':M.physical(result,reference=obj)}
