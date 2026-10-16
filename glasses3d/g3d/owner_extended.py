"""Additional finite owner operations; all amounts are shown in isolated previews."""
import copy
import math
import re
import numpy as np

OPERATION_KINDS=frozenset({'material_adjust','edge_shape','thickness','scale_region','bend_region','transform_marking'})
MATERIAL_SOCKETS={'base_color':'Base Color','roughness':'Roughness','metallic':'Metallic',
                  'transmission':'Transmission Weight','coat_weight':'Coat Weight',
                  'coat_roughness':'Coat Roughness','ior':'IOR','alpha':'Alpha'}


def plan_object(obj,operation):
    from . import owner_surface as S,owner_mesh as M
    kind,params=operation['kind'],operation['params']
    if kind=='edge_shape':
        from . import owner_edge_radius
        return owner_edge_radius.plan(obj,operation)
    if kind!='material_adjust':return deform(obj,operation)
    if set(params)!={'strength','material_index','values'}:S.error('material_adjust takes strength, material_index, values')
    strength=S.amount({'strength':params['strength']},('strength',))
    index=params['material_index'];definitions=obj.get('material_definitions')
    if type(index) is not int or definitions is None or not 0<=index<len(definitions) or definitions[index] is None:
        S.error('select an existing captured material slot')
    definition=definitions[index]
    if obj.get('material_slot_links',[None]*len(definitions))[index]!='DATA':
        S.error('material adjustment requires a DATA-linked material slot; object overrides need explicit rebinding')
    values=params['values']
    if not isinstance(values,dict) or not values or set(values)-set(MATERIAL_SOCKETS):S.error('unsupported material parameters')
    if not definition.get('principled_node'):S.error('material adjustment needs exactly one Principled shader')
    if set(values)&set(definition['linked_parameters']):S.error('a requested material input is linked; a dedicated texture operation is needed')
    selected=set(S.selected(obj,operation))
    material_faces={i for i,value in enumerate(obj['polygon_material']) if value==index}
    if not material_faces<=selected:S.error('show every face using the selected material on this object')
    result=copy.deepcopy(obj);after=result['material_definitions'][index]
    if not isinstance(operation['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',operation['id']):S.error('material operation id must be safe letters, digits, underscore or hyphen')
    after['name']=definition['name']+'.owner-'+operation['id']
    result['material_names'][index]=after['name']
    for key,target in values.items():
        original=np.asarray(definition['parameters'][key],float);target=np.asarray(target,float)
        if target.shape!=original.shape or not np.isfinite(target).all():S.error('material value has the wrong shape or is nonfinite')
        lo,hi=(1.,4.) if key=='ior' else (0.,1.)
        if np.any(target<lo) or np.any(target>hi):S.error('material value is outside its physical parameter range')
        value=(original+strength*(target-original)).astype(np.float32)
        after['parameters'][key]=value.tolist()
    edits=result.setdefault('_owner_material_edits',[])
    existing=next((e for e in edits if e['index']==index),None)
    if existing:existing['name']=after['name']
    else:edits.append({'index':index,'original_name':definition['name'],'name':after['name']})
    return result,{'selected_polygon_ids':sorted(selected),'strength':strength,'geometry_unchanged':True,
                   'material_before':definition,'material_after':after,'isolated_material_copy':True,
                   'physical':M.physical(obj)}


def _vector(value,name,unit=False):
    from . import owner_surface as S
    v=np.asarray(value,float)
    if v.shape!=(3,) or not np.isfinite(v).all():S.error(name+' must be three finite numbers')
    if unit:
        length=np.linalg.norm(v)
        if length<1e-10:S.error(name+' must be nonzero')
        v=v/length
    return v


def _finish(obj,world,operation,faces,report):
    from . import owner_surface as S,owner_mesh as M
    result=copy.deepcopy(obj);before=np.asarray(obj['vertices_world_mm'],float)
    matrix=np.asarray(obj.get('matrix_world',np.eye(4)),float)
    origin=before[0]-np.asarray(obj['vertices_local'][0])@matrix[:3,:3].T-matrix[:3,3]
    local=((world-origin-matrix[:3,3])@np.linalg.inv(matrix[:3,:3]).T).astype(np.float32).astype(float)
    same=np.all(world==before,axis=1);local[same]=np.asarray(obj['vertices_local'])[same]
    world=local@matrix[:3,:3].T+matrix[:3,3]+origin;world[same]=before[same]
    result.update(vertices_local=local.tolist(),vertices_world_mm=world.tolist(),corner_normals=S.transport_normals(obj,local))
    report['physical']=M.physical(result,reference=obj)
    generic=not(obj.get('role')=='temple' and obj.get('aliases',{}).get('status')=='verified_topology')
    thinning=operation['kind']=='thickness' and operation['params']['offset_mm']<0
    minimum=operation['params'].get('minimum_mm')
    if generic and thinning and minimum is None:S.error('generic thinning requires a positive minimum_mm in the confirmed preview')
    report['minimum_thickness']=S.preserve_minimum_thickness(obj,result,faces,preserve_original=not(generic and thinning),minimum_mm=minimum)
    report['maximum_move_mm']=float(np.linalg.norm(world-before,axis=1).max())
    return result,report


def deform(obj,operation):
    from . import owner_surface as S,owner_mesh as M
    kind,params=operation['kind'],operation['params']
    required={'thickness':('strength','offset_mm'),
              'scale_region':('strength','factors','anchor_mm'),
              'bend_region':('strength','axis','direction','anchor_mm','angle_deg','length_mm')}
    if kind not in required:S.error('unknown deformation')
    if kind=='thickness' and 'minimum_mm' in params:required[kind]=('strength','offset_mm','minimum_mm')
    strength=S.amount(params,required[kind]);weights,_=M.scope_vertices(operation['scope'],obj)
    fixed=set(operation['scope'].get('fixed_vertex_ids',[]))
    before=np.asarray(obj['vertices_world_mm'],float);world=before.copy()
    ids=sorted(i for i,w in weights.items() if w>0 and i not in fixed)
    if not ids:S.error('select moving vertices')
    support={i for i,w in weights.items() if w>0}
    faces=[i for i,poly in enumerate(obj['polygons']) if set(poly)<=support]
    if not faces:S.error('deformation needs complete selected faces, with positive falloff weights')
    w=np.asarray([weights[i] for i in ids])*strength
    report={'selected_polygon_ids':faces,'strength':strength,'topology_unchanged':True}
    if kind=='thickness':
        offset=params['offset_mm']
        if isinstance(offset,bool) or not isinstance(offset,(float,int)) or not math.isfinite(offset):S.error('offset_mm must be finite')
        normals=np.zeros_like(world)
        for f in faces:
            p=obj['polygons'][f];points=before[p]
            n=np.sum(np.cross(points,np.roll(points,-1,axis=0)),axis=0)
            normals[p]+=n
        lengths=np.linalg.norm(normals[ids],axis=1)
        if np.any(lengths<1e-10):S.error('selected normal offset has an undefined vertex normal')
        world[ids]+=w[:,None]*float(offset)*normals[ids]/lengths[:,None]
        report.update(offset_mm=offset*strength,normal_policy='area-weighted selected faces; opposite unselected vertices fixed')
    elif kind=='scale_region':
        anchor=_vector(params['anchor_mm'],'anchor_mm');factors=_vector(params['factors'],'factors')
        if np.any(factors<=0):S.error('scale factors must be strictly positive')
        world[ids]=anchor+(before[ids]-anchor)*(1+w[:,None]*(factors-1))
        report.update(anchor_mm=anchor.tolist(),factors=factors.tolist())
    else:
        axis=_vector(params['axis'],'axis',True);direction=_vector(params['direction'],'direction',True)
        if abs(axis@direction)>1e-8:S.error('bend direction must be perpendicular to its axis')
        anchor=_vector(params['anchor_mm'],'anchor_mm');angle=params['angle_deg'];length=params['length_mm']
        if isinstance(angle,bool) or not isinstance(angle,(int,float)) or not math.isfinite(angle) or abs(angle)>=90:S.error('bend angle must be finite and strictly between -90 and 90 degrees')
        if isinstance(length,bool) or not isinstance(length,(int,float)) or not math.isfinite(length) or length<=0:S.error('bend length_mm must be positive')
        distance=(before[ids]-anchor)@direction
        falloff=np.clip(distance/length,0,1)**2
        angles=np.deg2rad(angle)*w*falloff
        rel=before[ids]-anchor;c=np.cos(angles)[:,None];s=np.sin(angles)[:,None]
        world[ids]=anchor+rel*c+np.cross(axis,rel)*s+(rel@axis)[:,None]*axis*(1-c)
        report.update(angle_deg=angle*strength,falloff='quadratic along positive direction, clamped after length_mm',anchor_mm=anchor.tolist())
    return _finish(obj,world,operation,faces,report)


def transform_marking(obj,surface,operation):
    from . import owner_surface as S,owner_mesh as M,owner_markings as marks,owner_thickness
    p=operation['params'];strength=S.amount(p,('strength','surface_object_id','anchor_mm','u_axis','normal','translate_uv_mm','scale','angle_deg'))
    weights,_=M.scope_vertices(operation['scope'],obj);fixed=operation['scope'].get('fixed_vertex_ids',[])
    if fixed or set(i for i,w in weights.items() if w==1)!=set(range(len(obj['vertices_local']))):S.error('marking transform requires the complete marking with unit weights and no fixed vertices')
    if not operation.get('marking_attachments') or operation['marking_attachments'][0]['object_id']!=obj['id']:S.error('marking transform requires a sealed supporting surface')
    anchor=_vector(p['anchor_mm'],'anchor_mm');u=_vector(p['u_axis'],'u_axis',True);normal=_vector(p['normal'],'normal',True)
    if abs(u@normal)>1e-8:S.error('marking u_axis must be perpendicular to normal')
    v=np.cross(normal,u);basis=np.array([u,v,normal])
    move=np.asarray(p['translate_uv_mm'],float);scale=np.asarray(p['scale'],float);angle=p['angle_deg']
    if move.shape!=(2,) or scale.shape!=(2,) or not np.isfinite(move).all() or not np.isfinite(scale).all() or np.any(scale<=0):S.error('marking translation and positive scale need two finite numbers')
    if isinstance(angle,bool) or not isinstance(angle,(int,float)) or not math.isfinite(angle):S.error('marking angle must be finite')
    before=np.asarray(obj['vertices_world_mm'],float);local=(before-anchor)@basis.T
    xy=local[:,:2]*(1+strength*(scale-1));angle=math.radians(angle*strength)
    rotation=np.array([[math.cos(angle),-math.sin(angle)],[math.sin(angle),math.cos(angle)]])
    local[:,:2]=xy@rotation.T+strength*move
    virtual=local@basis+anchor
    points=np.asarray(surface['vertices_world_mm'],float);tri=surface['triangles']
    old=marks.nearest(before,points,tri);found=marks.nearest(virtual,points,tri);world=[]
    for candidate,prior,row in zip(virtual,old,found):
        triangle=points[tri[row[0]]];n=np.cross(triangle[1]-triangle[0],triangle[2]-triangle[0]);n/=np.linalg.norm(n)
        if n@normal<=0:S.error('marking projection reached the opposite surface')
        foot=np.asarray(row[1])@triangle
        # Projection must remain inside the supporting patch, never clamp a moved
        # mark onto a boundary or switch to an opposite side.
        if np.linalg.norm((candidate-foot)-((candidate-foot)@n)*n)>1e-5:S.error('marking transform leaves the supporting surface')
        world.append(foot+prior[3]*n)
    world=np.asarray(world);gap=owner_thickness.marking_gap(world,obj['triangles'],points,tri,normal)
    lift=0.
    if gap<0:
        baseline=owner_thickness.marking_gap(before,obj['triangles'],points,tri,normal)
        lift=float(max(baseline,0)-gap)+float(abs(np.spacing(np.float32(np.max(abs(world))))))*4
        world+=lift*normal
    result=copy.deepcopy(obj);matrix=np.asarray(obj.get('matrix_world',np.eye(4)),float)
    origin=before[0]-np.asarray(obj['vertices_local'][0])@matrix[:3,:3].T-matrix[:3,3]
    coordinates=((world-origin-matrix[:3,3])@np.linalg.inv(matrix[:3,:3]).T).astype(np.float32).astype(float)
    result.update(vertices_local=coordinates.tolist(),vertices_world_mm=(coordinates@matrix[:3,:3].T+matrix[:3,3]+origin).tolist(),corner_normals=S.transport_normals(obj,coordinates))
    checked=marks.validate_marking(result,before,np.asarray(result['vertices_world_mm']))
    return result,{'strength':strength,'supporting_surface_id':surface['id'],'surface_unchanged':True,'outward_clearance_lift_mm':lift,'physical':checked}
