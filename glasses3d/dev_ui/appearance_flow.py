"""Keep owner appearance separate from Astra's native scene authority.

Shape captures use the authenticated native delivery. Owner appearance is applied
only after a new native result has passed its own checks, through another checked,
immutable owner publication. No Blender checkpoint is claimed for patched bytes.
"""
from pathlib import Path
import hashlib
import json

from .contracts import APIError, now_iso


OWNER_ORIGINS = {"owner_lens_edit", "owner_look_edit"}


def photo_cameras(core, source_root, delivery_id):
    """Read the delivery's hash-bound frozen cameras; never fit new cameras."""
    from glasses3d.agent.edits import _source_basis
    from glasses3d.g3d_host.ar_camera import explicit_camera
    loaded = core.load_delivery(source_root, delivery_id)
    native_root = Path(source_root)
    try:
        saved = core.read_saved_look(source_root, delivery_id)
        if saved:
            native = core.resolve_native_source(source_root, delivery_id)
            native_root = Path(native["root"])
            loaded = core.load_delivery(native_root, native["delivery_id"])
        basis = _source_basis(native_root, loaded["report_data"])
    except (OSError, KeyError, ValueError):
        # A delivery without verified usable fitted evidence still opens. Never
        # replace missing or damaged frozen evidence with a guessed front view.
        return {}
    result = {}
    for rid, ref in basis.refs.items():
        if not basis.is_usable(rid):
            continue
        try:
            camera = explicit_camera(basis.camera(rid), basis.frame, basis.shape(rid),
                name=rid, background="#ffffff", size_px=1600, temple_pose=basis.pose(rid))
        except (ValueError, KeyError):
            continue
        result[ref["photo_sha256"]] = {"camera": camera, "photo": str(basis.photo_path(rid)),
                                      "sha256": ref["photo_sha256"], "note": "Frozen fitted photo angle"}
    # Intake can hold a byte-distinct upright transport image. The wipe uses the
    # hash-verified fit photo; the normal photo viewer retains intake's upright copy.
    job_path = Path(source_root) / "job.json"
    if job_path.is_file():
        for row in json.loads(job_path.read_text(encoding="utf-8")).get("images", []):
            for digest in (row.get("sha256"), row.get("sent_sha256")):
                if digest in result:
                    for alias in (row.get("sha256"), row.get("sent_sha256")):
                        if alias:
                            result[alias] = result[digest]
                    break
    return result


def native_draft_source(service, delivery_key, dto):
    if dto["origin"] not in OWNER_ORIGINS:
        return delivery_key, None
    record = service.get("deliveries", delivery_key)
    try:
        saved = service.core.read_saved_look(Path(record["root"]), record["delivery_id"])
        native = service.core.resolve_native_source(Path(record["root"]), record["delivery_id"])
    except (AttributeError, OSError, KeyError, ValueError) as error:
        raise APIError("native_edit_unavailable", "This version's original shape model is unavailable. "
                       "You can still edit its look.", 409) from error
    if not saved or not saved.get("look"):
        raise APIError("native_edit_unavailable", "This version has no verified reusable look. "
                       "Open its original shape model to ask Astra.", 409)
    key = service.register_delivery(Path(native["root"]), native["delivery_id"], job_id=record["job_id"])
    native_dto = service.delivery(key)
    if native_dto["origin"] in OWNER_ORIGINS:
        raise APIError("native_edit_unavailable", "The original shape model is unavailable.", 409)
    return key, {"delivery_key": delivery_key, "glb_sha256": dto["glb_sha256"]}


def verify_draft_look(service, draft):
    """Recheck the saved owner appearance before another paid send can start."""
    appearance = draft.get("appearance_source")
    if not appearance:
        return
    dto, _, _ = service._lens_source(appearance["delivery_key"])
    if dto["glb_sha256"] != appearance["glb_sha256"]:
        raise APIError("stale_source", "Your saved look changed. Open the version again before asking Astra.", 409)
    native_key, _ = native_draft_source(service, appearance["delivery_key"], dto)
    if native_key != draft["source"]["delivery_key"]:
        raise APIError("stale_source", "The shape version for this saved look changed. Start a new edit.", 409)


def register_owner_version(service, root, job_id):
    root, delivery_id = Path(root), None
    if root.parent.name == "deliveries":
        delivery_id, root = root.name, root.parent.parent
    key = service.register_delivery(root, delivery_id, origin="owner_look_edit", job_id=job_id)
    service.delivery(key)
    return key


def reapply_owner_look(service, appearance_source, delivery_key, destination):
    """Checked $0 owner publication after native picks or an Astra full edit."""
    before, source, _ = service._lens_source(appearance_source["delivery_key"])
    if before["glb_sha256"] != appearance_source["glb_sha256"]:
        raise APIError("stale_source", "Your saved look changed before it could be reapplied.", 409)
    saved = service.core.read_saved_look(Path(source["root"]), source["delivery_id"])
    if not saved or not saved.get("look"):
        raise APIError("look_unavailable", "Your saved look is unavailable; the new shape version is preserved.", 409)
    _, target, loaded = service._lens_source(delivery_key)
    mapping = service.core.map_look(saved["look"], Path(loaded["model"]), mode="reapply", same_job=True)
    root = service.core.save_look_edit(Path(target["root"]), target["delivery_id"], mapping["values"],
        destination=destination, look=saved["look"], mapping={"applied": mapping["applied"], "skipped": mapping["skipped"]})
    return {"delivery_key": register_owner_version(service, root, target["job_id"]),
            "native_delivery_key": delivery_key, "skipped": mapping["skipped"]}


def record_reapply_failure(service, delivery_key, error, task_key):
    """Keep a completed native result available without claiming its look saved.

    This is UI task metadata, not a rewrite of the immutable native delivery or
    its successful checks. The message follows that version when it is opened.
    """
    service.delivery(delivery_key)
    message = "Your shape changes were saved, but your saved look could not be reapplied: " + str(error)
    with service.registry.lock:
        record = service.get("deliveries", delivery_key)
        record["appearance_issue"] = {"task_key": task_key, "message": message}
        service.registry.put("deliveries", record, delivery_key)
    return message


def reapply_run_result(service, record, dto):
    """Queue once after a paid shape round; polling never does heavy work inline."""
    if record.get("kind") != "full_edit_item" or not record.get("edit_key"):
        return dto
    edit = service.get("edits", record["edit_key"])
    appearance_source = edit.get("appearance_source")
    if not appearance_source:
        return dto
    source_key = dto["delivery_key"]
    key = "look_" + hashlib.sha256((record["key"] + ":" + source_key).encode()).hexdigest()[:32]
    with service.registry.lock:
        try:
            task = service.get("tasks", key)
        except KeyError:
            task = {"task_key": key, "kind": "appearance_reapply", "phase": "queued", "started_at": None,
                "progress": "Reapplying your saved look", "result": None, "error": None, "body": {},
                "appearance_source": appearance_source, "native_delivery_key": source_key,
                "job_id": record["job_id"], "created_utc": now_iso(), "offline_test_mode": service.offline}
            service.registry.put("tasks", task, key)
        if task["phase"] not in ("done", "failed") or task.get("recoverable"):
            service._launch_apply(key)
        task = service.task(key)
    dto["appearance_task_key"] = key
    dto["native_delivery_key"] = source_key
    if task["phase"] == "done":
        dto["delivery_key"] = task["result"]["delivery_key"]
        dto["look_skipped"] = task["result"].get("skipped", [])
    elif task["phase"] == "failed":
        dto["appearance_error"] = record_reapply_failure(service, source_key, task["error"] or "unknown error", key)
    else:
        dto["phase"] = "finalizing"
    return dto
