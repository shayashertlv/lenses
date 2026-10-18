"""Trusted local mesh patch application. Not exposed to the Blender-code facade."""
from __future__ import annotations

import json
from pathlib import Path

import bpy
import numpy as np

from . import owner_mesh as M
from . import export_glb
from . import owner_surface
from . import owner_materials_bpy
from . import owner_normals_bpy


def _surface_apply(snapshot, operations):
    target, reports = owner_surface.state(snapshot, operations)
    old = M.objects(snapshot)
    for oid, row in M.objects(target).items():
        source = old[oid]
        if row == source:
            continue
        obj = bpy.data.objects.get(source['name'])
        if obj is None or obj.type != 'MESH' or obj.data.name != source['mesh_name']:
            raise M.OwnerMeshError('E_OWNER_STALE', 'the selected mesh is absent')
        me = obj.data
        if ([list(v.co) for v in me.vertices] != source['vertices_local'] or
                [list(p.vertices) for p in me.polygons] != source['polygons']):
            raise M.OwnerMeshError('E_OWNER_STALE', 'source changed after its trusted snapshot')
        if owner_materials_bpy.apply(obj,source,row) and all(row[k]==source[k] for k in ('vertices_local','polygons','corner_normals','polygon_smooth','uv_layers')):
            continue
        if all(row[k]==source[k] for k in ('vertices_local','polygons','corner_normals','polygon_smooth','uv_layers')):
            continue
        sharp = {tuple(sorted(edge.vertices)): bool(edge.use_edge_sharp) for edge in me.edges}
        sharp=owner_surface.split_edge_values(sharp,row.get('_surface_edge_splits',[]))
        remap=row.get('_surface_vertex_remap')
        if '_surface_sharp_edges' in row:
            sharp={tuple(sorted(e)):True for e in row['_surface_sharp_edges']}
        elif remap is not None:
            sharp={tuple(sorted((remap[a],remap[b]))):value for (a,b),value in sharp.items() if remap[a] is not None and remap[b] is not None}
        if row['polygons'] != source['polygons'] or len(row['vertices_local'])!=len(source['vertices_local']):
            me.clear_geometry()
            me.from_pydata(row['vertices_local'], [], row['polygons'])
            me.update()
            for layer in list(me.uv_layers): me.uv_layers.remove(layer)
            for uv in row.get('uv_layers', []):
                layer = me.uv_layers.new(name=uv['name'])
                layer.data.foreach_set('uv', np.asarray(uv['uv'], np.float32).ravel())
            for uv in row.get('uv_layers', []):
                if uv.get('active_render'): me.uv_layers[uv['name']].active_render = True
                if uv.get('active_clone'): me.uv_layers[uv['name']].active_clone = True
            if 'uv_active_index' in source: me.uv_layers.active_index = source['uv_active_index']
            for record in source.get('attributes', []):
                name = record['name']
                if name not in ('.select_vert', '.select_poly', '.select_edge'):continue
                values = record['values']
                if name == '.select_vert':
                    parents=row.get('_surface_vertex_parents')
                    values = [values[i] for i in parents] if parents is not None else values+[[False]]*(len(row['vertices_local'])-len(source['vertices_local']))
                elif name == '.select_poly':
                    values = [values[i] for i in row['_surface_polygon_parents']]
                else:
                    by_edge = {tuple(sorted((remap[a],remap[b]) if remap is not None else (a,b))):values[i] for i,(a,b) in enumerate(source['edges']) if remap is None or remap[a] is not None and remap[b] is not None}
                    by_edge=owner_surface.split_edge_values(by_edge,row.get('_surface_edge_splits',[]))
                    values = [by_edge.get(tuple(sorted(e.vertices)),[False]) for e in me.edges]
                attribute = me.attributes.get(name) or me.attributes.new(name,record['data_type'],record['domain'])
                field, _, dtype = export_glb._ATTR_ACCESS[record['data_type']]
                attribute.data.foreach_set(field,np.asarray(values,dtype=dtype).ravel())
        else:
            me.vertices.foreach_set('co', np.asarray(row['vertices_local'], np.float32).ravel())
        for poly, smooth, material in zip(me.polygons, row.get('polygon_smooth', []), row.get('polygon_material', [])):
            if poly.use_smooth != smooth: poly.use_smooth = smooth
            if poly.material_index != material: poly.material_index = material
        me.update()
        for edge in me.edges:
            wanted = sharp.get(tuple(sorted(edge.vertices)), False)
            if edge.use_edge_sharp != wanted: edge.use_edge_sharp = wanted
        if not any(a['name'] == 'sharp_edge' for a in source.get('attributes', [])):
            attribute = me.attributes.get('sharp_edge')
            if attribute is not None and not any(v.value for v in attribute.data): me.attributes.remove(attribute)
        me.update()
        try:owner_normals_bpy.write(me,row['corner_normals'])
        except ValueError as exc:raise M.OwnerMeshError('E_OWNER_NORMALS',str(exc)) from exc
        if row.get('edge_radius_recipe')!=source.get('edge_radius_recipe'):
            from .owner_edge_radius import PROPERTY
            if row.get('edge_radius_recipe') is None:
                if PROPERTY in me:del me[PROPERTY]
            else:me[PROPERTY]=json.dumps(row['edge_radius_recipe'],sort_keys=True,separators=(',',':'))
    return {'status': 'ok', 'result': {'schema': 'g3d.owner_surface_patch/1', 'operations': reports}}


def apply(params, root):
    """Host-authenticated patch only; source file and current mesh identities are rechecked."""
    allowed = {"snapshot_path", "snapshot_sha256", "operations", "expect_fingerprint",
               "target_snapshot_path", "target_snapshot_sha256"}
    if set(params)-allowed:
        return {"status": "refused", "code": "E_OWNER_ARGS", "message": "unexpected patch fields"}
    try:
        path = Path(params["snapshot_path"]).resolve()
        root = Path(root).resolve()
        if not path.is_relative_to(root) or path.suffix != ".json":
            raise M.OwnerMeshError("E_OWNER_SNAPSHOT", "snapshot must stay inside its job")
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if M.digest(snapshot) != params["snapshot_sha256"]:
            raise M.OwnerMeshError("E_OWNER_SNAPSHOT", "the source snapshot changed")
        if owner_surface.active(params['operations']):
            if 'target_snapshot_path' in params:
                target_path=Path(params['target_snapshot_path']).resolve()
                if not target_path.is_relative_to(root) or target_path.suffix!='.json':
                    raise M.OwnerMeshError('E_OWNER_SNAPSHOT','target snapshot must stay inside its job')
                target=json.loads(target_path.read_text(encoding='utf-8'))
                if M.digest(target)!=params.get('target_snapshot_sha256'):
                    raise M.OwnerMeshError('E_OWNER_SNAPSHOT','the private target snapshot changed')
                owner_surface.verify(snapshot,target,params['operations'])
            return _surface_apply(snapshot, params['operations'])
        target = None
        if "target_snapshot_path" in params:
            target_path = Path(params["target_snapshot_path"]).resolve()
            if not target_path.is_relative_to(root) or target_path.suffix != ".json":
                raise M.OwnerMeshError("E_OWNER_SNAPSHOT", "target snapshot must stay inside its job")
            target = json.loads(target_path.read_text(encoding="utf-8"))
            if M.digest(target) != params["target_snapshot_sha256"]:
                raise M.OwnerMeshError("E_OWNER_SNAPSHOT", "the private target snapshot changed")
            plan = M.between(snapshot, target, params["operations"])
        else:
            plan = M.plan(snapshot, params["operations"])
        indexed = M.objects(snapshot)
        targets = M.objects(target) if target else {}
        prepared = []
        origin = np.asarray(snapshot["origin_mm"], float)
        for patch in plan["patches"]:
            source = indexed[patch["object_id"]]
            obj = bpy.data.objects.get(source["name"])
            if obj is None or obj.type != "MESH" or obj.data.name != source["mesh_name"]:
                raise M.OwnerMeshError("E_OWNER_STALE", "the captured editable object is absent")
            current = np.asarray([tuple(v.co) for v in obj.data.vertices], float)
            polygons = [list(p.vertices) for p in obj.data.polygons]
            if not np.array_equal(current, np.asarray(source["vertices_local"])) or polygons != source["polygons"]:
                raise M.OwnerMeshError("E_OWNER_STALE", "the editable mesh changed after its trusted snapshot")
            old = np.asarray(source["vertices_world_mm"], float)
            new = old.copy()
            new[patch["vertex_ids"]] = patch["positions_world_mm"]
            matrix = np.asarray(obj.matrix_world, float)
            local = current.copy()
            ids = patch["vertex_ids"]
            local[ids] = (patch["positions_local"] if target else
                          (new[ids]+origin-matrix[:3, 3]) @ np.linalg.inv(matrix[:3, :3]).T)
            prepared.append((obj, local, patch, source))
        for obj, local, patch, source in prepared:
            obj.data.vertices.foreach_set("co", local.astype(np.float32).ravel())
            obj.data.update()
            # Finite vertex edits retain the authored encoding, sharp flags and automatic/custom mode exactly.
            # Blender updates the decoded vectors in their local normal spaces as the geometry changes.
            # Re-encoding with normals_split_custom_set can silently introduce sharp edges, so do not call it.
            if target and source["has_custom_normals"]:
                rows = targets[patch["object_id"]].get("normal_attributes")
                if rows is None:
                    raise M.OwnerMeshError("E_OWNER_NORMALS", "capture encoded normal attributes before editing")
                for row in rows:
                    attr = obj.data.attributes.get(row["name"])
                    if attr is None or attr.domain != row["domain"] or attr.data_type != row["data_type"] or row["values"] is None:
                        raise M.OwnerMeshError("E_OWNER_NORMALS", "unsupported encoded normal attribute correspondence")
                    field, width, dtype = export_glb._ATTR_ACCESS[attr.data_type]
                    values = np.asarray(row["values"], dtype=dtype)
                    attr.data.foreach_set(field, values.ravel())
                obj.data.update()
            # Originally automatic normal meshes remain automatic. No layer is introduced.
        return {"status": "ok", "result": plan}
    except M.OwnerMeshError as exc:
        return exc.result()
