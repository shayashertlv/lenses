"""Owner-controlled lens colours; handles are bound to immutable source bytes."""
from pathlib import Path
import json

from .contracts import APIError, confined, now_iso


class LensColourService:
    def _lens_source(self, key):
        dto = self.delivery(key)
        record = self.get("deliveries", key)
        # Re-read the complete manifest, not only the cached model, before edits.
        loaded = self.core.load_delivery(Path(record["root"]), record["delivery_id"])
        if loaded["glb_sha256"] != dto["glb_sha256"]:
            raise APIError("stale_source", "This version changed. Reload before editing.", 409)
        return dto, record, loaded

    def lens_colour(self, key):
        dto, record, loaded = self._lens_source(key)
        materials = self.core.read_lens_materials(Path(loaded["model"]))
        root = Path(record["root"])
        photos = []
        job_path = root / "job.json"
        if job_path.is_file():
            job = json.loads(job_path.read_text(encoding="utf-8"))
            for photo in job.get("images", []):
                # Intake's upright transport image and hash use the same coordinates
                # in the photo viewer and the eyedropper.
                path = confined(root, photo.get("sent_file") or photo["file"])
                digest = photo.get("sent_sha256") or photo["sha256"]
                url = self.artifacts.register(path, digest)
                photos.append({"url": url, "kind": photo["view_kind"],
                               "side": photo.get("side"), "name": photo.get("original_name", path.name)})
        return {"schema": "g3d.dev_lens_colour/1",
                "source": {name: dto[name] for name in ("delivery_key", "glb_sha256", "model_url")},
                "materials": materials["materials"], "photos": photos}

    def _lens_task(self, key, body, kind):
        if set(body) != {"source_sha256", "materials", "idempotency_key"}:
            raise APIError("invalid_input", "Send the source version and the lens values shown in the panel.")
        dto, record, loaded = self._lens_source(key)
        if body["source_sha256"] != dto["glb_sha256"]:
            raise APIError("stale_source", "This version changed. Reload before editing.", 409)
        rows = body["materials"]
        if not isinstance(rows, list) or not rows:
            raise APIError("invalid_input", "Choose at least one lens material to edit.")
        values = {}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"material_index", "values"}
                    or type(row["material_index"]) is not int or row["material_index"] in values):
                raise APIError("invalid_input", "Each lens must have its own material number and values.")
            values[row["material_index"]] = row["values"]
        # Validate at the boundary before queueing; core repeats this against the
        # verified source inside the detached worker.
        self.core.patch_lens_glb(Path(loaded["model"]), values)
        with self.registry.lock:
            request_key, request = self._request(kind + ":" + key, body)
            if not request.get("task_key"):
                task = {"kind": kind, "phase": "queued", "started_at": None, "progress": "Queued",
                        "result": None, "error": None, "source_root": record["root"],
                        "source_delivery_id": record["delivery_id"], "source_delivery_key": key,
                        "job_id": record["job_id"], "body": body, "created_utc": now_iso(),
                        "offline_test_mode": self.offline}
                task_key = self.registry.put("tasks", task)
                task["task_key"] = task_key
                self.registry.put("tasks", task, task_key)
                request["task_key"] = task_key
                self.registry.put("requests", request, request_key)
            self._launch_apply(request["task_key"])
            return {"task_key": request["task_key"]}

    def lens_preview(self, key, body):
        return self._lens_task(key, body, "lens_preview")

    def lens_save(self, key, body):
        return self._lens_task(key, body, "lens_save")


def execute_lens_task(service, task):
    """Called only after the normal durable task worker has claimed the task."""
    body = task["body"]
    dto, record, loaded = service._lens_source(task["source_delivery_key"])
    if dto["glb_sha256"] != body["source_sha256"]:
        raise APIError("stale_source", "The source version changed. Open it again before saving.", 409)
    values = {row["material_index"]: row["values"] for row in body["materials"]}
    destination = service.config.state_root / "lens-edits" / task["task_key"]
    if task["kind"] == "lens_preview":
        preview = service.core.create_lens_preview(Path(record["root"]), record["delivery_id"],
                                                   values, destination=destination)
        return {"url": preview["url"]}
    # Core recovery authenticates the source/value binding before reusing an
    # interrupted publication; a current pointer by itself is not enough.
    root = Path(service.core.save_lens_edit(Path(record["root"]), record["delivery_id"],
                                           values, destination=destination))
    delivery_id = None
    if root.parent.name == "deliveries":
        delivery_id, root = root.name, root.parent.parent
    delivery_key = service.register_delivery(root, delivery_id, origin="owner_lens_edit", job_id=task["job_id"])
    # Loading authenticates the immutable publication before exposing it.
    service.delivery(delivery_key)
    return {"delivery_key": delivery_key}
