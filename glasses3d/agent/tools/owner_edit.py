"""Two-step owner communication, using actual mesh maps and actual AR evidence."""
from __future__ import annotations

import json
from pathlib import Path

from agents.tool_context import ToolContext
from pydantic import BaseModel, ConfigDict
from PIL import Image, ImageDraw

from .. import edits, procs
from . import model_map, look


class Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: int
    label: str
    interpretation: str
    direction: str
    amount: str
    target_ids: list[str]
    operation_json: str


def understand(scene, call_id, map_id, map_sha256, capture_receipts, items, limitations):
    doc = edits.load(scene.root)
    if not doc or doc["phase"] != "understand":
        raise ValueError("understand_finish is only for an understand round")
    if scene.s.fingerprint() != doc["source"]["fingerprint"]:
        raise ValueError("understand cannot change the delivered model")
    records = [item.model_dump() if hasattr(item, "model_dump") else item for item in items]
    if not records or [r["number"] for r in records] != list(range(1, len(records) + 1)):
        raise ValueError("interpret each item separately with consecutive numbers starting at 1")
    operations = []
    for item in records:
        if any(not item[key].strip() for key in ("label", "interpretation", "direction", "amount")):
            raise ValueError("each item needs its label, interpretation, direction and proposed amount")
        value = json.loads(item["operation_json"])
        group = value if isinstance(value, list) else [value]
        if not group or set(key for op in group for key in op["target_ids"]) != set(item["target_ids"]):
            raise ValueError("interpretation and operation must name the same shown targets")
        operations.extend(group)
    if len({op["id"] for op in operations}) != len(operations):
        raise ValueError("operation IDs must be unique")
    evidence = model_map.validate_understanding(scene.root, map_id, map_sha256, capture_receipts, operations)
    operations = evidence.get("operations", operations)
    if evidence["source"]["fingerprint"] != doc["source"]["fingerprint"]:
        raise ValueError("the shown map is not of the selected delivered checkpoint")
    files = {row["path"]: row["sha256"] for row in evidence["evidence_files"]}
    files[evidence["map_path"]] = evidence["map_sha256"]
    path = scene.root / "work/owner_edit/understanding.json"
    artifact = {"schema": edits.UNDERSTANDING_SCHEMA, "status": "awaiting_owner_confirmation",
                "call_id": call_id, "request_text": doc["request_text"], "source_fingerprint": doc["source"]["fingerprint"],
                "items": records, "operations": operations, "limitations": limitations,
                "snapshot_path": Path(evidence["snapshot_path"]).relative_to(scene.root).as_posix(),
                "map_id": map_id, "map_sha256": map_sha256, "evidence_files": files,
                "capture_receipts": capture_receipts, "geometry_changed": False}
    artifact["scripted"] = bool(getattr(scene, "scripted", False))
    procs.write_json_atomic(path, artifact)
    return {"status": "accepted", "next": "STOP: awaiting owner confirmation; the edit needs its own estimate and approval.",
            "understanding": str(path), "understanding_sha256": edits.sha(path),
            "interpretations": records, "limitations": limitations, "geometry_changed": False,
            "image_delivery": "separate", "images": evidence["images"]}


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
    w, h = opened[0].size
    canvas = Image.new("RGB", (3*w, h+40), "white")
    draw = ImageDraw.Draw(canvas)
    for i, (im, title) in enumerate(zip(opened, ("PHOTO", "BEFORE", "AFTER"))):
        canvas.paste(im, (i*w, 40))
        draw.text((i*w+8, 8), f"{title} | {label}", fill="black", font_size=18)
    canvas.save(target)
    return {"path": str(target), "sha256": edits.sha(target), "view": f"{label}: photo, before, after (native pixels)"}


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
        images.append(_triple(row["photo_path"], prior["drawing_path"], row["drawing_path"],
                              output / (row["ref"] + "-before-after.png"), row["ref"]))
    images += [{"path": before["worn_front_path"], "view": "Before: worn on the test face"},
               {"path": after["worn_front_path"], "view": "After: worn on the test face"}]
    # Native per-feature close-ups remain attached and available without resampling the source views.
    images += [dict(r, view="Before: " + r["view"]) for r in before["images"] if r.get("kind") == "look_detail"]
    images += [dict(r, view="After: " + r["view"]) for r in after["images"] if r.get("kind") == "look_detail"]
    receipts = scene.owner_edit_authority.receipts()
    current_receipts = receipts if receipts and receipts[-1].get("fingerprint") == scene.s.fingerprint() else []
    applied = {key for row in current_receipts for key in row["operation_ids"]}
    artifact = {"schema": "g3d.owner_edit_report/1", "status": "ok", "summary": summary,
                "request_text": doc["request_text"], "confirmation_text": doc["confirmation_text"],
                "fingerprint": scene.s.head["fingerprint"], "checkpoint": scene.s.head["id"],
                "authority_sha256": edits.sha(scene.root / "inputs/owner-edit.json"), "scope_check": guard,
                "operations": current_receipts, "historical_receipts_not_currently_verified": receipts if not current_receipts else [],
                "not_done": not_done,
                "without_operation_receipt": [op["id"] for op in understanding["operations"] if op["id"] not in applied],
                "before": before, "after": after, "images": images, "image_delivery": "separate",
                "side_effects": guard["changes"]}
    path = scene.root / "work/owner_edit/change-report.json"
    procs.write_json_atomic(path, artifact)
    return {**artifact, "report": str(path), "report_sha256": edits.sha(path)}


def tools_for(scene, tool, call):
    @tool("understand_finish")
    async def understand_finish(ctx: ToolContext, map_id: str, map_sha256: str, capture_receipts: list[str],
                                items: list[Interpretation], limitations: list[str]):
        """Stop the understand round after showing each requested change. Supply the successfully shown map
        receipt(s), numbered interpretations, direction and proposed amount, or selected rendered option.
        operation_json names the exact operation and drawn support; it cannot expand any target. List impossible
        or ambiguous requests explicitly. This accepts understanding only, not a product finish or paid edit.

        Each operation_json is one object or a list for mirrored changes: id, kind, target_ids,
        scope_ref_sha256 (a support hash returned by the shown map), and params. An explicit scope object from
        that same map is also accepted. translate_region takes delta_mm [x,y,z]; rotate_region takes
        axis_points_mm [[x,y,z],[x,y,z]] and delta_deg; taper_region takes that axis and delta_mm, ramping from
        zero at the first point to the full displacement at the second, multiplied by the shown support weights.
        Coordinates are asset millimetres. Positive X is outward only on the positive-X temple. A rigid outer-face
        rotation can make a thin edge; slope changes at fixed length/height preserve a graph-shaped wall.
        Propose only feasible changes, retaining all physical checks; state unsupported requests in limitations.
        """
        return await call(ctx, "understand_finish", lambda cid: understand(scene, cid, map_id, map_sha256,
                                                                          capture_receipts, items, limitations))

    @tool("edit_report")
    async def edit_report(ctx: ToolContext, summary: str, not_done: list[str]):
        """Draw the before and after at the unchanged photo cameras, with native full pairs, close-ups and worn
        views. Report actual displacements, confirmed operation receipts, affected neighbours and undone items.
        Runs unchanged strict export and physical checks. Required before finish of a confirmed edit round."""
        return await call(ctx, "edit_report", lambda cid: change_report(scene, cid, summary, not_done))
    return [understand_finish, edit_report]
