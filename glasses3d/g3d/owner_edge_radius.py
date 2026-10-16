"""Circular fillets of a selected straight convex edge; exact saved-source sharpening.

Supported endpoints have three incident planar convex faces. Complex corners,
material boundaries and non-affine UVs refuse instead of changing adjoining faces.
"""
import copy
import math
import numpy as np

PROPERTY='g3d_owner_edge_recipe'


def _normal(v,p):
    from . import owner_surface as S
    n=S.face_vectors(v,[p])[0]
    if np.max(abs((v[p]-v[p[0]])@n))>1e-7:S.error('radius fillet requires planar adjoining faces and end caps')
    turns=[np.cross(v[p[(i+1)%len(p)]]-v[p[i]],v[p[(i+2)%len(p)]]-v[p[(i+1)%len(p)]])@n for i in range(len(p))]
    if min(turns)<-1e-10:S.error('radius fillet requires convex adjoining faces and end caps')
    return n


def _inside(point,v,p,n):
    return all(np.cross(v[b]-v[a],point-v[a])@n>=-1e-8 for a,b in zip(p,p[1:]+p[:1]))


def _geometry(base,a,b,radius,segments):
    from . import owner_surface as S,owner_health as H
    v=np.asarray(base['vertices_world_mm'],float);polys=base['polygons'];records=H._records(base)
    incident=[i for i,p in enumerate(polys) if a in p or b in p]
    edges=[i for i,p in enumerate(polys) if any({x,y}=={a,b} for x,y in zip(p,p[1:]+p[:1]))]
    if len(edges)!=2:S.error('choose one closed-manifold edge')
    f1,f2=edges
    if polys[f1][(polys[f1].index(a)+1)%len(polys[f1])]!=b:a,b=b,a
    caps=[]
    for vertex in (a,b):
        faces=[i for i,p in enumerate(polys) if vertex in p]
        cap=set(faces)-set(edges)
        if len(faces)!=3 or len(cap)!=1:S.error('fillet endpoints need exactly three incident faces')
        caps.append(cap.pop())
    if caps[0]==caps[1]:S.error('fillet needs distinct end caps')
    normals={i:_normal(v,polys[i]) for i in incident};n1,n2=normals[f1],normals[f2]
    axis=v[b]-v[a];axis/=np.linalg.norm(axis);dot=float(n1@n2)
    if not -1+1e-8<dot<1-1e-8:S.error('selected edge is flat or reversed')
    angle=math.acos(dot)
    shift=-radius*(n1+n2)/(1+dot)
    # The cylinder centre must lie on the inward side of both adjoining faces.
    for f,other in ((f1,f2),(f2,f1)):
        if max((v[polys[other]]-v[a])@normals[f])>1e-7:S.error('radius fillet requires a convex edge')
    output=v.tolist();rings=[];parent=list(range(len(v)))
    for endpoint,cap in zip((a,b),caps):
        capnormal=normals[cap];den=float(axis@capnormal)
        if abs(den)<1e-8:S.error('end cap is parallel to the selected edge')
        ring=[]
        for j in range(segments+1):
            t=j/segments;n=(math.sin((1-t)*angle)*n1+math.sin(t*angle)*n2)/math.sin(angle)
            offset=shift+radius*n;point=v[endpoint]+offset-axis*(offset@capnormal)/den
            if not _inside(point,v,polys[cap],capnormal):S.error('requested radius leaves the selected end cap')
            if j==0:
                vid=endpoint;output[vid]=point.tolist()
            else:
                vid=len(output);output.append(point.tolist());parent.append(endpoint)
            ring.append(vid)
        if not _inside(np.asarray(output[ring[0]]),v,polys[f1],n1) or not _inside(np.asarray(output[ring[-1]]),v,polys[f2],n2):S.error('requested radius exceeds the adjoining face')
        rings.append(ring)
    world=np.asarray(output);new=[];sharp=set();oldsharp=H._sharp(base)
    for i,record in enumerate(records):
        if i not in incident:new.append(record);continue
        p=record['p'];replacement=[]
        for vertex in p:
            if vertex not in (a,b):replacement.append(vertex);continue
            ring=rings[0 if vertex==a else 1]
            if i==f1:replacement.append(ring[0])
            elif i==f2:replacement.append(ring[-1])
            else:
                prior=p[(p.index(vertex)-1)%len(p)]
                face=polys[f1];k=face.index(vertex);other=b if vertex==a else a
                neighbour=face[(k-1)%len(face)] if face[(k+1)%len(face)]==other else face[(k+1)%len(face)]
                replacement.extend(ring if prior==neighbour else ring[::-1])
        uv=[]
        for values in record['uv']:
            design=np.c_[np.ones(len(p)),v[p]];values=np.asarray(values);coef=np.linalg.lstsq(design,values,rcond=None)[0]
            if np.max(abs(design@coef-values))>1e-7:S.error('radius fillet requires affine UVs on its adjoining faces')
            uv.append((np.c_[np.ones(len(replacement)),world[replacement]]@coef).tolist())
        # Adjoining faces remain on their exact original planes; their flat normal
        # follows that plane. The cylindrical strip has explicit radial normals.
        matrix=np.asarray(base.get('matrix_world',np.eye(4)));localnormal=normals[i]@matrix[:3,:3];localnormal/=np.linalg.norm(localnormal)
        new.append({**record,'p':replacement,'normal':[localnormal.tolist()]*len(replacement),'uv':uv})
        for x,y in zip(p,p[1:]+p[:1]):
            if tuple(sorted((x,y))) not in oldsharp or {x,y}=={a,b}:continue
            def replacement_end(x,other):
                if x not in (a,b):return x
                ring=rings[0 if x==a else 1]
                if i==f1:return ring[0]
                if i==f2:return ring[-1]
                pos=replacement.index(other);candidate=[replacement[(pos-1)%len(replacement)],replacement[(pos+1)%len(replacement)]]
                return next(k for k in candidate if k in ring)
            sharp.add(tuple(sorted((replacement_end(x,y),replacement_end(y,x)))))
    if base['polygon_material'][f1]!=base['polygon_material'][f2]:S.error('radius fillet cannot cross a material boundary')
    for j in range(segments):
        poly=[rings[1][j],rings[0][j],rings[0][j+1],rings[1][j+1]]
        radial=[]
        for t in (j/segments,j/segments,(j+1)/segments,(j+1)/segments):
            n=(math.sin((1-t)*angle)*n1+math.sin(t*angle)*n2)/math.sin(angle);n=n@matrix[:3,:3];n/=np.linalg.norm(n);radial.append(n.tolist())
        uv=[]
        for layer in range(len(base.get('uv_layers',[]))):
            coords=[]
            for endpoint,t in ((b,j/segments),(a,j/segments),(a,(j+1)/segments),(b,(j+1)/segments)):
                left=np.asarray(records[f1]['uv'][layer][polys[f1].index(endpoint)]);right=np.asarray(records[f2]['uv'][layer][polys[f2].index(endpoint)])
                if np.max(abs(left-right))>1e-7:S.error('radius fillet cannot cross an existing UV seam')
                coords.append(((1-t)*left+t*right).tolist())
            uv.append(coords)
        new.append({'p':poly,'parent':f1,'normal':radial,'uv':uv,'smooth':True,'material':base['polygon_material'][f1]})
        for ring in rings:sharp.add(tuple(sorted((ring[j],ring[j+1]))))
    # Preserve every unrelated sharp edge exactly.
    sharp.update(e for e in oldsharp if a not in e and b not in e)
    result=copy.deepcopy(base);matrix=np.asarray(base.get('matrix_world',np.eye(4)));origin=v[0]-np.asarray(base['vertices_local'][0])@matrix[:3,:3].T-matrix[:3,3]
    local=((world-origin-matrix[:3,3])@np.linalg.inv(matrix[:3,:3]).T).astype(np.float32).astype(float)
    for i in range(len(v)):
        if np.array_equal(world[i],v[i]):local[i]=base['vertices_local'][i]
    result.update(vertices_local=local.tolist(),vertices_world_mm=(local@matrix[:3,:3].T+matrix[:3,3]+origin).tolist(),
        polygons=[r['p'] for r in new],corner_normals=[n for r in new for n in r['normal']],
        polygon_smooth=[r['smooth'] for r in new],polygon_material=[r['material'] for r in new],
        uv_layers=[{**layer,'uv':[uv for r in new for uv in r['uv'][j]]} for j,layer in enumerate(base.get('uv_layers',[]))],
        _surface_polygon_parents=[r['parent'] for r in new],_surface_vertex_parents=parent,
        _surface_sharp_edges=[list(e) for e in sorted(sharp)])
    result['triangles']=[[p[0],p[k],p[k+1]] for p in result['polygons'] for k in range(1,len(p)-1)]
    result['triangle_polygons']=[i for i,p in enumerate(result['polygons']) for _ in range(len(p)-2)]
    return result,incident


def plan(obj,operation):
    from . import owner_surface as S,owner_mesh as M,owner_health as H
    params=operation['params'];mode=params.get('mode')
    required={'strength','mode','radius_mm'}|({'edge_vertex_ids','segments'} if mode=='round' else set())
    if 'minimum_mm' in params:required.add('minimum_mm')
    strength=S.amount(params,required)
    if mode not in ('round','sharpen'):S.error('edge mode must be round or sharpen')
    radius=params['radius_mm']
    if isinstance(radius,bool) or not isinstance(radius,(float,int)) or not math.isfinite(radius) or radius<0:S.error('radius_mm must be finite and nonnegative')
    H._attributes(obj);selected=set(S.selected(obj,operation));recipe=obj.get('edge_radius_recipe')
    if mode=='round':
        if recipe:S.error('this mesh already has an authored fillet; use sharpen or a fresh source copy')
        edge=params['edge_vertex_ids'];segments=params['segments']
        if not isinstance(edge,list) or len(edge)!=2 or any(type(i) is not int or not 0<=i<len(obj['vertices_local']) for i in edge) or edge[0]==edge[1]:S.error('edge_vertex_ids must name two distinct source vertices')
        if type(segments) is not int or not 2<=segments<=16:S.error('fillet segments must be an integer from2 through16')
        if radius<=0:S.error('rounding needs a positive radius')
        base=copy.deepcopy(obj);base.pop('edge_radius_recipe',None)
        for key in list(base):
            if key.startswith(('_surface_','_owner_')):base.pop(key)
        source_affected=list(operation['scope']['affected_polygon_ids'])
        current=0.;actual=radius*strength
    else:
        if not isinstance(recipe,dict) or recipe.get('schema')!='g3d.edge_radius/1':S.error('sharpen supports an authored circular fillet with its saved source recipe')
        base=copy.deepcopy(recipe['source']);edge=recipe['edge_vertex_ids'];segments=recipe['segments'];current=recipe['radius_mm']
        if radius>=current:S.error('sharpen radius must be smaller than the current authored radius')
        reproduced,reproduced_incident=_geometry(base,*edge,current,segments)
        source_affected=recipe.get('source_affected_polygon_ids',reproduced_incident)
        # Reproduce the final planned normal fans as well as the geometric
        # fillet. These flags are part of the sealed recipe, not an excuse to
        # ignore later changes on an unrelated component.
        from . import owner_normal_plan
        owner_normal_plan.plan({'schema':'g3d.mesh_snapshot/1','objects':[base]},
            {'schema':'g3d.mesh_snapshot/1','objects':[reproduced]},[
            {'scope':{'object_id':base['id'],'affected_polygon_ids':source_affected}}])
        if (any(obj[k]!=reproduced[k] for k in ('vertices_local','polygons','polygon_material','polygon_smooth')) or
                obj.get('matrix_world')!=base.get('matrix_world') or len(obj['uv_layers'])!=len(reproduced['uv_layers']) or any(
                {k:v for k,v in a.items() if k!='uv'}!={k:v for k,v in b.items() if k!='uv'} or
                not np.array_equal(np.asarray(a['uv'],np.float32),np.asarray(b['uv'],np.float32)) for a,b in zip(obj['uv_layers'],reproduced['uv_layers']))):
            S.error('authored fillet geometry, smoothing or UVs changed; saved source cannot safely restore it')
        witness=obj.get('normal_intent');intended=witness['intent_normals'] if witness else obj['corner_normals']
        if (witness and not (witness.get('encoded_matches') and witness.get('decoded_within_quantization') and witness.get('normal_space_matches')) or
                not np.array_equal(np.asarray(intended,np.float32),np.asarray(reproduced['corner_normals'],np.float32)) or
                H._sharp(obj)!={tuple(e) for e in reproduced['_surface_sharp_edges']}):
            S.error('authored fillet shading or sharp edges changed; saved source cannot safely restore it')
        actual=current+strength*(radius-current)
    if actual==0:
        result=copy.deepcopy(base);incident=[i for i,p in enumerate(base['polygons']) if set(edge)&set(p)]
        result['_surface_polygon_parents']=list(range(len(base['polygons'])))
        result['_surface_vertex_parents']=list(range(len(base['vertices_local'])))
        result['_surface_vertex_remap']=[i if i<len(base['vertices_local']) else None for i in range(len(obj['vertices_local']))]
        result['_surface_sharp_edges']=[list(e) for e in sorted(H._sharp(base))]
        result['edge_radius_recipe']=None
    else:
        result,incident=_geometry(base,*edge,actual,segments)
        result['edge_radius_recipe']={'schema':'g3d.edge_radius/1','source':base,'edge_vertex_ids':edge,'segments':segments,'radius_mm':actual,
                                     'source_affected_polygon_ids':source_affected}
    required_faces=set(incident)|set(range(len(base['polygons']),len(obj['polygons'])))
    if not required_faces<=selected:S.error('show all adjoining faces, both end caps and the complete authored fillet')
    if mode=='sharpen' and actual:
        result['_surface_polygon_parents']=list(range(len(result['polygons'])))
        result['_surface_vertex_parents']=list(range(len(result['vertices_local'])))
    # Materials and object metadata belong to the live source; only geometry is rebuilt.
    for key in ('material_names','material_definitions','material_sha256','object_state_sha256','properties_sha256','mesh_properties_sha256','mesh_properties'):
        if key in obj:result[key]=copy.deepcopy(obj[key])
    if '_owner_material_edits' in obj:result['_owner_material_edits']=copy.deepcopy(obj['_owner_material_edits'])
    props=copy.deepcopy(obj.get('mesh_properties',{}))
    import json
    if result['edge_radius_recipe'] is None:props.pop(PROPERTY,None)
    else:props[PROPERTY]=json.dumps(result['edge_radius_recipe'],sort_keys=True,separators=(',',':'))
    result['mesh_properties']=props
    edges=sorted({tuple(sorted((a,b))) for p in result['polygons'] for a,b in zip(p,p[1:]+p[:1])})
    result['edges']=[list(e) for e in edges]
    wanted={tuple(e) for e in result['_surface_sharp_edges']}
    result['attributes']=[a for a in result.get('attributes',[]) if a['name']!='sharp_edge']
    result['attributes'].append({'name':'sharp_edge','domain':'EDGE','data_type':'BOOLEAN','values':[[e in wanted] for e in edges]})
    physical=M.physical(result,reference=obj)
    minimum=params.get('minimum_mm');generic=not(obj.get('role')=='temple' and obj.get('aliases',{}).get('status')=='verified_topology')
    if generic and minimum is None:S.error('generic radius edits need an explicit positive minimum_mm in the confirmed preview')
    # The authored source fixes the measurement chart for every later radius;
    # changed tessellation must not rotate a PCA chart and alter the floor's meaning.
    clearance=S.preserve_minimum_thickness(base,result,sorted(i for i in selected if i<len(base['polygons'])),preserve_original=not generic,minimum_mm=minimum)
    return result,{'strength':strength,'mode':mode,'radius_before_mm':current,'radius_after_mm':actual,'segments':segments,
        'adjoining_face_planes_preserved':True,'selected_polygon_ids':sorted(selected),'physical':physical,'minimum_thickness':clearance,
        'source_recipe_saved':actual>0,'restored_original_sharp_corner':actual==0}
