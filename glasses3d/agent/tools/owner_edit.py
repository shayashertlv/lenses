"""Two-step owner communication, using actual mesh maps and actual AR evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from agents.tool_context import ToolContext
import numpy as np
from pydantic import BaseModel, ConfigDict
from PIL import Image

from .. import edits, owner_edits, procs
from . import model_map, look, owner_preview, look_composition, native_panels
from ...g3d import owner_markings
from ...g3d import owner_surface


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str | None = None
    number: int
    label: str
    interpretation: str
    disposition: Literal["executable", "unsupported", "needs_clarification"]
    reason: str
    direction: str
    amount: str
    target_ids: list[str]
    operation_json: str


def understand(scene, call_id, map_id, map_sha256, capture_receipts, items, limitations, preview_receipt=None):
    doc = edits.load(scene.root)
    if not doc or doc["phase"] != "understand":
        raise ValueError("understand_finish is only for an understand round")
    if scene.s.fingerprint() != doc["source"]["fingerprint"]:
        raise ValueError("understand cannot change the delivered model")
    records = [Interpretation.model_validate(item).model_dump(exclude_none=True) for item in items]
    if not records or [r["number"] for r in records] != list(range(1, len(records) + 1)):
        raise ValueError("interpret each item separately with consecutive numbers starting at 1")
    if doc.get("owner_capture_items") and [row.get("item_id") for row in records] != [
            row["item_id"] for row in doc["owner_capture_items"]]:
        raise ValueError("interpret every owner capture item once, with its original item_id and order")
    operations = []
    diagnostic_targets = set()
    for item in records:
        if any(not item[key].strip() for key in ("label", "interpretation")):
            raise ValueError("each item needs its label and interpretation")
        value = json.loads(item["operation_json"])
        if item["disposition"] != "executable":
            if value != [] or not item["reason"].strip():
                raise ValueError("blocked items need operation_json=[] and an explanatory reason")
            diagnostic_targets.update(item["target_ids"])
            continue
        if any(not item[key].strip() for key in ("direction", "amount")):
            raise ValueError("executable items need direction and proposed amount")
        group = value if isinstance(value, list) else [value]
        if (not group or any(not isinstance(op, dict) for op in group) or not item["target_ids"] or
                set(key for op in group for key in op["target_ids"]) != set(item["target_ids"])):
            raise ValueError("interpretation and operation must name the same shown targets")
        operations.extend(group)
    if len({op["id"] for op in operations}) != len(operations):
        raise ValueError("operation IDs must be unique")
    if not capture_receipts:
        raise ValueError("understanding requires at least one successfully delivered map capture")
    evidence = model_map.validate_understanding(scene.root, map_id, map_sha256, capture_receipts, operations)
    if not diagnostic_targets.issubset(evidence["annotated_target_ids"]):
        raise ValueError("a diagnostic target was not shown in the delivered map")
    operations = evidence.get("operations", operations)
    if evidence["source"]["fingerprint"] != doc["source"]["fingerprint"]:
        raise ValueError("the shown map is not of the selected delivered checkpoint")
    snapshot = json.loads(Path(evidence['snapshot_path']).read_text(encoding='utf-8'))
    operations = owner_markings.seal(snapshot, operations) if operations else []
    if operations and preview_receipt is None:
        raise ValueError('every executable operation group requires a delivered two-or-three-amount preview before confirmation')
    if any(op['kind'] in owner_surface.OPERATION_KINDS for op in operations):
        original = before = owner_surface.signature(snapshot)
        for count in range(1, len(operations)+1):
            after = owner_surface.signature(owner_surface.state(snapshot, operations[:count])[0])
            if before == after:
                raise ValueError('executable operations must change geometry or shading; no no-op placeholders')
            before = after
        if original == after:
            raise ValueError('executable group leaves geometry and shading unchanged')
    elif operations:
        original = before = owner_edits.expected_positions(snapshot, [])
        requested_objects = {op['scope']['object_id'] for op in operations}
        for count in range(1, len(operations) + 1):
            after = owner_edits.expected_positions(snapshot, operations[:count])
            key = operations[count-1]['scope']['object_id']
            if np.array_equal(before[key].astype(np.float32), after[key].astype(np.float32)):
                raise ValueError("executable operations must move their shown support; do not invent no-op placeholders")
            before = after
        if all(np.array_equal(original[key].astype(np.float32), after[key].astype(np.float32)) for key in requested_objects):
            raise ValueError("executable operation group leaves the source unchanged; do not invent no-op placeholders")
    following_markings = owner_markings.summary(operations)
    files = {row["path"]: row["sha256"] for row in evidence["evidence_files"]}
    files[evidence["map_path"]] = evidence["map_sha256"]
    preview = None
    images = list(evidence["images"])
    if preview_receipt is not None:
        if not operations:
            raise ValueError("diagnostic-only understanding cannot bind an amount preview")
        preview = owner_preview.load_verified(scene, preview_receipt)
        if (preview["map_id"] != map_id or preview["map_sha256"] != map_sha256 or
                preview["capture_receipts"] != capture_receipts or preview["base_operations"] != operations):
            raise ValueError("preview must show these exact operations, targets and amounts on this map")
        files.update(preview["evidence_files"])
        files[preview["preview_receipt"]] = preview["preview_sha256"]
        images += preview["images"]
    path = scene.root / "work/owner_edit/understanding.json"
    disposition = "awaiting_owner_confirmation" if operations else "diagnostic_only"
    artifact = {"schema": edits.UNDERSTANDING_SCHEMA, "status": disposition,
                "call_id": call_id, "request_text": doc["request_text"], "source_fingerprint": doc["source"]["fingerprint"],
                "items": records, "operations": operations, "limitations": limitations,
                "following_markings": following_markings, "legend": evidence.get('legend', []),
                "snapshot_path": Path(evidence["snapshot_path"]).relative_to(scene.root).as_posix(),
                "map_id": map_id, "map_sha256": map_sha256, "evidence_files": files,
                "capture_receipts": capture_receipts, "geometry_changed": False}
    artifact["scripted"] = bool(getattr(scene, "scripted", False))
    if preview is not None:
        artifact["amount_preview"] = {key: preview[key] for key in
            ("preview_receipt", "preview_sha256", "call_id")}
        artifact["amount_preview"]["options"] = [{key: option[key] for key in
            ("id", "label", "scale", "operations")} for option in preview["options"]]
    procs.write_json_atomic(path, artifact)
    if getattr(scene, "_review_log", None) is None:
        procs.write_json_atomic(path.with_name("understanding-usage.json"), {
            "schema": "g3d.owner_understanding_usage/1", "requests": 0, "settled_usd": "0.000000",
            "settled_micro_usd": 0, "mode": "local_tool_calls"})
    if any(item['disposition'] != 'executable' for item in records):
        next_step = ("STOP: preserve this diagnostic evidence; resolve every blocked item in a new understanding "
                     "before a paid EDIT round. No blocked item grants edit authority.")
    else:
        next_step = ("STOP: awaiting owner selection of a shown option; job edit-apply applies the exact "
                     "preview locally at $0. Only a new or unclear request needs another understanding.")
    return {"status": "accepted", "disposition": disposition, "next": next_step,
            "understanding": str(path), "understanding_sha256": edits.sha(path),
            "interpretations": records, "limitations": limitations, "geometry_changed": False,
            "following_markings": following_markings, "legend": evidence.get('legend', []),
            "amount_preview": artifact.get("amount_preview"), "image_delivery": "separate", "images": images}


def mark_delivered(scene, result, delivered=True):
    if result.get("status") != "accepted" or not result.get("understanding"):
        return
    path = edits.confined(scene.root, result["understanding"])
    if edits.sha(path) != result["understanding_sha256"]:
        raise ValueError("understanding changed during delivery")
    procs.write_json_atomic(path.with_name("understanding-delivery.json"), {
        "schema": "g3d.owner_understanding_delivery/1", "sha256": edits.sha(path),
        "call_id": result["call_id"], "delivered": bool(delivered)})


def mark_report_delivered(scene, result, delivered=True):
    if result.get("status") != "ok" or not result.get("report"):
        return
    path = edits.confined(scene.root, result["report"])
    if edits.sha(path) != result["report_sha256"]:
        raise ValueError("change report changed during delivery")
    procs.write_json_atomic(path.with_name("change-report-delivery.json"), {
        "schema": "g3d.owner_edit_report_delivery/1", "sha256": edits.sha(path),
        "call_id": result["call_id"], "fingerprint": result["fingerprint"], "delivered": bool(delivered)})


def delivered_report(scene):
    path = scene.root / "work/owner_edit/change-report.json"
    receipt_path = path.with_name("change-report-delivery.json")
    if not path.exists() or not receipt_path.exists():
        return None
    report, receipt = edits.read(path), edits.read(receipt_path)
    if (not receipt.get("delivered") or receipt.get("sha256") != edits.sha(path) or
            receipt.get("call_id") in getattr(scene, "_unseen", ()) or
            report.get("fingerprint") != scene.s.head["fingerprint"] or
            receipt.get("fingerprint") != report["fingerprint"] or
            report.get("authority_sha256") != edits.sha(scene.root / "inputs/owner-edit.json")):
        return None
    return {"path": str(path), "sha256": edits.sha(path)}


def _triple(photo, before, after, target, label):
    opened = [Image.open(path).convert("RGB") for path in (photo, before, after)]
    if len({im.size for im in opened}) != 1:
        raise ValueError("before/after images must use the same frozen photo camera and pixel grid")
    full, tiles = native_panels.comparison(opened, [0, 0, *opened[0].size], target, label,
                                         ["PHOTO", "BEFORE", "AFTER"], gap=0, font=model_map._font(18))
    for item in [full, *tiles]:
        item.update(view=f"{label}: photo, before, after (native pixels)", ref=label, kind="owner_edit_full")
    return full, tiles


def change_report(scene, call_id, summary, not_done):
    from ..owner_edits import ScopeGuard
    doc = edits.load(scene.root)
    if not doc or doc["phase"] != "edit":
        raise ValueError("change report needs a confirmed edit round")
    guard = ScopeGuard(scene.owner_edit_authority).verify(scene)
    if not guard.get("ok"):
        return guard
    output = scene.root / "work/owner_edit" / call_id
    output.mkdir(parents=True, exist_ok=False)
    understanding = doc["understanding"]
    manifest = model_map.load_map(scene.root, understanding["map_id"], understanding["map_sha256"])
    source_glb = next(row for row in manifest["assets"] if row["sha256"] == doc["source"]["export_sha256"])
    before_export = {"status": "exported", "path": str(scene.root / source_glb["path"]),
                     "sha256": source_glb["sha256"], "checkpoint": doc["source"]["checkpoint"]}
    after_export = scene.seeing.export_head(call_id=call_id)
    before = look.capture(scene.root, before_export, call_id + "-before", basis=doc["source"]["basis"])
    after = look.capture(scene.root, after_export, call_id + "-after", basis=doc["source"]["basis"])
    if before.get("status") != "ok" or after.get("status") != "ok":
        return {"status": "refused", "code": "E_EDIT_IMAGES", "before": before, "after": after}
    images = []
    old = {r["ref"]: r for r in before["views"]}
    for row in after["views"]:
        prior = old[row["ref"]]
        full, sources = _triple(row["photo_path"], prior["drawing_path"], row["drawing_path"],
                                output / (row["ref"] + "-before-after.png"), row["ref"])
        # Include both ordinary and side-light details; each retains its source
        # record inside the lossless per-photo composed pages.
        for caption, evidence in (("Before", before), ("After", after)):
            sources.extend(dict(r, view=caption + ": " + r["view"], comparison_phase=caption) for r in evidence["images"]
                           if r.get("ref") == row["ref"] and r.get("kind") in ("look_detail", "surface_detail"))
        images.extend(look_composition.compose(sources, output / (row["ref"] + "-combined"), row["ref"]))
    images += [{"path": before["worn_front_path"], "view": "Before: worn on the test face"},
               {"path": after["worn_front_path"], "view": "After: worn on the test face"}]
    receipts = scene.owner_edit_authority.receipts()
    current_receipts = receipts if receipts and receipts[-1].get("fingerprint") == scene.s.fingerprint() else []
    applied = {key for row in current_receipts for key in row["operation_ids"]}
    artifact = {"schema": "g3d.owner_edit_report/1", "status": "ok", "summary": summary,
                "request_text": doc["request_text"], "confirmation_text": doc["confirmation_text"],
                "fingerprint": scene.s.head["fingerprint"], "checkpoint": scene.s.head["id"],
                "authority_sha256": edits.sha(scene.root / "inputs/owner-edit.json"), "scope_check": guard,
                "operations": current_receipts, "historical_receipts_not_currently_verified": receipts if not current_receipts else [],
                "not_done": not_done, "blocked_items": edits.blocked_items(understanding),
                "without_operation_receipt": [op["id"] for op in understanding["operations"] if op["id"] not in applied],
                "before": before, "after": after, "images": images, "image_delivery": "separate",
                "side_effects": guard["changes"]}
    artifact['following_markings'] = understanding.get('following_markings', [])
    path = scene.root / "work/owner_edit/change-report.json"
    procs.write_json_atomic(path, artifact)
    return {**artifact, "report": str(path), "report_sha256": edits.sha(path)}


def tools_for(scene, tool, call):
    @tool("understand_finish")
    async def understand_finish(ctx: ToolContext, map_id: str, map_sha256: str, capture_receipts: list[str],
                                items: list[Interpretation], limitations: list[str], preview_receipt: str | None = None):
        """Stop the short understand round after diagnosing and showing only each requested change; no whole-model
        review is needed. Measure the visible cause before choosing operation kinds, never map by a keyword.
        For movement toward the head, show top and rear as well as side views. Supply the successfully shown map
        receipt(s), numbered interpretations, direction and base proposed amount. Every executable group,
        including translate, rotate and taper, requires two or three delivered amount previews. After preview_owner_edit,
        pass its preview_receipt unchanged: the host attaches its shown options, images and exact amounts;
        the owner selects an option at confirmation. Do not choose for the owner.
        Each item needs disposition executable, unsupported or needs_clarification, plus reason (empty is allowed
        only for executable items). Blocked items require operation_json="[]"; direction and amount may be empty.
        Their optional target_ids are diagnostic labels from the delivered map, never permission to edit.
        Executable items require direction, amount, nonempty target_ids and actual nonzero operations on drawn
        support; never invent a no-op for a blocked request. All-blocked items finish as diagnostic_only: no edit
        or amount-preview authority. Mixed results retain diagnostic evidence, but every blocked item must be
        resolved in a new understanding before a paid EDIT round. This accepts understanding
        only, not a product finish or paid edit.

        Each operation_json is one object or a list for mirrored changes: id, kind, target_ids,
        scope_ref_sha256 (a support hash returned by the shown map), and params. An explicit scope object from
        that same map is also accepted. translate_region takes delta_mm [x,y,z]; rotate_region takes
        axis_points_mm [[x,y,z],[x,y,z]] and delta_deg; taper_region takes that axis and delta_mm, ramping from
        zero at the first point to the full displacement at the second, multiplied by the shown support weights.
        flatten_face takes strength (greater than zero through one), optionally keep_boundary (default true).
        It projects the complete selected patch onto its boundary plane; a nonplanar locked boundary refuses.
        keep_boundary=false explicitly includes boundary movement within the shown support and reports it;
        shared neighbouring faces are part of that shown influence. repair_shading takes strength and mode
        flat or smooth; it changes selected loop normals without moving any vertex. refine_curved_surface takes
        strength; its default uses local cubic tangent interpolation from the selected shading normals and splits
        shared internal edges with curved midpoints. Fully split triangles become four triangles; boundary triangles
        and larger polygons use a centre fan. Shared source normals are averaged within the selection.
        New normals use shared quadratic endpoint/chord controls, retaining shared-edge continuity, not derivative continuity.
        Original vertices, patch boundary edges and the opposite surface stay fixed by default. Original UV
        values stay exact; new per-face values interpolate each face's UVs independently, retaining seams.
        Split edges inherit their sharp/selection flags. Default refinement reports a quadratic fit residual
        when available, otherwise quadric_fit_unavailable_reason; missing fit does not block local tangent interpolation. Select a
        single curved side, not the front and back together. Unknown attributes and verified temple charts
        refuse refinement. These three operations require complete faces with unit support and a delivered
        preview_receipt with two or three amounts; the owner must choose an exact option before editing.
        refine_curved_surface also accepts relax_interior=true, requiring a valid quadratic fit, to project the selected patch's original
        interior vertices toward a fitted quadratic surface, with cubic smoothstep falloff over 10% of the
        frozen patch span near fixed boundary segments and points. New curvature offsets use the same fade.
        This mode inserts fitted curvature with interpolated source residuals; boundaries and fixed points stay exact.
        It may reduce interior kinks but cannot promise to remove a kink at a fixed boundary. Inspect the reported
        fit and previews. Both modes retain the original
        minimum opposite-surface clearance, measured with an exact graph check; unprovable charts refuse.
          material_adjust takes strength, material_index, values with base_color RGBA, roughness, metallic,
          transmission, coat_weight, coat_roughness, ior or alpha. Select every face using that DATA-linked slot;
          the material is copied so other objects stay unchanged. Linked shader inputs and OBJECT overrides refuse.
          edge_shape round takes strength, mode round, radius_mm, edge_vertex_ids [a,b], segments (2-16).
          It makes a circular fillet of a straight convex edge with planar adjoining faces and one planar end cap
          at each endpoint (three incident faces). Show all adjoining faces/end caps; their planes stay unchanged.
          Mode sharpen takes strength, mode sharpen and smaller radius_mm; it uses the saved authored fillet recipe.
          radius zero restores its original sharp corner. Arbitrary existing arcs, complex corners, material
          boundaries and UV seams refuse. For a generic part give positive minimum_mm in this confirmed preview;
          it is a geometric clearance floor, not a manufacturing safety claim, and cannot weaken a declared floor.
          thickness takes strength and signed offset_mm along area-weighted selected surface normals; unselected
          opposite points stay fixed, and the existing declared minimum thickness and no-crossing checks remain.
          Generic thinning also requires positive minimum_mm. Other generic deformations retain measured original
          minimum clearance. The preview reports the requested and resulting floor before owner confirmation.
          scale_region takes strength, positive factors [x,y,z], anchor_mm. bend_region takes strength, unit axis,
          perpendicular direction, anchor_mm, angle_deg strictly between -90 and90, and positive length_mm;
          its angle has quadratic falloff along positive direction and the shown support weights.
          transform_marking takes strength, surface_object_id, anchor_mm, perpendicular u_axis/normal,
          translate_uv_mm [u,v], positive scale [u,v], angle_deg. Select the complete marking at unit weight;
          its original stand-off follows the named surface, which stays unchanged; a reported minimum outward
          clearance lift can keep long lettering triangles clear. Leaving that surface refuses.
          mesh_cleanup takes strength, welding exact duplicate selected vertices and removing zero-area faces.
          consistent_normals takes strength; select a whole connected closed shell to orient it consistently and
          blend orientation corrections while retaining valid authored smooth and split normals.
          symmetrize takes strength and explicit pairs [[source_vertex_id,destination_vertex_id]] about X=0.
          controlled_decimate takes strength and max_removed_vertices; only planar interior fans without sharp,
          UV, material or marking boundaries can be removed. Curved or protected regions may remain unchanged.
          Every new operation needs the labelled map, plain legend, two or three distinct rendered amounts,
          preview_receipt and owner selection. Topology edits must be last on their object until a fresh map.
        Normal-changing operations mark every shared edge incident to the shown normal influence as sharp for
        independent face normal spaces, preserving existing sharp flags. These flags move no vertices; equal intended normals stay
        continuous. Inspect the added_sharp_edges in normal_fan_encoding before confirmation. Encoded storage
        must match the planned normal fans exactly; decoded vectors must stay within encoding quantization bounds.
        Coordinates are asset millimetres. Positive X is outward only on the positive-X temple. A rigid outer-face
        rotation can make a thin edge; slope changes at fixed length/height preserve a graph-shaped wall.
        Propose only feasible changes, retaining all physical checks; explain blockers in each item's reason.
        The host automatically seals and lists nearby surface markings in following_markings. Those markings
          follow the confirmed surface with their surface-relative offset. Refinement, edge/radius edits, thickness,
          scale, bend and marking transforms can add a measured minimum
          outward clearance lift so a long marking triangle cannot intersect the edited surface between its
          vertices; inspect and report marking_clearance or outward_clearance_lift_mm in the option checks before
          asking for confirmation.
        Ambiguous or unsupported
        attachments refuse. Show this list to the owner as part of understanding, before confirmation.
        """
        return await call(ctx, "understand_finish", lambda cid: understand(scene, cid, map_id, map_sha256,
                                                                          capture_receipts, items, limitations, preview_receipt))

    @tool("edit_report")
    async def edit_report(ctx: ToolContext, summary: str, not_done: list[str]):
        """Draw the before and after at the unchanged photo cameras, with native full pairs, close-ups and worn
        views. Comparisons and close-ups are composed per photo without shrinking pixels; overflow uses more images.
        Report actual displacements, confirmed operation receipts, affected neighbours and undone items.
        Runs unchanged strict export and physical checks. Required before finish of a confirmed edit round."""
        return await call(ctx, "edit_report", lambda cid: change_report(scene, cid, summary, not_done))
    return [understand_finish, edit_report]
