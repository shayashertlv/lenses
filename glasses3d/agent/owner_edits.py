"""Confirmed owner operations and a scope guard shared by every edit-mode tool.

Authority is read from the host's sealed round, never accepted as a tool argument.
The normal photo-driven specialist and its measurement gates are unchanged.
"""
import copy
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict

from glasses3d.g3d import owner_mesh as M
from glasses3d.g3d import owner_markings as markings
from .procs import write_json_atomic


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    delta_mm: list[float] | None
    delta_deg: float | None
    axis_points_mm: list[list[float]] | None


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    operation_id: str
    params: Parameters


def snapshot(scene, label="owner-check"):
    path = scene.root / "work/model_maps" / (label + "-" + uuid4().hex) / "snapshot.json"
    result = scene.s.call_read("model_map", "model_map_snapshot", {"path": str(path)},
                               deadline_s=150, call_id=label + "-" + uuid4().hex)
    body = result.get("result", result)
    if result.get("status") != "ok" or body.get("unchanged") is not True or not path.is_file():
        raise M.OwnerMeshError("E_OWNER_SNAPSHOT", "current mesh snapshot failed or changed the scene")
    return json.loads(path.read_text(encoding="utf-8")), path


def verify_snapshots(baseline, current, operations):
    """Only confirmed vertices and their incident normals may change; every other datum stays fixed."""
    markings.require_bound(baseline, operations)
    old, new = M.objects(baseline), M.objects(current)
    if set(old) != set(new) or baseline["origin_mm"] != current["origin_mm"]:
        raise M.OwnerMeshError("E_OWNER_SCOPE", "objects or the bridge origin changed outside the confirmed target")
    support, affected = markings.support(old, operations)
    marking_ids = set(support)
    for op in operations:
        oid = op["scope"]["object_id"]
        if oid not in old:
            raise M.OwnerMeshError("E_OWNER_SCOPE", "confirmed object is absent")
        weights, faces = M.scope_vertices(op["scope"], old[oid])
        support.setdefault(oid, set()).update(i for i, w in weights.items() if w)
        affected.setdefault(oid, set()).update(faces)
    variable = {"vertices_local", "vertices_world_mm", "positions_sha256", "corner_normals",
                "normal_attributes", "evaluated", "triangles", "triangle_polygons"}
    changes, checks = [], {}
    for oid, a in old.items():
        b = new[oid]
        if oid not in support:
            if a != b:
                raise M.OwnerMeshError("E_OWNER_SCOPE", f"unrequested object {a['name']} changed")
            continue
        for key in set(a) | set(b):
            if key not in variable and a.get(key) != b.get(key):
                raise M.OwnerMeshError("E_OWNER_SCOPE", f"{a['name']}: unrequested {key} changed")
        va, vb = np.asarray(a["vertices_world_mm"]), np.asarray(b["vertices_world_mm"])
        if va.shape != vb.shape:
            raise M.OwnerMeshError("E_OWNER_SCOPE", "mesh vertex count changed")
        ids = np.flatnonzero(np.any(va != vb, axis=1))
        if set(ids)-support[oid]:
            raise M.OwnerMeshError("E_OWNER_SCOPE", "vertices outside confirmed support changed",
                                   vertex_ids=sorted(set(ids)-support[oid]))
        ca, cb = np.asarray(a["corner_normals"]), np.asarray(b["corner_normals"])
        if ca.shape != cb.shape:
            raise M.OwnerMeshError("E_OWNER_SCOPE", "corner normal count changed")
        normal_faces = M.normal_faces(a, affected[oid])
        permitted_loops, cursor = set(), 0
        for pid, poly in enumerate(a["polygons"]):
            if pid in normal_faces:
                permitted_loops.update(range(cursor, cursor+len(poly)))
            cursor += len(poly)
        normal_change = np.flatnonzero(np.any(ca != cb, axis=1))
        if set(normal_change)-permitted_loops:
            raise M.OwnerMeshError("E_OWNER_SCOPE", "normals outside the shown normal-only influence ring changed")
        attrs_a = {r["name"]: r for r in a.get("normal_attributes", [])}
        attrs_b = {r["name"]: r for r in b.get("normal_attributes", [])}
        if set(attrs_a) != set(attrs_b):
            raise M.OwnerMeshError("E_OWNER_SCOPE", "the original normal attribute layers changed")
        for name, attr in attrs_a.items():
            other = attrs_b[name]
            if any(attr.get(k) != other.get(k) for k in set(attr)-{"values"}):
                raise M.OwnerMeshError("E_OWNER_SCOPE", "normal attribute representation changed")
            if attr["values"] == other["values"]:
                continue
            if attr.get("domain") != "CORNER" or attr["values"] is None or other["values"] is None:
                raise M.OwnerMeshError("E_OWNER_SCOPE", "unsupported normal attribute change")
            aa, bb = np.asarray(attr["values"]), np.asarray(other["values"])
            if aa.shape != bb.shape or set(np.flatnonzero(np.any(aa != bb, axis=1)))-permitted_loops:
                raise M.OwnerMeshError("E_OWNER_SCOPE", "encoded normal rows outside the shown influence ring changed")
        ev = b.get("evaluated", {})
        if not ev.get("topology_matches_source") or not ev.get("positions_match_source"):
            raise M.OwnerMeshError("E_OWNER_CORRESPONDENCE", "the edited modifier result no longer corresponds to the captured mesh")
        checks[oid] = (markings.validate_marking(b, va, vb) if oid in marking_ids else M.physical(b, reference=a))
        if len(ids):
            distances = np.linalg.norm(vb[ids]-va[ids], axis=1)
            changes.append({"object_id": oid, "object": a["name"], "changed_vertex_ids": ids.tolist(),
                            "affected_polygon_ids": sorted(affected[oid]),
                            "normal_affected_polygon_ids": sorted(normal_faces),
                            "changed_normal_loop_ids": normal_change.tolist(),
                            "maximum_move_mm": float(distances.max())})
    # Full checkpoint state catches hidden objects, nonmesh transforms, unused datablocks and settings too.
    if "scene_state" in baseline and "scene_state" in current:
        left, right = copy.deepcopy(baseline["scene_state"]), copy.deepcopy(current["scene_state"])
        for oid in support:
            name = old[oid]["mesh_name"]
            left.get("meshes", {}).pop(name, None)
            right.get("meshes", {}).pop(name, None)
            # Bounding boxes in the human summary are derived from the exact positions checked above.
            object_name = old[oid]["name"]
            left.get("summary", {}).get(object_name, {}).pop("bbox_mm", None)
            right.get("summary", {}).get(object_name, {}).pop("bbox_mm", None)
        if left != right:
            raise M.OwnerMeshError("E_OWNER_SCOPE", "unrequested scene or datablock state changed")
    return {"ok": True, "status": "ok", "changes": changes, "physical": checks,
            "outside_support_unchanged": True, "materials_uvs_transforms_unchanged": True,
            "marking_surface_checks": markings.surface_checks(baseline,
                {oid: np.asarray(o['vertices_world_mm']) for oid, o in new.items()}, operations)}


def expected_positions(source, operations):
    """Exact float32 local representation produced by one confirmed worker call."""
    indexed = M.objects(source)
    world, _, _ = M.deformation(source, operations)
    result = {oid: np.asarray(obj["vertices_local"], float).copy() for oid, obj in indexed.items()}
    for oid in world:
        obj = indexed[oid]
        matrix = np.asarray(obj.get("matrix_world", np.eye(4)), float)
        before = np.asarray(obj["vertices_world_mm"], float)
        ids = np.flatnonzero(np.any(world[oid] != before, axis=1))
        local = (world[oid][ids]+source["origin_mm"]-matrix[:3, 3]) @ np.linalg.inv(matrix[:3, :3]).T
        result[oid][ids] = local.astype(np.float32).astype(float)
    return result


def _positions_equal(current, expected):
    return all(np.array_equal(np.asarray(obj["vertices_local"]), expected[obj["id"]])
               for obj in current["objects"])


def _advance(source, operations):
    out = copy.deepcopy(source)
    positions = expected_positions(source, operations)
    for obj in out["objects"]:
        local = positions[obj["id"]]
        matrix = np.asarray(obj.get("matrix_world", np.eye(4)), float)
        obj["vertices_local"] = local.tolist()
        obj["vertices_world_mm"] = (local @ matrix[:3, :3].T+matrix[:3, 3]-source["origin_mm"]).tolist()
    return out


def confirmed_transition(sealed, current, before_fingerprint):
    """Recognize complete confirmed operations, not an arbitrary displacement inside their support.

    Receipt groups reproduce the worker's float32 rounding at each completed call.
    Least squares proposes a binary subset only; exact replay is the acceptance test.
    """
    before = sealed["snapshot"]
    if sealed.get("selected_preview"):
        if _positions_equal(current, expected_positions(before, [])):
            return []
        if not _positions_equal(current, expected_positions(before, sealed["all_operations"])):
            raise M.OwnerMeshError("E_OWNER_AMOUNT", "the edited mesh must equal the complete selected preview option")
        if before_fingerprint == sealed["source_fingerprint"]:
            return sealed["all_operations"]
        if any(row.get("fingerprint") == before_fingerprint for row in sealed["receipts"]):
            return []
        raise M.OwnerMeshError("E_OWNER_AMOUNT", "selected preview has no confirmed operation receipt")
    byid = {op["id"]: op for op in sealed["all_operations"]}
    used = set()
    if before_fingerprint != sealed["source_fingerprint"]:
        matched = False
        for receipt in sealed["receipts"]:
            ops = []
            for row in receipt.get("operations", []):
                if row["id"] in used:
                    continue
                op = copy.deepcopy(byid[row["id"]]); op["params"] = row["params"]
                ops.append(op); used.add(op["id"])
            before = _advance(before, ops)
            if receipt.get("fingerprint") == before_fingerprint:
                matched = True
                break
        if not matched:
            raise M.OwnerMeshError("E_OWNER_AMOUNT", "the previous geometry has no current confirmed-operation receipt")
    remaining = [op for op in sealed["all_operations"] if op["id"] not in used]
    if _positions_equal(current, expected_positions(before, [])):
        return []
    if not remaining:
        raise M.OwnerMeshError("E_OWNER_AMOUNT", "all confirmed operations are already applied; this movement is additional")
    ids = sorted(M.objects(before))
    base = np.concatenate([np.asarray(M.objects(before)[i]["vertices_local"]).ravel() for i in ids])
    actual = np.concatenate([np.asarray(M.objects(current)[i]["vertices_local"]).ravel() for i in ids])-base
    columns = []
    for op in remaining:
        proposal = expected_positions(before, [op])
        columns.append(np.concatenate([proposal[i].ravel() for i in ids])-base)
    coefficients = np.linalg.lstsq(np.column_stack(columns), actual, rcond=None)[0]
    guessed = [op for op, value in zip(remaining, coefficients) if value > .5]
    candidates = [guessed, remaining, *[[op] for op in remaining]]
    seen = set()
    for candidate in candidates:
        key = tuple(op["id"] for op in candidate)
        if key in seen:
            continue
        seen.add(key)
        if _positions_equal(current, expected_positions(before, candidate)):
            return candidate
    raise M.OwnerMeshError("E_OWNER_AMOUNT", "the changed vertices do not match the confirmed operation directions and amounts; use facet_owner_edit for an exact operation group")


class ScopeGuard:
    def __init__(self, authority):
        self.authority = authority

    def verify(self, scene):
        try:
            sealed = self.authority.resolve(None, scene.s.head)
            current, path = snapshot(scene)
            result = verify_snapshots(sealed["snapshot"], current, sealed["all_operations"])
            if (sealed.get("selected_preview") and current.get("fingerprint") not in
                    (sealed["source_fingerprint"], sealed["selected_preview"]["fingerprint"])):
                raise M.OwnerMeshError("E_OWNER_AMOUNT", "the edited scene must match the selected preview, including normals")
            if sealed.get("selected_preview") and not (
                    _positions_equal(current, expected_positions(sealed["snapshot"], [])) or
                    _positions_equal(current, expected_positions(sealed["snapshot"], sealed["all_operations"]))):
                raise M.OwnerMeshError("E_OWNER_AMOUNT", "only the unchanged source or complete selected preview is authorized")
            expected = getattr(scene, "_owner_expected_positions", None)
            recognized = []
            if expected is not None:
                if not _positions_equal(current, expected):
                    raise M.OwnerMeshError("E_OWNER_AMOUNT", "the actual mesh differs from the host-resolved confirmed amounts")
            else:
                current_fingerprint = scene.s.head["fingerprint"]
                if (current_fingerprint == sealed["source_fingerprint"] or
                        any(r.get("fingerprint") == current_fingerprint for r in sealed["receipts"])):
                    # A report/finish checks the already receipt-bound head; it must not apply or re-record it.
                    before_fingerprint = current_fingerprint
                else:
                    previous = scene.s.index.get(scene.s.head.get("parent")) or scene.s.head
                    before_fingerprint = previous["fingerprint"]
                recognized = confirmed_transition(sealed, current, before_fingerprint)
            exported = scene.seeing.export_head("owner-scope-" + uuid4().hex)
            validation = exported.get("validate_spec", {})
            if exported.get("status") != "exported" or not validation.get("ok"):
                raise M.OwnerMeshError("E_OWNER_SPEC", "the edited current mesh does not pass strict export specification",
                                       export=exported)
            result = {**result, "snapshot_path": str(path), "snapshot_sha256": M.digest(current),
                    "strict_spec": validation, "confirmed_amounts_match": True,
                    "export_sha256": exported["sha256"], "authority_sha256": sealed["authority_sha256"]}
            if recognized:
                self.authority.record_applied({"schema": "g3d.owner_edit_receipt/1", "status": "ok",
                    "call_id": scene.s.head.get("call_id"), "round_id": sealed["round_id"],
                    "authority_sha256": sealed["authority_sha256"],
                    "confirmation_sha256": sealed["confirmation_sha256"],
                    "operation_ids": [op["id"] for op in recognized], "operations": recognized,
                    "checkpoint": scene.s.head["id"], "fingerprint": scene.s.head["fingerprint"], "checks": result,
                    "request_text": sealed["request_text"], "confirmation_text": sealed["confirmation_text"],
                    "method": "general tool produced the exact confirmed finite operation result"})
            return result
        except M.OwnerMeshError as exc:
            return {**exc.result(), "ok": False}
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return {"ok": False, "status": "refused", "code": "E_OWNER_AUTHORITY", "message": str(exc)}


def corrected_operations(sealed, corrections):
    operations = copy.deepcopy(sealed["operations"])
    if not corrections:
        return operations
    if sealed.get("selected_preview"):
        raise M.OwnerMeshError("E_OWNER_AUTHORITY", "selected preview amounts cannot be replaced; request another shown preview")
    if not sealed.get("correction"):
        raise M.OwnerMeshError("E_OWNER_AUTHORITY", "changed amounts require an explicit owner correction round")
    byid = {op["id"]: op for op in operations}
    seen = set()
    for row in corrections:
        if set(row) != {"operation_id", "params"} or row["operation_id"] not in byid or row["operation_id"] in seen:
            raise M.OwnerMeshError("E_OWNER_AUTHORITY", "a correction may only change parameters of one confirmed operation")
        op = byid[row["operation_id"]]
        if set(row["params"]) != set(op["params"]):
            raise M.OwnerMeshError("E_OWNER_AUTHORITY", "a correction cannot change operation kind or parameter fields")
        # The geometric axis is part of the shown target. Only direction/amount may be reinterpreted.
        if row["params"].get("axis_points_mm") != op["params"].get("axis_points_mm"):
            raise M.OwnerMeshError("E_OWNER_AUTHORITY", "a correction cannot move the shown operation axis")
        op["params"] = copy.deepcopy(row["params"])
        op["owner_correction_sha256"] = sealed["confirmation_sha256"]
        seen.add(row["operation_id"])
    return operations


def apply(scene, operation_ids, call_id, *, corrections=None):
    """Host-only resolved scope; repeated IDs return their existing receipt without movement."""
    try:
        authority = scene.owner_edit_authority
        sealed = authority.resolve(operation_ids, scene.s.head)
        operations = corrected_operations(sealed, corrections)
        already = set(sealed["applied_operation_ids"])
        if set(operation_ids) <= already:
            if (not sealed["receipts"] or sealed["receipts"][-1].get("fingerprint") != scene.s.head["fingerprint"]
                    or scene.s.fingerprint() != scene.s.head["fingerprint"]):
                raise M.OwnerMeshError("E_OWNER_REPLAY", "the scene no longer matches the last applied operation receipt")
            return {"status": "ok", "idempotent": True, "operation_ids": operation_ids,
                    "receipts": [r for r in sealed["receipts"] if set(r.get("operation_ids", [])) & set(operation_ids)]}
        if already.intersection(operation_ids):
            raise M.OwnerMeshError("E_OWNER_REPLAY", "apply only new operation IDs; some requested IDs already have receipts")
        current, path = snapshot(scene, "owner-before")
        verify_snapshots(sealed["snapshot"], current, sealed["all_operations"])
        plan = M.plan(current, operations)  # compute/check first, without writing scene state
        before = copy.deepcopy(scene.s.head)
        scene._owner_expected_positions = expected_positions(current, operations)
        try:
            result = scene.s.call_mutating("facet_owner_edit", "owner_edit_apply", {
                "snapshot_path": str(path), "snapshot_sha256": M.digest(current), "operations": operations},
                deadline_s=180, call_id=call_id, label="Confirmed owner feature edits")
        finally:
            del scene._owner_expected_positions
        if result.get("status") != "ok":
            return result
        checked = result.get("owner_scope_check") or ScopeGuard(authority).verify(scene)
        if not checked["ok"]:
            scene.s.restore_checkpoint(before["id"], call_id=call_id+"-rollback")
            return checked
        receipt = {"schema": "g3d.owner_edit_receipt/1", "status": "ok", "call_id": call_id,
                   "round_id": sealed["round_id"], "authority_sha256": sealed["authority_sha256"],
                   "confirmation_sha256": sealed["confirmation_sha256"],
                   "operation_ids": operation_ids, "operations": plan["operations"],
                   "checkpoint_before": before["id"], "checkpoint": scene.s.head["id"],
                   "fingerprint": scene.s.head["fingerprint"], "checks": checked,
                   "measurement_policy": "Confirmed owner targets outrank photo readings and prior choices only on these targets; original observations are unchanged and reported as information.",
                   "request_text": sealed["request_text"], "confirmation_text": sealed["confirmation_text"]}
        authority.record_applied(receipt)
        return receipt
    except M.OwnerMeshError as exc:
        return exc.result()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {"status": "refused", "code": "E_OWNER_AUTHORITY", "message": str(exc)}


def tool_for(scene, tool, call):
    """Strict tool arguments carry IDs; only explicit correction rounds accept changed numeric amounts."""
    @tool("facet_owner_edit")
    async def facet_owner_edit(ctx: ToolContext, operation_ids: list[str],
                               corrections: list[Correction] | None):
        """Apply the owner's confirmed numbered feature changes to the actual current mesh. Target IDs,
        support, operation kinds and amounts come from the shown understanding and host confirmation.
        Confirmed owner targets outrank photo readings and earlier choices only for those named features;
        retain and report contrary observations. Closed-solid, thickness, physical orientation and strict
        specification checks remain. Unrequested geometry, materials and UVs stay unchanged. Repeating an
        applied ID never repeats its displacement. An explicit owner correction may revise numeric direction
        or amount on the same shown target and axis; it cannot expand the support or create a new target.
        For a selected rendered preview, apply the complete recorded operation group in its shown order,
        with corrections null. Its amounts cannot be replaced, even in a correction round; a different
        amount needs another shown preview. General edit tools must produce that same complete option.
        The surface markings listed in the confirmed understanding follow their surface automatically.
        Newly buried marking vertices and new surface intersections refuse. Older understandings that omit
        detected markings must be renewed before editing; no dependency is silently added after confirmation.

        Args:
            operation_ids: The confirmed operation IDs to apply, in the host's recorded order.
            corrections: Null normally. Only for an explicit correction round: interpreted numeric parameters
                bound to the owner's exact correction text; give null for unused parameter fields.
        """
        rows = None if corrections is None else [
            {"operation_id": c.operation_id, "params": c.params.model_dump(exclude_none=True)} for c in corrections]
        return await call(ctx, "facet_owner_edit", lambda cid: apply(scene, operation_ids, cid, corrections=rows))
    return facet_owner_edit
