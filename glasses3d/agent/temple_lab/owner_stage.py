"""Owner-confirmed specialist sessions over exact captured meshes, without template rebuilding."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from .. import brief, edits, jobs, owner_edits
from ..instructions import build_instructions, instruction_contract
from ..loop import StageContract, StageRunFailed, StageSpec
from ..procs import write_json_atomic
from ..session_inputs import owner_capture_references
from glasses3d.g3d import owner_mesh as M


def settings(parent, owner_edit):
    from .stage import settings as ordinary_settings
    return replace(ordinary_settings(parent),
                   finish_tool="understand_finish" if owner_edit["phase"] == "understand" else "finish")


def registry(scene, child_settings):
    from ..tools import build_tools
    return [tool for tool in build_tools(scene, child_settings) if tool.name != "temple_stage"]


def contract(job, parent_settings, maximum_usd, owner_edit):
    child_settings = settings(parent_settings, owner_edit)
    tools = registry(SimpleNamespace(root=None, owner_edit=owner_edit), child_settings)
    spec = StageSpec("temple_stage", build_instructions(tools, owner_edit=owner_edit), tools,
                     "Current actual mesh and the host-sealed owner edit round.", child_settings,
                     contract=instruction_contract(tools, owner_edit=owner_edit),
                     contract_brief=brief.host_text(job))
    return StageContract.from_spec(spec, maximum_usd)


def prepare(scene, folder):
    """Copy inputs unchanged; the current captured head is a separate host-only seed."""
    folder = Path(folder).resolve()
    if folder.exists():
        raise ValueError("owner specialist package already exists")
    head = copy.deepcopy(scene.s.head)
    if not head or scene.s.fingerprint() != head["fingerprint"]:
        raise ValueError("owner specialist needs the verified current parent head")
    source = scene.root / "checkpoints" / head["file"]
    if edits.sha(source) != head["sha256"]:
        raise ValueError("owner specialist source checkpoint bytes changed")
    edits._copy_inputs(scene.root, folder)
    for name in ("cameras", "work/model_maps"):
        if (scene.root / name).exists():
            shutil.copytree(scene.root / name, folder / name)
    # Every immutable understanding artifact retains its original relative path and exact bytes.
    for name, digest in scene.owner_edit["immutable_files"].items():
        src, dst = edits.confined(scene.root, name), edits.confined(folder, name)
        if edits.sha(src) != digest:
            raise ValueError("owner specialist authority input changed")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copyfile(src, dst)
        elif edits.sha(dst) != digest:
            raise ValueError("owner specialist copied authority bytes differ")
    captured = folder / "work/owner_stage/source.blend"
    captured.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, captured)
    if edits.sha(captured) != head["sha256"]:
        raise ValueError("captured source copy differs")
    receipts = scene.owner_edit_authority.receipts()
    write_json_atomic(folder / "work/owner_edit/applied.json", receipts)
    write_json_atomic(folder / "work/owner_stage/source.json", {
        "checkpoint": head, "authority_sha256": edits.sha(scene.root / "inputs/owner-edit.json"),
        "inherited_receipts": len(receipts), "seed_sha256": head["sha256"]})
    edits.load(folder)
    return {"head": head, "seed": captured, "inherited_receipts": len(receipts)}


def run(scene, runtime, bridge, call_id, cap_usd, child_settings):
    from ..job_scene import JobScene
    from .stage import money_note, progress, record_failure
    bridge.check()
    folder = scene.root / "work/os" / hashlib.sha256(call_id.encode()).hexdigest()[:12]
    source = prepare(scene, folder)
    child = JobScene(folder, captured_seed=source["seed"], captured_fingerprint=source["head"]["fingerprint"])
    child.scripted = bool(getattr(scene, "scripted", False))
    try:
        child.start()
        bridge.check()
        tools = registry(child, child_settings)
        spec = StageSpec("temple_stage", build_instructions(tools, owner_edit=child.owner_edit), tools,
            brief.build_brief(child.job) + edits.brief_suffix(child.owner_edit), child_settings,
            photos=jobs.references(folder) + owner_capture_references(folder, child.owner_edit),
            scene_note=(f"Private owner-edit specialist: exact current parent head {source['head']['id']}. "
                        "No template or earlier specification has replaced this mesh. "
                        + money_note(runtime, child_settings, cap_usd)),
            contract=instruction_contract(tools, owner_edit=child.owner_edit), contract_brief=brief.host_text(child.job))
        outcome = bridge.run(runtime, spec, child, cap_usd, call_id)
        outcome["progress"] = progress(child, folder, scene.root)
        write_json_atomic(folder / "stage-outcome.json", outcome)
        bridge.check()
        exit_head = outcome.get("exit_checkpoint") or {}
        if (outcome.get("status") != "completed" or exit_head.get("verified") is not True
                or exit_head.get("id") != child.s.head["id"] or exit_head.get("sha256") != child.s.head["sha256"]):
            return {"status": "refused", "code": "E_STAGE_INCOMPLETE", "stage": outcome,
                    "message": "owner specialist did not complete on its verified head; parent is unchanged"}
        if child.owner_edit["phase"] == "understand":
            if child.s.fingerprint() != source["head"]["fingerprint"]:
                raise ValueError("understand specialist changed its source")
            from ..job_run import _publish_result
            published = _publish_result(child.root, call_id, child, outcome)
            if published["status"] not in {"awaiting_owner_confirmation", "diagnostic_only"}:
                return {"status": "refused", "code": "E_STAGE_INCOMPLETE", "stage": outcome,
                        "message": "owner specialist understanding was not successfully delivered; parent is unchanged"}
            understanding = edits.read(published["understanding"])
            return {"status": "ok", "stage": outcome, "parent_unchanged": True,
                    "understanding_status": published["status"], "interpretations": understanding["items"],
                    "limitations": understanding["limitations"], "blocked_items": edits.blocked_items(understanding),
                    "package": folder.relative_to(scene.root).as_posix(),
                    "message": "Private understanding is available for the parent to show; no edit is applied."}
        finished = child.finish
        if (not finished or finished.get("status") != "accepted"
                or finished.get("fingerprint") != child.s.head["fingerprint"]):
            return {"status": "refused", "code": "E_STAGE_INCOMPLETE", "stage": outcome,
                    "message": "owner specialist has no accepted finish on its current mesh"}
        sealed = child.owner_edit_authority.resolve(None, child.s.head)
        target, target_path = owner_edits.snapshot(child, "owner-stage-final")
        if target.get("fingerprint") != child.s.head["fingerprint"] or child.s.fingerprint() != child.s.head["fingerprint"]:
            return {"status": "refused", "code": "E_STAGE_STALE", "stage": outcome,
                    "message": "the private mesh drifted from its accepted verified checkpoint"}
        owner_edits.verify_snapshots(sealed["snapshot"], target, sealed["all_operations"])
        if scene.s.head["id"] != source["head"]["id"] or scene.s.fingerprint() != source["head"]["fingerprint"]:
            return {"status": "refused", "code": "E_STAGE_STALE", "stage": outcome,
                    "message": "the parent changed since this exact owner mesh was captured"}
        current, current_path = owner_edits.snapshot(scene, "owner-stage-parent")
        bridge.check()
        scene._owner_expected_positions = {o["id"]: o["vertices_local"] for o in target["objects"]}
        try:
            applied = scene.s.call_mutating("temple_stage", "owner_edit_apply", {
                "snapshot_path": str(current_path), "snapshot_sha256": M.digest(current),
                "target_snapshot_path": str(target_path), "target_snapshot_sha256": M.digest(target),
                "operations": sealed["all_operations"]}, deadline_s=180, label="Confirmed owner specialist mesh",
                call_id=call_id)
        finally:
            del scene._owner_expected_positions
        outcome["progress"]["parent_geometry_unchanged"] = applied.get("status") != "ok"
        bridge.check()
        if applied.get("status") == "ok":
            # Copy receipt facts only after the validated actual child mesh reached the parent.
            new_receipts = child.owner_edit_authority.receipts()[source["inherited_receipts"]:]
            receipt = {"schema": "g3d.owner_edit_receipt/1", "status": "ok", "call_id": call_id,
                "round_id": sealed["round_id"], "authority_sha256": sealed["authority_sha256"],
                "confirmation_sha256": sealed["confirmation_sha256"],
                "operation_ids": list(dict.fromkeys(i for r in new_receipts for i in r.get("operation_ids", []))),
                "operations": [op for r in new_receipts for op in r.get("operations", [])],
                "checkpoint_before": source["head"]["id"], "checkpoint": scene.s.head["id"],
                "fingerprint": scene.s.head["fingerprint"], "checks": applied.get("owner_scope_check"),
                "child_receipts": new_receipts, "child_fingerprint": child.s.head["fingerprint"],
                "request_text": sealed["request_text"], "confirmation_text": sealed["confirmation_text"]}
            scene.owner_edit_authority.record_applied(receipt)
        result = {**applied, "stage": outcome, "package": folder.relative_to(scene.root).as_posix(),
                  "source": source["head"], "summary": finished.get("summary"), "finish": finished["status"]}
        write_json_atomic(folder / "application.json", result)
        return result
    except StageRunFailed as exc:
        record_failure(exc, child, folder, scene.root)
        raise
    finally:
        child.close()
