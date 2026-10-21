"""Role-derived owner appearance controls over authenticated immutable deliveries."""
from pathlib import Path
import json

from .contracts import APIError, confined, now_iso
from .owner_lists import VersionLabels, unique_materials, version_order


class AppearanceService:
    def appearance(self, key, show_test_jobs=False):
        dto, record, loaded = self._lens_source(key)
        materials = self.core.read_look_materials(Path(loaded["model"]))
        materials["materials"] = unique_materials(materials["materials"])
        refusals = list(materials.get("refusals", []))
        if dto.get("appearance_error"):
            refusals.append({"name": "Saved look", "reason": dto["appearance_error"]})
        root = Path(record["root"])
        cameras = self.core.photo_cameras(root, record["delivery_id"])
        photos = []
        if (root / "job.json").is_file():
            job = json.loads((root / "job.json").read_text(encoding="utf-8"))
            for photo in job.get("images", []):
                path = confined(root, photo.get("sent_file") or photo["file"])
                digest = photo.get("sent_sha256") or photo["sha256"]
                fitted = cameras.get(digest, {})
                photos.append({"url": self.artifacts.register(path, digest), "kind": photo["view_kind"],
                    "side": photo.get("side"), "name": photo.get("original_name", path.name),
                    "camera": fitted.get("camera"), "camera_note": fitted.get("note",
                        "This photo's angle isn't available for this model"),
                    "camera_photo_url": (self.artifacts.register(fitted["photo"], fitted["sha256"])
                                         if fitted.get("photo") else None)})
        # Discover old job deliveries as well as owner versions already in the UI registry.
        jobs = self.jobs(show_test_jobs)["jobs"]
        allowed = {row["delivery_key"]: row for job in jobs for row in job["deliveries"]}
        labels = VersionLabels(self)
        versions, copy_sources = [], []
        for candidate in self.registry.list("deliveries"):
            if candidate["key"] not in allowed:
                continue
            try:
                version = self.delivery(candidate["key"])
            except (OSError, ValueError, KeyError):
                continue
            summary = {**allowed[candidate["key"]], "lens_swatch": labels.swatch(candidate, version)}
            copy_sources.append(summary)
            if candidate["job_id"] == record["job_id"]:
                versions.append(summary)
        return {"schema": "g3d.dev_appearance/1",
            "source": {name: dto[name] for name in ("delivery_key", "glb_sha256", "model_url")},
            "materials": materials["materials"], "refusals": refusals,
            "default_material_index": materials["materials"][0]["material_index"] if materials["materials"] else None,
            "show_test_jobs": bool(show_test_jobs),
            "appearance_error": dto.get("appearance_error"),
            "look_skipped": dto.get("look_skipped", []),
            "roles": [{"role": role, "label": label} for role, label in
                      (("lens", "Lenses"), ("frame", "Frame"), ("temple", "Temples"))
                      if any(row["role"] == role for row in materials["materials"])],
            "photos": photos, "versions": sorted(versions, key=version_order, reverse=True),
            "copy_sources": sorted(copy_sources, key=version_order, reverse=True)}

    def _appearance_values(self, key, body, *, task=False):
        fields = {"source_sha256", "materials"} | ({"idempotency_key"} if task else set())
        if set(body) != fields:
            raise APIError("invalid_input", "Send the source version and the appearance values shown in the page.")
        dto, record, loaded = self._lens_source(key)
        if body["source_sha256"] != dto["glb_sha256"]:
            raise APIError("stale_source", "This version changed. Reload before editing.", 409)
        rows = body["materials"]
        if not isinstance(rows, list) or not rows:
            raise APIError("invalid_input", "Choose at least one material to edit.")
        values = {}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {"material_index", "values"}
                    or type(row["material_index"]) is not int or row["material_index"] in values):
                raise APIError("invalid_input", "Each material needs its own material number and values.")
            values[row["material_index"]] = row["values"]
        return dto, record, loaded, values

    def appearance_model(self, key, body):
        _, _, loaded, values = self._appearance_values(key, body)
        payload, _ = self.core.patch_look_glb(Path(loaded["model"]), values)
        return payload

    def _appearance_task(self, key, body, kind):
        _, record, loaded, values = self._appearance_values(key, body, task=True)
        self.core.patch_look_glb(Path(loaded["model"]), values)
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

    def appearance_preview(self, key, body):
        return self._appearance_task(key, body, "appearance_preview")

    def appearance_save(self, key, body):
        return self._appearance_task(key, body, "appearance_save")

    def appearance_copy(self, key, body):
        if set(body) != {"from_delivery_key", "role", "source_sha256"}:
            raise APIError("invalid_input", "Choose a saved model and the part look to copy.")
        dto, target, loaded = self._lens_source(key)
        if body["source_sha256"] != dto["glb_sha256"]:
            raise APIError("stale_source", "This version changed. Reload before copying a look.", 409)
        if body["role"] not in ("lens", "frame", "temple"):
            raise APIError("invalid_input", "Choose Lenses, Frame or Temples.")
        _, source, original = self._lens_source(body["from_delivery_key"])
        rows = self.core.read_look_materials(Path(original["model"]))["materials"]
        values = {row["material_index"]: {**row["values"], "gradient": row.get("gradient")}
                  for row in rows if row["role"] == body["role"]}
        if not values:
            return {"materials": [], "applied": [], "skipped": [
                {"role": body["role"], "reason": "That model has no matching editable materials."}]}
        look = self.core.build_look(Path(original["model"]), values)
        mapped = self.core.map_look(look, Path(loaded["model"]), mode="copy",
                                    same_job=source["job_id"] == target["job_id"])
        return {"materials": [{"material_index": int(index), "values": value}
                               for index, value in mapped["values"].items()],
                "applied": mapped["applied"], "skipped": mapped["skipped"]}


def execute_appearance_task(service, task):
    body = task["body"]
    _, record, _, values = service._appearance_values(task["source_delivery_key"], body, task=True)
    destination = service.config.state_root / "appearance-edits" / task["task_key"]
    if task["kind"] == "appearance_preview":
        preview = service.core.create_look_preview(Path(record["root"]), record["delivery_id"], values,
                                                   destination=destination)
        return {"url": preview["url"]}
    root = service.core.save_look_edit(Path(record["root"]), record["delivery_id"], values,
                                       destination=destination)
    from .appearance_flow import register_owner_version
    return {"delivery_key": register_owner_version(service, root, task["job_id"])}
