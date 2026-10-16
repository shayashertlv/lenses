"""Trusted local mesh patch application. Not exposed to the Blender-code facade."""
from __future__ import annotations

import json
from pathlib import Path

import bpy
import numpy as np

from . import owner_mesh as M
from . import export_glb


def _transport_normals(obj, before, after, affected):
    """Carry each original loop normal through its changed face's local affine map."""
    normals = np.asarray(obj["corner_normals"], float).copy()
    loop_start = 0
    for pid, ids in enumerate(obj["polygons"]):
        if pid not in affected:
            loop_start += len(ids)
            continue
        old, new = before[ids], after[ids]
        found = None
        for k in range(1, len(ids)-1):
            a, b = old[k]-old[0], old[k+1]-old[0]
            c, d = new[k]-new[0], new[k+1]-new[0]
            n, nn = np.cross(a, b), np.cross(c, d)
            if np.linalg.norm(n) > 1e-10:
                if np.linalg.norm(nn) <= 1e-10:
                    raise M.OwnerMeshError("E_OWNER_PHYSICAL", "the edit collapses a previously nondegenerate face")
                found = np.column_stack((c, d, nn/np.linalg.norm(nn))) @ np.linalg.inv(
                    np.column_stack((a, b, n/np.linalg.norm(n))))
                break
        if found is not None:
            vals = normals[loop_start:loop_start+len(ids)] @ np.linalg.inv(found)
            vals /= np.maximum(np.linalg.norm(vals, axis=1, keepdims=True), 1e-30)
            normals[loop_start:loop_start+len(ids)] = vals
        loop_start += len(ids)
    return normals


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
