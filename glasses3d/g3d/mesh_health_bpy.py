"""Main-run in-memory mesh-health facade using the owner lane's exact planners.

The main build phase already authorizes its scene construction. Delivered-model
owner edits use the labelled, previewed, confirmed owner tools instead. These
helpers write only mesh datablocks and roll back all touched objects on failure;
they never load/save files, start an agent or call a provider.
"""
from __future__ import annotations
import copy
import bpy
import numpy as np
from . import model_map_bpy as MAP,owner_mesh as M,owner_surface as S,owner_health as H
from . import owner_markings as MARK,owner_mesh_bpy as APPLY,export_glb as EX,owner_normals_bpy


def _capture():
    origin=np.asarray(bpy.context.scene.get(EX.ORIGIN_KEY,[0,0,0]),float);rows=[];unsupported=[]
    depsgraph=bpy.context.evaluated_depsgraph_get()
    for obj in sorted((o for o in bpy.context.scene.objects if o.type=='MESH' and not o.hide_render),key=lambda o:o.name):
        me=obj.data;raw=MAP._mesh(me,obj.matrix_world,origin);ev=obj.evaluated_get(depsgraph);em=ev.to_mesh(preserve_all_data_layers=True,depsgraph=depsgraph)
        try:evaluated=MAP._mesh(em,ev.matrix_world,origin)
        finally:ev.to_mesh_clear()
        same=raw['polygons']==evaluated['polygons'] and len(raw['vertices_local'])==len(evaluated['vertices_local'])
        unsupported_state=[]
        if me.shape_keys is not None:unsupported_state.append('shape keys need a dedicated operation')
        _,unknown=MAP._other_attributes(me);unsupported_state+=unknown
        rows.append({'id':f'O{len(rows)+1:04d}','name':obj.name,'mesh_name':me.name,'parent_name':obj.parent.name if obj.parent else None,
            'role':str(obj.get('partRole',me.get('partRole',''))),'side':1 if np.mean(np.asarray(raw['vertices_world_mm'])[:,0])>0 else -1,
            'matrix_world':[list(r) for r in obj.matrix_world],**raw,'polygon_smooth':[p.use_smooth for p in me.polygons],
            'polygon_material':[p.material_index for p in me.polygons],'corner_normals':[list(n.vector) for n in me.corner_normals],
            'has_custom_normals':me.has_custom_normals,'attributes':MAP._attributes(me),'normal_attributes':MAP._normal_attributes(me),
            'normal_intent':owner_normals_bpy.capture(me),
            'uv_layers':[{'name':u.name,'uv':[list(v.uv) for v in u.data],'active_render':u.active_render,'active_clone':u.active_clone} for u in me.uv_layers],
            'uv_active_index':me.uv_layers.active_index,'shared_mesh_users':[o.name for o in bpy.data.objects if o.data==me],
            'evaluated':{'topology_matches_source':same,'positions_match_source':same and bool(np.allclose(raw['vertices_world_mm'],evaluated['vertices_world_mm'],rtol=0,atol=1e-7))},
            'aliases':MAP._aliases(obj,raw),'unsupported_edit_state':unsupported_state,
            'topology_sha256':M.digest(raw['polygons']),'positions_sha256':M.digest(raw['vertices_local'])})
    for obj in bpy.context.scene.objects:
        if obj.hide_render or obj.type not in {'FONT','CURVE','SURFACE','META','CURVES','VOLUME','GREASEPENCIL'}:continue
        matrix=np.asarray(obj.matrix_world);p=np.asarray(obj.bound_box)@matrix[:3,:3].T+matrix[:3,3]-origin
        unsupported.append({'name':obj.name,'type':obj.type,'parent_name':obj.parent.name if obj.parent else None,'bbox_world_mm':[p.min(0).tolist(),p.max(0).tolist()]})
    return {'schema':'g3d.mesh_snapshot/1','origin_mm':origin.tolist(),'objects':rows,'unsupported_rendered_geometry':unsupported}


def _run(obj,kind,params,vertex_ids,dry_run):
    saved={};working=[]
    try:
        obj=bpy.data.objects.get(obj) if isinstance(obj,str) else obj
        if obj is None or obj.type!='MESH' or obj.hide_render:S.error('choose a visible mesh object')
        if type(dry_run) is not bool:S.error('dry_run must be boolean')
        source=_capture();row=next(r for r in source['objects'] if r['name']==obj.name)
        ids=list(range(len(row['vertices_local']))) if vertex_ids is None else list(vertex_ids)
        if not ids or len(set(ids))!=len(ids) or any(type(i) is not int or not 0<=i<len(row['vertices_local']) for i in ids):S.error('vertex_ids must be distinct existing IDs')
        support=set(ids);op={'id':'main-health','kind':kind,'target_ids':['F1'],
            'scope':{'object_id':row['id'],'vertex_weights':[{'vertex_id':i,'weight':1.} for i in ids],
                     'fixed_vertex_ids':[],'affected_polygon_ids':[i for i,p in enumerate(row['polygons']) if support.intersection(p)]},'params':params}
        operations=MARK.seal(source,[op]);target,reports=S.state(source,operations)
        info={'status':'ok','tool':kind,'dry_run':dry_run,'label':'F1','legend':f'F1: selected vertices on {obj.name}',
              'selected_vertex_ids':ids,'operations':reports,'following_markings':MARK.summary(operations)}
        if dry_run:return info
        touched={op['scope']['object_id']}|{a['object_id'] for a in operations[0].get('marking_attachments',[])}
        for r in source['objects']:
            if r['id'] in touched:
                ob=bpy.data.objects[r['name']];saved[ob.name]=ob.data;ob.data=ob.data.copy();working.append(ob.data)
        # Recapture only changes mesh identities; seal and replay the same exact
        # finite operation against those disposable datablocks before writing.
        source=_capture();operations=MARK.seal(source,[op]);APPLY._surface_apply(source,operations)
        current=_capture();info['checks']=S.verify(source,current,operations)
        return info
    except M.OwnerMeshError as exc:
        result=exc.result()
    except Exception as exc:
        result={'status':'refused','code':'E_MESH_HEALTH','message':str(exc)}
    for name,mesh in saved.items():bpy.data.objects[name].data=mesh
    for mesh in working:
        if mesh.users==0:bpy.data.meshes.remove(mesh)
    return result


def mesh_cleanup(obj,*,vertex_ids=None,strength=1.,dry_run=False):
    """Weld exact selected duplicate vertices and remove zero-area faces; preserve retained positions and UV loops. Closed-solid checks must pass. dry_run returns the proposal without a write."""
    return _run(obj,'mesh_cleanup',{'strength':strength},vertex_ids,dry_run)


def consistent_normals(obj,*,vertex_ids=None,strength=1.,dry_run=False):
    """Orient a complete selected closed shell outward, retaining valid authored smooth/split normals and rotating backward loop normals outward. Winding correction is indivisible; strength blends shading. Geometry positions stay exact."""
    return _run(obj,'consistent_normals',{'strength':strength},vertex_ids,dry_run)


def symmetrize_mesh(obj,pairs,*,vertex_ids=None,strength=1.,dry_run=False):
    """Move each selected destination vertex toward its explicit source vertex mirrored about asset X=0. pairs=[[source,destination],...]. Markings follow and minimum thickness is checked."""
    return _run(obj,'symmetrize',{'strength':strength,'pairs':pairs},vertex_ids,dry_run)


def controlled_decimate(obj,*,vertex_ids=None,max_removed_vertices=1,strength=1.,dry_run=False):
    """Remove up to ceil(strength*max_removed_vertices) selected planar interior fan vertices. Retained positions, silhouette, existing sharp edges, material/UV seams and marking attachment triangles stay exact; curved areas may yield zero removals. Normal encoding can add scoped sharp fan flags without moving geometry."""
    return _run(obj,'controlled_decimate',{'strength':strength,'max_removed_vertices':max_removed_vertices},vertex_ids,dry_run)
