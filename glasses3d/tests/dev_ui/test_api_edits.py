"""Edit HTTP contracts with local fakes; no Blender, approval, or model request."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest
from starlette.testclient import TestClient

from glasses3d.dev_ui.contracts import APIError, Config
from glasses3d.dev_ui.server import create_app
from glasses3d.dev_ui.tasks import execute_task


BASE = "http://127.0.0.1:8274"


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _png(size=(16, 10), colour="white"):
    output = io.BytesIO()
    Image.new("RGB", size, colour).save(output, format="PNG")
    return output.getvalue()


class EditLauncher:
    offline_test_mode = True

    def __init__(self):
        self.prepares, self.approvals, self.launches = [], [], []
        self.busy = False

    def check_available(self):
        if self.busy:
            raise APIError("paid_run_busy", "Another paid run is in progress", 409)

    def prepare(self, root, amount, *, run_id, kind):
        self.prepares.append((Path(root), amount, run_id, kind))
        return {"run_id": run_id, "cutoff_usd": amount, "estimate_usd": "1.250000"}

    def approve(self, root, run_id, amount, button, subject):
        self.approvals.append((Path(root), run_id, amount, button, subject))

    def launch(self, root, run_id):
        self.launches.append((Path(root), run_id))
        # Offline fixtures publish immediately. This releases UI startup exclusion
        # before another separately approved fixture round is tested.
        _write(Path(root) / "runs" / run_id / "runner-status.json", {"exit_code": 0})
        if (Path(root) / "work/owner_edit/understanding.json").exists():
            _write(Path(root) / "work/owner_edit/understanding-usage.json", {
                "run_id": run_id, "settled_usd": "1.250000"})
        return {"status": "started", "run_id": run_id}


class EditCore:
    def __init__(self, root):
        self.root = root
        self.deliveries, self.understandings = {}, {}
        self.validated, self.created, self.applied, self.fallbacks = [], [], [], []
        self.reject_validate = False
        self.fail_apply = False

    def make_delivery(self, root, *, identifier="delivery-1", payload=b"glTFsynthetic-source"):
        folder = root / "deliveries" / identifier
        folder.mkdir(parents=True, exist_ok=True)
        model = folder / "model.glb"
        model.write_bytes(payload)
        sha = hashlib.sha256(payload).hexdigest()
        _write(folder / "manifest.json", {"id": identifier, "created_utc": "2026-10-19T00:00:00Z"})
        _write(root / "deliveries/current.json", {"id": identifier, "glb_sha256": sha})
        self.deliveries[(str(root.resolve()), identifier)] = {
            "model": str(model), "manifest": str(folder / "manifest.json"), "glb_sha256": sha,
            "report_data": {"finished": True, "accepted_finish": True, "run_status": "completed",
                "created_utc": "2026-10-19T00:00:00Z", "cost": {"settled_usd": "1.250000"},
                "validate_spec": {"ok": True}, "seating": {"ok": True},
                "print_check": {"ok": True}, "arview": {"ok": True}}}
        return root

    def load_delivery(self, root, delivery_id):
        return copy.deepcopy(self.deliveries[(str(Path(root).resolve()), delivery_id)])

    def validate_capture(self, payload, viewer_png, annotated_png, source_glb):
        if self.reject_validate:
            raise ValueError("The capture anchors do not match the source model")
        assert payload["schema"] == "g3d.owner_capture/1"
        assert payload["source"]["glb_sha256"] == hashlib.sha256(Path(source_glb).read_bytes()).hexdigest()
        assert Path(viewer_png).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert Path(annotated_png).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        self.validated.append(copy.deepcopy(payload))
        return copy.deepcopy(payload)

    def create_capture_understand(self, source_root, delivery_id, items, destination=None):
        self.created.append((Path(source_root), delivery_id, copy.deepcopy(items)))
        root = Path(destination) if destination else self.root / f"understand-{len(self.created)}"
        root.mkdir(parents=True, exist_ok=True)
        evidence = root / "evidence.png"
        evidence.write_bytes(_png(colour="lightblue"))
        image = {"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                 "view": "top", "panels": ["photo", "before", "option"]}
        rows = []
        for number, item in enumerate(items, 1):
            rows.append({"item_id": item["item_id"], "number": number,
                "interpretation": f"Reading item {number}",
                "disposition": "previewed" if number == 1 else "cannot_preview",
                "reason": "" if number == 1 else "No sealed operation for this fixture item",
                "fallback_allowed": number != 1,
                "options": [{"option_id": "I1-small", "label": "Small", "amount_text": "1 mm", "images": [image]},
                            {"option_id": "I1-large", "label": "Large", "amount_text": "3 mm", "images": [image]}]
                           if number == 1 else []})
        raw = {"understanding_sha256": "a" * 64, "run_settled_usd": "1.250000", "items": rows,
               "map_images": [{**image, "label": "Marked model"}], "limitations": ["Offline fixture"]}
        self.understandings[str(root)] = raw
        internal = root / "work/owner_edit/understanding.json"
        _write(internal, {"call_id": "offline-understand"})
        _write(internal.with_name("understanding-delivery.json"), {"delivered": True,
            "call_id": "offline-understand", "sha256": hashlib.sha256(internal.read_bytes()).hexdigest()})
        return root

    def read_understanding(self, root):
        return copy.deepcopy(self.understandings[str(root)])

    def apply_choices(self, root, sha, choices, skipped, confirmation_text, destination=None):
        self.applied.append((Path(root), sha, copy.deepcopy(choices), copy.deepcopy(skipped), confirmation_text))
        if self.fail_apply:
            raise ValueError("The selected operations failed the existing hard check")
        assert destination is not None
        return self.make_delivery(Path(destination), identifier="applied-1", payload=b"glTFsynthetic-applied")

    def create_full_edit_item(self, root, item_id, destination=None):
        self.fallbacks.append((Path(root), item_id))
        result = Path(destination) if destination else self.root / f"full-{len(self.fallbacks)}"
        result.mkdir(parents=True, exist_ok=True)
        return result


@pytest.fixture
def edits(tmp_path):
    config = Config(port=8274, state_root=tmp_path / "state", jobs_root=tmp_path / "jobs",
                    static_root=tmp_path / "static", vendor_root=tmp_path / "vendor")
    core, launcher = EditCore(tmp_path / "core"), EditLauncher()
    tasks = []

    def task_launcher(service, key):
        tasks.append(key)
        return execute_task(service, key)

    app = create_app(config, launcher=launcher, core_adapter=core, task_launcher=task_launcher)
    service = app.state.service
    source = core.make_delivery(tmp_path / "source")
    delivery_key = service.register_delivery(source, "delivery-1", job_id="fixture-job")
    with TestClient(app, base_url=BASE, headers={"Origin": BASE}, raise_server_exceptions=False) as client:
        yield SimpleNamespace(config=config, core=core, launcher=launcher, tasks=tasks, app=app,
                              service=service, client=client, delivery_key=delivery_key, source=source,
                              task_launcher=task_launcher)


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _new(edits):
    return _ok(edits.client.post("/api/edit-drafts", json={"delivery_key": edits.delivery_key}))


def _capture(draft, *, label="A", text="Move A inward.", capture_id="C1"):
    return {"schema": "g3d.owner_capture/1", "capture_id": capture_id, "source": draft["source"],
            "image": {"width_px": 16, "height_px": 10},
            "camera": {"projection": "perspective", "fov_y_deg": 35, "aspect": 1.6,
                "near_m": 0.001, "far_m": 10, "position_m": [0, 0, 0.4],
                "quaternion_xyzw": [0, 0, 0, 1], "target_m": [0, 0, 0],
                "matrix_world": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0.4, 1],
                "projection_matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]},
            "marks": [{"label": label, "kind": "point", "radius_px": 3, "stroke_px": 1,
                "vertices": [{"xy_px": [8, 5], "anchor": {"kind": "background"}}],
                "footprint": {"pixels": 0, "faces": []}}], "request_text": text}


def _add(edits, draft, capture=None, *, viewer=None, annotated=None):
    return edits.client.post(f"/api/edit-drafts/{draft['draft_id']}/items",
        data={"capture": json.dumps(capture if capture is not None else _capture(draft))},
        files={"viewer": ("viewer.png", _png() if viewer is None else viewer, "image/png"),
               "annotated": ("annotated.png", _png(colour="red") if annotated is None else annotated, "image/png")})


def _sent(edits, *, two=True):
    draft = _new(edits)
    _ok(_add(edits, draft))
    if two:
        _ok(_add(edits, draft, _capture(draft, label="B", text="Change B, retaining A.", capture_id="C2")))
    body = {"cutoff_usd": "27.50", "idempotency_key": "send-fixture"}
    result = _ok(edits.client.post(f"/api/edit-drafts/{draft['draft_id']}/send", json=body))
    return draft, result, body


def _apply_body():
    return {"understanding_sha256": "a" * 64, "choices": [{"item_id": "I1", "option_id": "I1-large"}],
            "skipped": [{"item_id": "I2", "note": 'Please try a different "shape".'}],
            "idempotency_key": "apply-fixture"}


def test_draft_add_get_patch_delete_and_reopen(edits):
    draft = _new(edits)
    assert draft["schema"] == "g3d.dev_edit_draft/1"
    assert draft["items"] == [] and draft["labels"] == [] and draft["frozen"] is False
    result = _ok(_add(edits, draft))
    assert result["item"]["item_id"] == "I1"
    assert result["item"]["labels"] == ["A"] and result["warnings"] == []
    for field, expected in (("viewer_url", _png()), ("annotated_url", _png(colour="red"))):
        response = edits.client.get(result["item"][field])
        assert response.status_code == 200 and response.content == expected
        assert response.headers["content-type"] == "image/png"
    text = '  A: owner words unchanged.\nעברית "quotes"  '
    patched = _ok(edits.client.patch(f"/api/edit-drafts/{draft['draft_id']}/items/I1", json={"request_text": text}))
    assert patched["item"]["request_text"] == text
    stored = edits.service.get("drafts", draft["draft_id"])["items"][0]
    assert json.loads((Path(stored["capture_dir"]) / "capture.json").read_text(encoding="utf-8"))["request_text"] == text
    reopened = create_app(edits.config, launcher=edits.launcher, core_adapter=edits.core, task_launcher=edits.task_launcher)
    with TestClient(reopened, base_url=BASE) as client:
        assert _ok(client.get(f"/api/edit-drafts/{draft['draft_id']}"))["items"][0]["request_text"] == text
    assert _ok(edits.client.delete(f"/api/edit-drafts/{draft['draft_id']}/items/I1"))["items"] == []
    assert edits.client.delete(f"/api/edit-drafts/{draft['draft_id']}/items/I1").status_code == 404
    assert edits.client.patch(f"/api/edit-drafts/{draft['draft_id']}/items/I1", json={"request_text": "A"}).status_code == 404


def test_draft_label_warnings_recalculate_across_all_items(edits):
    draft = _new(edits)
    first = _ok(_add(edits, draft, _capture(draft, text="B and Q2 should move; I agree.")))
    assert {(w["code"], w["label"]) for w in first["warnings"]} == {
        ("unknown_label", "B"), ("unknown_label", "Q2"), ("unused_label", "A")}
    second = _ok(_add(edits, draft, _capture(draft, label="B", text="Use A.", capture_id="C2")))
    assert [(w["code"], w["label"]) for w in second["warnings"]] == [("unknown_label", "Q2")]
    blank = _ok(edits.client.patch(f"/api/edit-drafts/{draft['draft_id']}/items/I2", json={"request_text": " \n"}))
    assert ("empty_text", "") in {(w["code"], w["label"]) for w in blank["warnings"]}


@pytest.mark.parametrize(("mutation", "code"), [
    (lambda c: c.update(source={"delivery_key": "otherkey", "glb_sha256": "0" * 64}), "capture_source"),
    (lambda c: c.update(marks=[]), "invalid_capture"),
    (lambda c: c["marks"].append(copy.deepcopy(c["marks"][0])), "duplicate_label"),
    (lambda c: c.update(capture_id="../C1"), "invalid_capture"),
    (lambda c: c.update(image={"width_px": True, "height_px": 10}), "invalid_capture"),
    (lambda c: c.update(image={"width_px": 40_000_001, "height_px": 1}), "invalid_capture"),
    (lambda c: c.update(image=None), "invalid_capture"),
])
def test_capture_refusals_leave_draft_unchanged(edits, mutation, code):
    draft = _new(edits)
    capture = _capture(draft)
    mutation(capture)
    response = _add(edits, draft, capture)
    assert response.status_code in (400, 409), response.text
    assert response.json()["error"]["code"] == code
    assert _ok(edits.client.get(f"/api/edit-drafts/{draft['draft_id']}"))["items"] == []
    assert not edits.launcher.approvals


def test_duplicate_capture_and_session_labels_refuse(edits):
    draft = _new(edits)
    _ok(_add(edits, draft))
    label = _add(edits, draft, _capture(draft, capture_id="C2"))
    assert label.status_code == 400 and label.json()["error"]["code"] == "duplicate_label"
    duplicate = _add(edits, draft, _capture(draft, label="B"))
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "duplicate_capture"


@pytest.mark.parametrize("payload", [b"not png", b"\x89PNG\r\n\x1a\ntruncated", _png((8, 8))])
def test_bad_png_or_mismatched_dimensions_refuse(edits, payload):
    draft = _new(edits)
    response = _add(edits, draft, viewer=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_png"
    assert not edits.core.validated


def test_capture_size_limit_missing_part_and_core_validation_refuse(edits):
    draft = _new(edits)
    edits.config.max_png_bytes = 8
    response = _add(edits, draft)
    assert response.status_code == 413
    edits.config.max_png_bytes = 20 * 1024 * 1024
    missing = edits.client.post(f"/api/edit-drafts/{draft['draft_id']}/items",
        data={"capture": json.dumps(_capture(draft))}, files={"viewer": ("viewer.png", _png(), "image/png")})
    assert missing.status_code == 400 and missing.json()["error"]["code"] == "invalid_form"
    edits.core.reject_validate = True
    refused = _add(edits, draft)
    assert refused.status_code == 400
    assert _ok(edits.client.get(f"/api/edit-drafts/{draft['draft_id']}"))["items"] == []


def test_send_once_survives_restart_and_freezes_mutations(edits):
    draft, sent, body = _sent(edits)
    again = _ok(edits.client.post(f"/api/edit-drafts/{draft['draft_id']}/send", json=body))
    assert again == sent
    reopened = create_app(edits.config, launcher=edits.launcher, core_adapter=edits.core, task_launcher=edits.task_launcher)
    with TestClient(reopened, base_url=BASE, headers={"Origin": BASE}) as client:
        assert _ok(client.post(f"/api/edit-drafts/{draft['draft_id']}/send", json=body)) == sent
    assert len(edits.core.created) == len(edits.launcher.prepares) == len(edits.launcher.approvals) == len(edits.launcher.launches) == 1
    assert edits.launcher.approvals[0][2:4] == ("27.50", "Send to Astra (paid)")
    assert edits.launcher.prepares[0][3] == "understand"
    assert edits.core.created[0][2][1]["request_text"] == "Change B, retaining A."
    frozen = _ok(edits.client.get(f"/api/edit-drafts/{draft['draft_id']}"))
    assert frozen["frozen"] is True
    for response in (_add(edits, draft, _capture(draft, label="C", capture_id="C3")),
                     edits.client.patch(f"/api/edit-drafts/{draft['draft_id']}/items/I1", json={"request_text": "new"}),
                     edits.client.delete(f"/api/edit-drafts/{draft['draft_id']}/items/I1")):
        assert response.status_code == 409 and response.json()["error"]["code"] == "draft_frozen"
    changed = edits.client.post(f"/api/edit-drafts/{draft['draft_id']}/send", json={**body, "cutoff_usd": "40"})
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "idempotency_conflict"
    second_click = edits.client.post(f"/api/edit-drafts/{draft['draft_id']}/send", json={**body, "idempotency_key": "different-click"})
    assert second_click.status_code == 409 and second_click.json()["error"]["code"] == "draft_frozen"


def test_send_empty_and_busy_refuse_before_approval(edits):
    draft = _new(edits)
    path = f"/api/edit-drafts/{draft['draft_id']}/send"
    body = {"cutoff_usd": "5", "idempotency_key": "send-empty"}
    assert edits.client.post(path, json=body).json()["error"]["code"] == "empty_draft"
    _ok(_add(edits, draft))
    edits.launcher.busy = True
    response = edits.client.post(path, json={**body, "idempotency_key": "send-busy"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "paid_run_busy"
    assert not edits.core.created and not edits.launcher.approvals
    assert _ok(edits.client.get(f"/api/edit-drafts/{draft['draft_id']}"))["frozen"] is False


def test_unbuilt_understanding_adapter_leaves_draft_mutable(edits, monkeypatch):
    draft = _new(edits)
    _ok(_add(edits, draft))
    original = edits.core.create_capture_understand

    def not_built(*args, **kwargs):
        raise APIError("not_built_yet", "Capture understanding is not built yet.", 501)

    monkeypatch.setattr(edits.core, "create_capture_understand", not_built)
    body = {"cutoff_usd": "8", "idempotency_key": "retry-after-build"}
    path = f"/api/edit-drafts/{draft['draft_id']}/send"
    failed = edits.client.post(path, json=body)
    assert failed.status_code == 501 and failed.json()["error"]["code"] == "not_built_yet"
    assert _ok(edits.client.get(f"/api/edit-drafts/{draft['draft_id']}"))["frozen"] is False
    updated = "Move A a little farther inward."
    _ok(edits.client.patch(f"/api/edit-drafts/{draft['draft_id']}/items/I1", json={"request_text": updated}))
    assert not edits.launcher.approvals and not edits.core.created
    monkeypatch.setattr(edits.core, "create_capture_understand", original)
    _ok(edits.client.post(path, json=body))
    assert edits.core.created[0][2][0]["request_text"] == updated
    assert len(edits.launcher.approvals) == 1


def test_understanding_registers_images_and_keeps_owner_words(edits):
    draft, sent, _ = _sent(edits)
    dto = _ok(edits.client.get(f"/api/edits/{sent['edit_key']}/understanding"))
    assert dto["schema"] == "g3d.dev_understanding/1" and dto["source"] == draft["source"]
    assert dto["run_settled_usd"] == "1.250000"
    assert dto["items"][1]["request_text"] == "Change B, retaining A."
    assert dto["items"][1]["fallback_allowed"] is True and dto["items"][1]["options"] == []
    image = dto["items"][0]["options"][0]["images"][0]
    assert image["panels"] == ["photo", "before", "option"] and image["view"] == "top"
    assert image["url"].startswith("/api/artifacts/") and "path" not in image
    assert edits.client.get(image["url"]).content == _png(colour="lightblue")
    assert dto["map_images"][0]["url"] == image["url"]
    assert edits.client.get(dto["items"][0]["annotated_url"]).content == _png(colour="red")


@pytest.mark.parametrize("tamper", ["outside", "hash"])
def test_understanding_refuses_outside_or_tampered_evidence(edits, tamper):
    _, sent, _ = _sent(edits)
    root = edits.service.get("edits", sent["edit_key"])["root"]
    image = edits.core.understandings[root]["items"][0]["options"][0]["images"][0]
    if tamper == "outside":
        outside = edits.config.state_root / "outside.png"
        outside.write_bytes(_png(colour="lightblue"))
        image["path"] = str(outside)
    else:
        Path(image["path"]).write_bytes(_png(colour="green"))
    response = edits.client.get(f"/api/edits/{sent['edit_key']}/understanding")
    assert response.status_code in (400, 409), response.text


def test_apply_once_records_confirmation_new_delivery_and_new_draft(edits):
    _, sent, _ = _sent(edits)
    before = (edits.source / "deliveries/delivery-1/model.glb").read_bytes()
    endpoint = f"/api/edits/{sent['edit_key']}/apply"
    body = _apply_body()
    task = _ok(edits.client.post(endpoint, json=body))
    assert _ok(edits.client.post(endpoint, json=body)) == task
    assert len(edits.tasks) == len(edits.core.applied) == 1
    assert edits.core.applied[0][1:4] == ("a" * 64, body["choices"], body["skipped"])
    assert edits.core.applied[0][4] == 'Dev UI: owner pressed "Apply my picks": I1=I1-large, I2=none ("Please try a different "shape".")'
    status = _ok(edits.client.get(f"/api/tasks/{task['task_key']}"))
    assert status["phase"] == "done" and status["error"] is None
    assert status["elapsed_seconds"] >= 0
    delivery = _ok(edits.client.get(f"/api/deliveries/{status['result']['delivery_key']}"))
    assert delivery["origin"] == "applied_picks"
    assert delivery["applied"] == [{"item_id": "I1", "option_id": "I1-large", "amount_text": "3 mm"}]
    assert delivery["notes"] == body["skipped"]
    record = edits.service.get("tasks", task["task_key"])
    draft = _ok(edits.client.get(f"/api/edit-drafts/{record['draft_id']}"))
    assert draft["source"]["delivery_key"] == delivery["delivery_key"] and draft["items"] == []
    assert (edits.source / "deliveries/delivery-1/model.glb").read_bytes() == before
    assert len(edits.launcher.approvals) == 1  # UNDERSTAND only; apply is $0.
    conflict = edits.client.post(endpoint, json={**body, "choices": [{"item_id": "I1", "option_id": "I1-small"}]})
    assert conflict.status_code == 409


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(understanding_sha256="b" * 64),
    lambda b: b.update(choices=[]),
    lambda b: b.update(choices=[{"item_id": "I1", "option_id": "invented"}]),
    lambda b: b.update(choices=[{"item_id": "I1", "option_id": []}]),
    lambda b: b.update(choices=[{"item_id": "I1", "option_id": "I1-large", "operations": []}]),
    lambda b: b.update(skipped=[{"item_id": "I1", "note": "twice"}, {"item_id": "I2", "note": "skip"}]),
    lambda b: b.update(skipped=[{"item_id": "I2", "note": 12}]),
])
def test_apply_refuses_stale_incomplete_unknown_or_injected_picks(edits, mutation):
    _, sent, _ = _sent(edits)
    body = _apply_body()
    mutation(body)
    response = edits.client.post(f"/api/edits/{sent['edit_key']}/apply", json=body)
    assert response.status_code in (400, 409), response.text
    assert not edits.tasks and not edits.core.applied


def test_apply_reports_hard_check_failure_without_delivery(edits):
    _, sent, _ = _sent(edits)
    edits.core.fail_apply = True
    task = _ok(edits.client.post(f"/api/edits/{sent['edit_key']}/apply", json=_apply_body()))
    status = _ok(edits.client.get(f"/api/tasks/{task['task_key']}"))
    assert status["phase"] == "failed" and status["result"] is None
    assert "hard check" in status["error"]
    assert len(edits.service.registry.list("deliveries")) == 1


def test_failed_task_launch_retries_the_saved_task_once(edits):
    _, sent, _ = _sent(edits)
    attempts = []

    def initially_refused(service, key):
        attempts.append(key)
        if len(attempts) == 1:
            raise APIError("launch_failed", "The local task could not start.", 409)
        return execute_task(service, key)

    edits.service.task_launcher = initially_refused
    path = f"/api/edits/{sent['edit_key']}/apply"
    body = _apply_body()
    failed = edits.client.post(path, json=body)
    assert failed.status_code == 409 and failed.json()["error"]["code"] == "launch_failed"
    saved = edits.service.registry.list("tasks")
    assert len(saved) == 1 and saved[0]["phase"] == "queued"
    assert not edits.core.applied
    retried = _ok(edits.client.post(path, json=body))
    assert retried["task_key"] == saved[0]["key"]
    assert attempts == [saved[0]["key"], saved[0]["key"]]
    assert _ok(edits.client.post(path, json=body)) == retried
    assert len(attempts) == 2 and len(edits.core.applied) == 1
    assert _ok(edits.client.get(f"/api/tasks/{retried['task_key']}"))["phase"] == "done"


def test_edit_sends_reject_invalid_cutoffs_before_approval(edits):
    draft = _new(edits)
    _ok(_add(edits, draft))
    refused = edits.client.post(f"/api/edit-drafts/{draft['draft_id']}/send",
        json={"cutoff_usd": "-1", "idempotency_key": "invalid-send"})
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "invalid_cutoff"
    assert not edits.launcher.approvals and not edits.core.created
    _, sent, _ = _sent(edits)
    fallback = edits.client.post(f"/api/edits/{sent['edit_key']}/items/I2/full-edit",
        json={"cutoff_usd": "NaN", "idempotency_key": "invalid-full"})
    assert fallback.status_code == 400 and fallback.json()["error"]["code"] == "invalid_cutoff"
    assert len(edits.launcher.approvals) == 1 and not edits.core.fallbacks


def test_fallback_only_for_allowed_item_and_idempotent(edits):
    _, sent, _ = _sent(edits)
    body = {"cutoff_usd": "31.25", "idempotency_key": "full-fixture"}
    base = f"/api/edits/{sent['edit_key']}/items"
    denied = edits.client.post(base + "/I1/full-edit", json=body)
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "fallback_unavailable"
    missing = edits.client.post(base + "/I9/full-edit", json=body)
    assert missing.status_code == 404
    result = _ok(edits.client.post(base + "/I2/full-edit", json=body))
    assert _ok(edits.client.post(base + "/I2/full-edit", json=body)) == result
    assert len(edits.core.fallbacks) == 1 and edits.core.fallbacks[0][1] == "I2"
    assert len(edits.launcher.approvals) == len(edits.launcher.launches) == 2
    assert edits.launcher.approvals[-1][2:4] == ("31.25", "Send this item to a full paid edit")
    run = _ok(edits.client.get(f"/api/runs/{result['run_key']}"))
    assert run["kind"] == "full_edit_item" and run["cutoff_usd"] == "31.25" and run["offline_test_mode"] is True
    assert [row[3] for row in edits.launcher.prepares] == ["understand", "full_edit_item"]
