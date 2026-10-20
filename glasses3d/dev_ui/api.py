"""Persistent application services. HTTP handlers run these in Starlette's threadpool."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import re
import uuid

from PIL import Image
from glasses3d.agent.procs import write_json_atomic

from .artifacts import Artifacts
from .contracts import APIError, SHA, cutoff, json_object, now_iso, opaque, text_field
from .core_adapter import CoreAdapter
from .labels import label_warnings, item_warnings
from .launcher import has_launch_evidence
from .registry import Registry
from .status import read_run_status, find_run_delivery, _money


def _read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class Service:
    def __init__(self, config, launcher, core=None, task_launcher=None):
        self.config, self.launcher = config, launcher
        self.core = core or CoreAdapter()
        self.registry = Registry(config.state_root)
        self.artifacts = Artifacts(self.registry)
        self.task_launcher = task_launcher

    @property
    def offline(self):
        return self.launcher.offline_test_mode is True

    def get(self, kind, key):
        return self.registry.get(kind, opaque(key))

    def config_dto(self):
        from glasses3d.g3d_host.fit_views import VIEW_KINDS
        return {"schema": "g3d.dev_config/1", "offline_test_mode": self.offline,
                "view_kinds": list(VIEW_KINDS), "sides": ["both", "left", "right", "none"],
                "effort": "max", "edit_effort": "high"}

    def _request(self, scope, body):
        token = text_field(body.get("idempotency_key"), "idempotency_key", nonempty=True)
        if len(token) > 256:
            raise APIError("invalid_input", "idempotency_key is too long.")
        key = hashlib.sha256((scope + "\0" + token).encode()).hexdigest()
        digest = _digest(body)
        try:
            row = self.registry.get("requests", key)
        except KeyError:
            row = {"scope": scope, "digest": digest, "created_utc": now_iso()}
            self.registry.put("requests", row, key)
        if row["digest"] != digest:
            raise APIError("idempotency_conflict", "This button request was already used with different inputs.", 409)
        return key, row

    def _available(self):
        self.launcher.check_available()
        # Cover the gap between CLI detach and acquisition of the shared paid lock.
        for run in self.registry.list("runs"):
            if run.get("launch_requested") and read_run_status(run)["phase"] in ("starting", "running", "finalizing"):
                raise APIError("paid_run_busy", "Another paid run is in progress", 409)

    def _paid(self, request_key, request, *, root, kind, amount, button, job_id, edit_key=None):
        if "run_key" not in request:
            run_key = uuid.uuid4().hex
            run = {"root": str(root), "kind": kind, "cutoff_usd": amount, "job_id": job_id,
                   "run_key": run_key,
                   "run_id": "ui-" + uuid.uuid4().hex[:20], "edit_key": edit_key,
                   "created_utc": now_iso(), "offline_test_mode": self.offline}
            self.registry.put("runs", run, run_key)
            request["run_key"] = run_key
            self.registry.put("requests", request, request_key)
        run = self.get("runs", request["run_key"])
        # A saved pre-upgrade request must satisfy today's effort before a retry
        # can start it. Already-started runs keep their idempotent recovery path.
        if not run.get("prepared") or (not run.get("launched") and not has_launch_evidence(root, run["run_id"])):
            prepared = self.launcher.prepare(Path(root), amount, run_id=run["run_id"], kind=kind)
            run.update(prepared, prepared=True)
            self.registry.put("runs", run, run["run_key"])
        if not run.get("approved"):
            self.launcher.check_available()
            self.launcher.approve(Path(root), run["run_id"], amount, button, edit_key or job_id)
            run["approved"] = True
            self.registry.put("runs", run, run["run_key"])
        if not run.get("launched"):
            run.update(launch_requested=True, error=None)
            self.registry.put("runs", run, run["run_key"])
            try:
                self.launcher.launch(Path(root), run["run_id"])
            except Exception as error:
                # Preserve the exact approval/run for retry. A runner receipt means the
                # launch may have succeeded despite loss of the CLI response.
                if not has_launch_evidence(root, run["run_id"]):
                    run.update(launch_requested=False, error=str(error))
                    self.registry.put("runs", run, run["run_key"])
                raise
            run["launched"] = True
            self.registry.put("runs", run, run["run_key"])
        return run

    def start_job(self, manifest, uploads):
        amount = cutoff(manifest.get("cutoff_usd"))
        description = text_field(manifest.get("description"), "Description", nonempty=True)
        name = text_field(manifest.get("name", ""), "Name")
        specs = text_field(manifest.get("specs_text", ""), "Specifications")
        if specs:
            json_object(specs)
        photos = manifest.get("photos")
        if not isinstance(photos, list) or not photos or len(photos) > 64:
            raise APIError("invalid_photos", "Choose between 1 and 64 photos with view and side labels.")
        views, names, fields = {}, set(), set()
        for row in photos:
            if not isinstance(row, dict):
                raise APIError("invalid_photos", "Each photo needs a filename, view and side.")
            filename = text_field(row.get("filename"), "Filename", nonempty=True)
            # Reject Windows path components/ADS/reserved aliases even on other operating systems.
            if (filename in (".", "..") or re.search(r'[\\/:*?"<>|\x00-\x1f]', filename)
                    or filename.endswith((".", " ")) or re.match(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", filename)):
                raise APIError("invalid_filename", "Photo filenames must have no path parts or reserved characters.")
            field = text_field(row.get("field"), "Photo field", nonempty=True)
            if filename.casefold() in names or field in fields or field not in uploads:
                raise APIError("invalid_photos", "Each photo needs its own unique filename and upload field.")
            names.add(filename.casefold()); fields.add(field)
            if uploads[field][0] != filename:
                raise APIError("invalid_filename", "The uploaded filename must match the photo manifest exactly.")
            if not uploads[field][1] or len(uploads[field][1]) > self.config.max_photo_bytes:
                raise APIError("file_too_large", "A photo is empty or exceeds the upload size limit.", 413)
            if row.get("side") not in ("both", "left", "right", "none"):
                raise APIError("invalid_view", "Choose a side for every photo.")
            from glasses3d.agent.jobs import _view
            view = {"kind": row.get("kind"), "side": None if row["side"] == "none" else row["side"]}
            _view(view, filename)
            views[filename] = view
        if fields != set(uploads):
            raise APIError("invalid_photos", "The photo manifest must describe every upload.")
        binding = {**manifest, "upload_sha256": {k: hashlib.sha256(v[1]).hexdigest() for k, v in uploads.items()}}
        with self.registry.lock:
            key, request = self._request("job", binding)
            if not request.get("root"):
                self._available()
                folder = self.config.state_root / "uploads" / key
                images = folder / "images"
                images.mkdir(parents=True, exist_ok=True)
                for row in photos:
                    (images / row["filename"]).write_bytes(uploads[row["field"]][1])
                (folder / "views.json").write_text(json.dumps(views, ensure_ascii=False), encoding="utf-8")
                specs_path = folder / "specs.json" if specs else None
                if specs_path:
                    specs_path.write_bytes(specs.encode("utf-8"))
                root = self.core.create_job(images=images, views=folder / "views.json", name=name if name.strip() else "Product",
                                            description=description, specs=specs_path, jobs_root=self.config.jobs_root)
                job = _read(Path(root) / "job.json")
                request.update(root=str(root), job_id=job.get("id", Path(root).name))
                self.registry.put("requests", request, key)
                self.registry.put("jobs", {"root": str(root), "job_id": request["job_id"],
                                            "name": name if name.strip() else "Product", "created_utc": job.get("created", now_iso())})
            run = self._paid(key, request, root=request["root"], kind="job", amount=amount,
                             button="Start a paid run", job_id=request["job_id"])
            return {"run_key": run["run_key"], "job_id": run["job_id"], "run_id": run["run_id"], "cutoff_usd": amount}

    def run(self, key):
        record = self.get("runs", key)
        dto = read_run_status(record)
        info = find_run_delivery(record)
        if info:
            dto["delivery_key"] = self.register_delivery(info["root"], info["delivery_id"],
                origin="full_edit_item" if record["kind"] == "full_edit_item" else "run", job_id=record["job_id"])
        return dto

    def stop(self, key):
        record = self.get("runs", key)
        current = self.run(key)
        if current["phase"] in ("ended", "failed"):
            return current
        heartbeat = _read(Path(record["root"]) / "heartbeat.json")
        if heartbeat.get("run_id") and heartbeat["run_id"] != record["run_id"]:
            raise APIError("stale_run", "A different run now owns this job. Open that run to stop it.", 409)
        self.launcher.stop(Path(record["root"]), record["run_id"])
        record["stop_requested"] = True
        self.registry.put("runs", record, key)
        result = self.run(key)
        result["stop_requested"] = True
        return result

    def register_delivery(self, root, delivery_id=None, *, origin="run", job_id=None, applied=None, notes=None):
        root = Path(root).resolve()
        if delivery_id is None:
            delivery_id = _read(root / "deliveries/current.json").get("id")
        if not isinstance(delivery_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", delivery_id):
            raise APIError("delivery_unavailable", "No delivery is available yet.", 409)
        with self.registry.lock:
            existing = self.registry.find("deliveries", root=str(root), delivery_id=delivery_id)
            if existing:
                return existing["key"]
            return self.registry.put("deliveries", {"root": str(root), "delivery_id": delivery_id,
                "origin": origin, "job_id": job_id or root.name, "applied": applied or [], "notes": notes or []})

    def delivery(self, key):
        with self.registry.lock:
            record = self.get("deliveries", key)
            if "dto" not in record or record.get("dto_revision") != 2:
                loaded = self.core.load_delivery(Path(record["root"]), record["delivery_id"])
                report = loaded["report_data"]
                self.artifacts.register(loaded["model"], loaded["glb_sha256"])
                finished = report.get("finished", loaded.get("finished", False)) is True
                checks = [{"name": name, "ok": report.get(name, {}).get("ok"),
                           "detail": report.get(name, {}).get("status", "")}
                          for name in ("validate_spec", "seating", "print_check", "arview")]
                manifest = Path(loaded.get("manifest", Path(loaded["model"]).parent / "manifest.json"))
                created = report.get("created_utc") or _read(manifest).get("created_utc")
                if not created:
                    from datetime import datetime, timezone
                    created = datetime.fromtimestamp(manifest.stat().st_mtime if manifest.exists() else
                        Path(loaded["model"]).stat().st_mtime, timezone.utc).isoformat()
                dto = {"schema": "g3d.dev_delivery/1", "delivery_key": key, "job_id": record["job_id"],
                       "delivery_id": record["delivery_id"], "glb_sha256": loaded["glb_sha256"],
                       "model_url": f"/api/deliveries/{key}/model.glb", "finished": finished,
                       "accepted_finish": report.get("accepted_finish", False),
                       "run_status": report.get("run_status", loaded.get("run_status")),
                       "label": "finished" if finished else "not finished",
                       "run_settled_usd": _money([report.get("cost")], "settled"),
                       "created_utc": created, "checks": checks,
                       "origin": report.get("origin", record["origin"]),
                       "applied": report.get("applied", record["applied"]),
                       "notes": report.get("notes", record["notes"]), "warnings": report.get("warnings", []),
                       "exactness": report.get("exactness"), "side_effects": report.get("side_effects")}
                record.update(dto=dto, dto_revision=2, model=str(loaded["model"]))
                self.registry.put("deliveries", record, key)
            # The artifact cache verifies the model once, then invalidates on file identity/stat changes.
            self.artifacts.read(record["dto"]["glb_sha256"])
            return record["dto"]

    def model(self, key):
        return self.artifacts.read(self.delivery(key)["glb_sha256"])

    def ar(self, key):
        self.delivery(key)
        with self.registry.lock:
            record = self.get("deliveries", key)
            if not record.get("ar_url"):
                result = self.core.review(Path(record["root"]), record["delivery_id"])
                record["ar_url"] = result["url"]
                self.registry.put("deliveries", record, key)
            return {"url": record["ar_url"]}

    def new_draft(self, delivery_key):
        delivery = self.delivery(delivery_key)
        with self.registry.lock:
            source = {"delivery_key": delivery_key, "glb_sha256": delivery["glb_sha256"]}
            # The task has already made the next empty draft. The page's standard
            # create-draft call opens that draft instead of orphaning a duplicate.
            for existing in self.registry.list("drafts"):
                if (existing.get("applied_task_key") and existing["source"] == source
                        and not existing["frozen"] and not existing["items"]):
                    return self._draft_dto(existing)
            row = {"source": source,
                   "items": [], "frozen": False, "next_number": 1, "created_utc": now_iso()}
            key = self.registry.put("drafts", row)
            return self.draft(key)

    def _draft_dto(self, row):
        items = []
        for original in row["items"]:
            item = {k: original[k] for k in ("item_id", "capture_id", "labels", "request_text", "viewer_url", "annotated_url")}
            item["warnings"] = item_warnings(row["items"], item["item_id"])
            items.append(item)
        return {"schema": "g3d.dev_edit_draft/1", "draft_id": row["key"], "source": row["source"],
                "items": items, "labels": [label for item in items for label in item["labels"]], "frozen": row["frozen"]}

    def draft(self, key):
        return self._draft_dto(self.get("drafts", key))

    def _mutable(self, key):
        row = self.get("drafts", key)
        if row["frozen"]:
            raise APIError("draft_frozen", "These edits were already sent. Start a new draft to change them.", 409)
        return row

    def add_item(self, key, capture, viewer, annotated):
        with self.registry.lock:
            draft = self._mutable(key)
            if capture.get("source") != draft["source"]:
                raise APIError("capture_source", "This capture belongs to a different model.", 409)
            text_field(capture.get("request_text"), "Request text")
            marks = capture.get("marks")
            if not isinstance(marks, list) or not marks:
                raise APIError("invalid_capture", "Add at least one labelled mark.")
            labels = [text_field(m.get("label") if isinstance(m, dict) else None, "Label", nonempty=True) for m in marks]
            previous = {label.casefold() for item in draft["items"] for label in item["labels"]}
            if len({label.casefold() for label in labels}) != len(labels) or previous.intersection(label.casefold() for label in labels):
                raise APIError("duplicate_label", "Every mark needs a unique label across this edit session.")
            dimensions = capture.get("image", {})
            if not isinstance(dimensions, dict):
                raise APIError("invalid_capture", "Capture dimensions are invalid.")
            expected = (dimensions.get("width_px"), dimensions.get("height_px"))
            if any(type(n) is not int or n <= 0 for n in expected) or expected[0] * expected[1] > self.config.max_capture_pixels:
                raise APIError("invalid_capture", "Capture dimensions are invalid or too large.")
            for data in (viewer, annotated):
                if len(data) > self.config.max_png_bytes:
                    raise APIError("file_too_large", "A capture PNG exceeds the upload size limit.", 413)
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise APIError("invalid_png", "Captures must be PNG images.")
                try:
                    with Image.open(io.BytesIO(data)) as img:
                        if img.size != expected or getattr(img, "n_frames", 1) != 1:
                            raise ValueError("dimensions")
                        img.verify()
                except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                    raise APIError("invalid_png", "The PNG dimensions must match the capture.") from None
            number = draft["next_number"]
            item_id, capture_id = f"I{number}", f"C{number}"
            if capture.get("capture_id") != capture_id:
                # Captures are client-identified; preserve exact JSON and refuse collisions, not silently rename.
                capture_id = capture.get("capture_id")
                if not isinstance(capture_id, str) or not re.fullmatch(r"C[1-9][0-9]*", capture_id):
                    raise APIError("invalid_capture", "Capture id must be C followed by a positive number.")
            if any(i["capture_id"] == capture_id for i in draft["items"]):
                raise APIError("duplicate_capture", "This capture id is already in the draft.", 409)
            folder = self.config.state_root / "captures" / key / capture_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "viewer.png").write_bytes(viewer)
            (folder / "annotated.png").write_bytes(annotated)
            source = self.get("deliveries", draft["source"]["delivery_key"])
            self.core.validate_capture(capture, folder / "viewer.png", folder / "annotated.png", Path(source["model"]))
            write_json_atomic(folder / "capture.json", capture)
            item = {"item_id": item_id, "capture_id": capture_id, "labels": labels,
                    "request_text": capture["request_text"], "capture_dir": str(folder),
                    "viewer_url": self.artifacts.register(folder / "viewer.png"),
                    "annotated_url": self.artifacts.register(folder / "annotated.png")}
            draft["items"].append(item); draft["next_number"] += 1
            self.registry.put("drafts", draft, key)
            return {"item": self.draft(key)["items"][-1], "warnings": label_warnings(draft["items"])}

    def patch_item(self, key, item_id, body):
        text = text_field(body.get("request_text"), "Request text")
        with self.registry.lock:
            draft = self._mutable(key)
            item = next((i for i in draft["items"] if i["item_id"] == item_id), None)
            if item is None:
                raise KeyError(item_id)
            item["request_text"] = text
            folder = Path(item["capture_dir"])
            payload = _read(folder / "capture.json"); payload["request_text"] = text
            write_json_atomic(folder / "capture.json", payload)
            self.registry.put("drafts", draft, key)
            return {"item": next(i for i in self.draft(key)["items"] if i["item_id"] == item_id),
                    "warnings": label_warnings(draft["items"])}

    def delete_item(self, key, item_id):
        with self.registry.lock:
            draft = self._mutable(key)
            if not any(i["item_id"] == item_id for i in draft["items"]):
                raise KeyError(item_id)
            draft["items"] = [i for i in draft["items"] if i["item_id"] != item_id]
            self.registry.put("drafts", draft, key)
            return self.draft(key)

    def send(self, key, body):
        amount = cutoff(body.get("cutoff_usd"))
        with self.registry.lock:
            request_key, request = self._request("send:" + key, body)
            draft = self.get("drafts", key)
            if not request.get("root"):
                if draft["frozen"] and draft.get("send_request") != request_key:
                    raise APIError("draft_frozen", "These edits were already sent.", 409)
                if not draft["items"]:
                    raise APIError("empty_draft", "Add an edit before sending.")
                self._available()
                draft.update(frozen=True, send_request=request_key)
                self.registry.put("drafts", draft, key)
                source = self.get("deliveries", draft["source"]["delivery_key"])
                items = [{k: i[k] for k in ("item_id", "request_text", "capture_dir")} for i in draft["items"]]
                try:
                    root = self.core.create_capture_understand(Path(source["root"]), source["delivery_id"], items)
                except Exception:
                    draft.update(frozen=False)
                    draft.pop("send_request", None)
                    self.registry.put("drafts", draft, key)
                    raise
                edit_key = self.registry.put("edits", {"root": str(root), "source": draft["source"],
                    "draft_id": key, "job_id": source["job_id"], "created_utc": now_iso()})
                request.update(root=str(root), edit_key=edit_key, job_id=source["job_id"])
                self.registry.put("requests", request, request_key)
            run = self._paid(request_key, request, root=request["root"], kind="understand", amount=amount,
                             button="Send to Astra (paid)", job_id=request["job_id"], edit_key=request["edit_key"])
            edit = self.get("edits", request["edit_key"]); edit["run_key"] = run["run_key"]
            self.registry.put("edits", edit, request["edit_key"])
            return {"run_key": run["run_key"], "edit_key": request["edit_key"]}

    def _image_url(self, image, root):
        if not isinstance(image, dict) or not isinstance(image.get("path"), str) or not SHA.fullmatch(image.get("sha256", "")):
            raise APIError("invalid_evidence", "Preview evidence has no verified image binding.", 409)
        path = Path(image["path"])
        path = path if path.is_absolute() else root / path
        if not path.resolve().is_relative_to(root.resolve()) or path.absolute() != path.resolve():
            raise APIError("invalid_evidence", "Preview evidence is outside the edit round.", 409)
        return self.artifacts.register(path, image["sha256"])

    def understanding(self, key):
        edit = self.get("edits", key)
        root = Path(edit["root"])
        raw = copy.deepcopy(self.core.read_understanding(root))
        draft = self.get("drafts", edit["draft_id"])
        originals = {item["item_id"]: item for item in draft["items"]}
        items = []
        for item in raw.get("items", []):
            original = originals.get(item["item_id"], {})
            options = []
            for option in item.get("options", []):
                images = [{"url": self._image_url(img, root), "view": img.get("view", ""),
                           "panels": img.get("panels", ["photo", "before", "option"])} for img in option.get("images", [])]
                options.append({"option_id": option["option_id"], "label": option.get("label", ""),
                                "amount_text": option.get("amount_text", ""), "images": images})
            items.append({"item_id": item["item_id"], "number": item.get("number"),
                "labels": original.get("labels", item.get("labels", [])), "request_text": original.get("request_text", ""),
                "annotated_url": original.get("annotated_url"), "interpretation": item.get("interpretation", ""),
                "disposition": item["disposition"], "reason": item.get("reason", ""),
                "fallback_allowed": item.get("fallback_allowed", False), "options": options})
        return {"schema": "g3d.dev_understanding/1", "edit_key": key,
            "understanding_sha256": raw["understanding_sha256"], "source": edit["source"],
            "run_settled_usd": _money([{"settled_usd": raw.get("run_settled_usd")}], "settled"), "items": items,
            "map_images": [{"url": self._image_url(img, root), "label": img.get("label", "")} for img in raw.get("map_images", [])],
            "limitations": raw.get("limitations", [])}

    def apply(self, key, body):
        with self.registry.lock:
            request_key, request = self._request("apply:" + key, body)
            if request.get("task_key"):
                self._launch_apply(request["task_key"])
                return {"task_key": request["task_key"]}
            edit = self.get("edits", key)
            understanding = self.understanding(key)
            if body.get("understanding_sha256") != understanding["understanding_sha256"]:
                raise APIError("stale_understanding", "The understanding has changed. Reload before applying.", 409)
            choices, skipped = body.get("choices"), body.get("skipped")
            if not isinstance(choices, list) or not isinstance(skipped, list):
                raise APIError("invalid_choices", "Choose an option or none of these for every item.")
            known = {i["item_id"]: i for i in understanding["items"]}
            seen, confirmations = set(), {}
            for choice in choices:
                if not isinstance(choice, dict) or set(choice) != {"item_id", "option_id"}:
                    raise APIError("invalid_choices", "A pick must name an item and a sealed option.")
                item_id, option_id = choice["item_id"], choice["option_id"]
                if not isinstance(item_id, str) or not isinstance(option_id, str) or item_id in seen or item_id not in known or option_id not in {o["option_id"] for o in known[item_id]["options"]}:
                    raise APIError("invalid_choices", "A chosen option does not belong to this item.")
                seen.add(item_id); confirmations[item_id] = f"{item_id}={option_id}"
            for skip in skipped:
                if not isinstance(skip, dict) or set(skip) != {"item_id", "note"}:
                    raise APIError("invalid_choices", "A skipped item must include its note.")
                item_id, note = skip["item_id"], text_field(skip["note"], "Note")
                if not isinstance(item_id, str) or item_id not in known or item_id in seen:
                    raise APIError("invalid_choices", "Each item must be picked or skipped exactly once.")
                seen.add(item_id); confirmations[item_id] = f'{item_id}=none ("{note}")'
            if seen != set(known):
                raise APIError("invalid_choices", "Choose an option or none of these for every item.")
            confirmation = 'Dev UI: owner pressed "Apply my picks": ' + ", ".join(confirmations[i] for i in known)
            task = {"kind": "apply_picks", "phase": "queued", "started_at": None, "progress": "Queued",
                    "result": None, "error": None, "edit_key": key, "round_root": edit["root"],
                    "job_id": edit["job_id"], "body": body, "confirmation_text": confirmation,
                    "created_utc": now_iso(), "offline_test_mode": self.offline}
            task_key = self.registry.put("tasks", task)
            task["task_key"] = task_key
            self.registry.put("tasks", task, task_key)
            request["task_key"] = task_key
            self.registry.put("requests", request, request_key)
            self._launch_apply(task_key)
            return {"task_key": task_key}

    def _launch_apply(self, key):
        from .tasks import launch_task, reconcile_task
        task = reconcile_task(self, key)
        if task["phase"] == "failed" and task.get("recoverable"):
            task.update(phase="queued", launched=False, error=None)
            self.registry.put("tasks", task, key)
        if (task.get("launched") or task["phase"] != "queued"
                or (task.get("launch_requested_at") and not task.get("recoverable"))):
            return
        if self.task_launcher:
            self.task_launcher(self, key)
        else:
            launch_task(self.config, key)
        task = self.get("tasks", key)
        task["launched"] = True
        self.registry.put("tasks", task, key)

    def task(self, key):
        from datetime import datetime, timezone
        from .tasks import reconcile_task
        task = reconcile_task(self, key)
        elapsed = 0
        if task.get("started_at"):
            end = datetime.fromisoformat(task["ended_at"]) if task.get("ended_at") else datetime.now(timezone.utc)
            elapsed = max(0, (end - datetime.fromisoformat(task["started_at"])).total_seconds())
        return {"schema": "g3d.dev_task/1", "task_key": key, "kind": task["kind"], "phase": task["phase"],
                "started_at": task.get("started_at"), "elapsed_seconds": elapsed, "progress": task.get("progress", ""),
                "result": task.get("result"), "error": task.get("error")}

    def full_edit(self, key, item_id, body):
        amount = cutoff(body.get("cutoff_usd"))
        with self.registry.lock:
            request_key, request = self._request("full:" + key + ":" + item_id, body)
            edit = self.get("edits", key)
            if not request.get("root"):
                understanding = self.understanding(key)
                item = next((i for i in understanding["items"] if i["item_id"] == item_id), None)
                if item is None:
                    raise KeyError(item_id)
                if item["fallback_allowed"] is not True:
                    raise APIError("fallback_unavailable", "This item is not available for a full paid edit.", 409)
                self._available()
                root = self.core.create_full_edit_item(Path(edit["root"]), item_id)
                request["root"] = str(root)
                self.registry.put("requests", request, request_key)
            run = self._paid(request_key, request, root=request["root"], kind="full_edit_item", amount=amount,
                button="Send this item to a full paid edit", job_id=edit["job_id"], edit_key=key)
            return {"run_key": run["run_key"]}

    def jobs(self):
        with self.registry.lock:
            for root in self.config.jobs_root.glob("*"):
                if not root.is_dir() or root.resolve() != root.absolute():
                    continue
                job = _read(root / "job.json")
                if not job.get("id"):
                    continue
                existing = self.registry.find("jobs", root=str(root))
                if not existing:
                    self.registry.put("jobs", {"root": str(root), "job_id": job["id"], "name": job.get("name", "Product"),
                                               "created_utc": job.get("created", "")})
            output = []
            drafts, edits = self.registry.list("drafts"), self.registry.list("edits")
            for job in self.registry.list("jobs"):
                root = Path(job["root"])
                for manifest in (root / "deliveries").glob("*/manifest.json"):
                    if manifest.resolve() != manifest.absolute():
                        continue
                    raw = _read(manifest)
                    if raw.get("id") == manifest.parent.name:
                        self.register_delivery(root, raw["id"], job_id=job["job_id"])
                deliveries = [d for d in self.registry.list("deliveries") if d["job_id"] == job["job_id"]]
                summaries = []
                for d in deliveries:
                    report_path = Path(d["root"]) / "deliveries" / d["delivery_id"] / "report.json"
                    report = d.get("dto") or _read(report_path)
                    from datetime import datetime, timezone
                    created = report.get("created_utc") or (datetime.fromtimestamp(report_path.stat().st_mtime,
                        timezone.utc).isoformat() if report_path.is_file() else "")
                    summaries.append({"delivery_key": d["key"], "label": "finished" if report.get("finished") else "not finished",
                                      "created_utc": created})
                keys = {d["key"] for d in deliveries}
                runs = [r for r in self.registry.list("runs") if r["job_id"] == job["job_id"]]
                output.append({"job_id": job["job_id"], "name": job["name"], "created_utc": job["created_utc"],
                    "deliveries": sorted(summaries, key=lambda d: d["created_utc"], reverse=True),
                    "runs": [{"run_key": r["key"], "kind": r["kind"], "phase": read_run_status(r)["phase"]} for r in runs],
                    "drafts": [{"draft_id": d["key"]} for d in drafts if d["source"]["delivery_key"] in keys],
                    "edits": [{"edit_key": e["key"], "phase": self.run(e["run_key"])["phase"] if e.get("run_key") else "starting"}
                              for e in edits if e["job_id"] == job["job_id"]]})
            return {"jobs": sorted(output, key=lambda j: j["created_utc"], reverse=True)}
