"""Real SDK finish procedure with local evidence fakes; no network or renderer."""
import copy
from types import SimpleNamespace

from PIL import Image
import pytest

from glasses3d.agent import tools as T
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.tools import finish_review as F, look
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record


@pytest.fixture
def review_scene(finish_scene, monkeypatch):
    scene = finish_scene
    scene.binding = {"basis": "basis-001", "basis_sha256": "basis-bytes", "cameras_sha256": "camera-bytes",
                     "photos": [{"ref": "front", "sha256": "photo-front"}, {"ref": "top", "sha256": "photo-top"}]}
    def evidence(root, head, export):
        return {"checkpoint": head["id"], "fingerprint": head["fingerprint"], "export_sha256": export["sha256"],
                **copy.deepcopy(scene.binding)}
    monkeypatch.setattr(F, "evidence", evidence)
    scene.look_calls = []
    def capture(root, export, cid, **kw):
        scene.look_calls.append(kw)
        rows, images = [], []
        for n, photo in enumerate(scene.binding["photos"]):
            path = scene.root / "work" / f"full-{photo['ref']}.png"
            Image.new("RGB", (1600, 460), (n * 80, 170, 50)).save(path)
            full = {"path": str(path), "kind": "look_full_pair", "ref": photo["ref"], "view": photo["ref"]}
            rows.append({"ref": photo["ref"], "photo_sha256": photo["sha256"], "images": [full],
                         "details": [{"kind": kind, "status": "unavailable", "reason": "not visible", "images": []}
                                     for kind in F.DETAIL_KINDS]})
            images.append(full)
        worn = scene.root / "work/worn.png"
        Image.new("RGB", (600, 380), "pink").save(worn)
        images.append({"path": str(worn), "view": "worn front"})
        return {"status": "ok", "export_sha256": export["sha256"], **copy.deepcopy(scene.binding),
                "views": rows, "images": images, "worn_front_path": str(worn)}
    monkeypatch.setattr(look, "capture", capture)
    return scene


def final_args(call="first"):
    return {"summary": "Reviewed photos", "review_call_id": call,
            "per_photo": [{"ref": "front", "differences": "none"},
                          {"ref": "top", "differences": "Arm tips remain too long; reporting the difference."}]}


def first(scene, **args):
    return record(invoke(scene, "finish", {"summary": "Inspect", **args}, cid="first"))


def test_actual_second_sdk_call_accepts_with_differences_not_a_likeness_gate(review_scene):
    scene = review_scene
    output = first(scene)
    assert output["status"] == "review_required" and scene.finish is None
    assert output["review"]["required_refs"] == ["front", "top"]
    assert output["review"]["review_call_id"] == "first"
    assert len(output["images"]) == 4 and output["images"][1]["size_px"] == [1600, 460]
    assert "front_only" not in scene.look_calls[0]
    final = record(invoke(scene, "finish", final_args(), cid="second"))
    assert final["status"] == "accepted" and scene.finish == final
    assert final["review"]["per_photo"] == final_args()["per_photo"]


def test_cannot_preempt_first_delivery_with_an_invented_review_id(review_scene):
    out = first(review_scene, **{key: value for key, value in final_args("first").items() if key != "summary"})
    assert out["status"] == "review_required" and review_scene.finish is None


@pytest.mark.parametrize("rows", [None, [], [{"ref": "front", "differences": "none"}],
    [{"ref": "front", "differences": "none"}, {"ref": "front", "differences": "none"}],
    [{"ref": "front", "differences": "none"}, {"ref": "top", "differences": "  "}],
    [{"ref": "front", "differences": "none"}, {"ref": "other", "differences": "none"}],
    [{"ref": "front", "differences": "none"}, {"ref": "top", "differences": "none"},
     {"ref": "other", "differences": "none"}]])
def test_missing_duplicate_blank_or_extra_photo_cannot_finish(review_scene, rows):
    first(review_scene)
    out = record(invoke(review_scene, "finish", {**final_args(), "per_photo": rows}, cid="second"))
    assert out["status"] == "refused" and out["code"] == "E_FINISH_REVIEW" and review_scene.finish is None


def test_wrong_review_id_and_same_call_id_cannot_finish(review_scene):
    first(review_scene)
    assert record(invoke(review_scene, "finish", final_args("unknown"), cid="second"))["status"] == "refused"
    assert record(invoke(review_scene, "finish", final_args(), cid="first"))["status"] == "review_required"
    assert review_scene.finish is None


@pytest.mark.parametrize("change", ["fingerprint", "export", "basis", "cameras", "photo", "unseen"])
def test_changed_or_unseen_evidence_requires_a_new_first_call(review_scene, change):
    scene = review_scene
    first(scene)
    if change == "fingerprint":
        scene.s.head["fingerprint"] = scene.export["fingerprint"] = "new fingerprint"
    elif change == "export":
        from pathlib import Path
        Path(scene.export["path"]).write_bytes(b"changed export")
        scene.export["sha256"] = scene.export["validation_sha256"] = T.arview.sha256(Path(scene.export["path"]))
        scene.export["print_check"]["glb_sha256"] = scene.export["sha256"]
    elif change == "basis":
        scene.binding["basis_sha256"] = "changed basis"
    elif change == "cameras":
        scene.binding["cameras_sha256"] = "changed cameras"
    elif change == "photo":
        scene.binding["photos"][0]["sha256"] = "changed photo"
    else:
        scene.mark_unseen("first")
    out = record(invoke(scene, "finish", final_args(), cid="second"))
    assert out["status"] == "review_required" and scene.finish is None
    assert scene.finish_review["call_id"] == "second"


@pytest.mark.parametrize("defect", ["capture", "missing_photo", "missing_detail", "missing_pair", "missing_worn", "wrong_photo", "undelivered_detail"])
def test_usable_photo_evidence_failure_cannot_claim_review_complete(review_scene, monkeypatch, defect):
    capture = look.capture
    def damaged(*args, **kwargs):
        result = capture(*args, **kwargs)
        if defect == "capture":
            return {"status": "unavailable", "images": []}
        if defect == "missing_photo":
            result["views"].pop()
        elif defect == "missing_detail":
            result["views"][0]["details"].pop()
        elif defect == "missing_pair":
            result["views"][0]["images"] = []
        elif defect == "missing_worn":
            result.pop("worn_front_path")
        elif defect == "wrong_photo":
            result["views"][0]["photo_sha256"] = "wrong"
        elif defect == "undelivered_detail":
            result["views"][0]["details"][0].update(status="visible", images=[{"path": "unemitted-native-detail.png"}])
        return result
    monkeypatch.setattr(look, "capture", damaged)
    out = first(review_scene)
    assert out["status"] == "refused" and out["code"] == "E_FINISH_REVIEW"
    assert review_scene.finish_review is None and review_scene.finish is None


def test_no_usable_cameras_still_requires_second_explicit_empty_list(finish_scene):
    scene = finish_scene
    out = first(scene)
    assert out["status"] == "review_required" and out["review"]["required_refs"] == []
    assert out["review"]["unavailable"]
    out = record(invoke(scene, "finish", {"summary": "unavailable", "review_call_id": "first"}, cid="missing"))
    assert out["status"] == "refused"
    out = record(invoke(scene, "finish", {"summary": "unavailable", "review_call_id": "first", "per_photo": []}, cid="second"))
    assert out["status"] == "accepted"


def test_output_failure_or_unseen_output_does_not_store_a_review_receipt(review_scene, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("cannot persist bounded output")
    monkeypatch.setattr(T, "limit_output", broken)
    output = invoke(review_scene, "finish", {"summary": "Inspect"}, cid="first")
    assert isinstance(output, str) and "cannot persist" in output
    assert review_scene.finish_review is None and review_scene.finish is None
    assert "first" in review_scene.unseen
    def unseen(root, cid, name, output):
        review_scene.mark_unseen(cid)
        return output
    monkeypatch.setattr(T, "limit_output", unseen)
    assert first(review_scene)["status"] == "review_required"
    assert review_scene.finish_review is None and review_scene.finish is None


def test_failed_comparison_output_cannot_hide_undelivered_images_from_finish(review_scene, monkeypatch):
    original_capture, original_limit = look.capture, T.limit_output
    def separate_capture(*args, **kwargs):
        return {**original_capture(*args, **kwargs), "image_delivery": "separate"}
    def fail_comparison(root, call_id, name, output):
        if name == "compare_look_to_photos":
            raise OSError("failed to persist complete comparison report")
        return original_limit(root, call_id, name, output)
    monkeypatch.setattr(look, "capture", separate_capture)
    monkeypatch.setattr(T, "limit_output", fail_comparison)
    failed = invoke(review_scene, "compare_look_to_photos", {}, cid="failed-look")
    assert isinstance(failed, str) and "failed to persist" in failed
    assert "failed-look" in review_scene.unseen
    output = invoke(review_scene, "finish", {"summary": "Inspect all photos"}, cid="first")
    result = record(output)
    assert result["status"] == "review_required"
    assert not any(image.get("reused_from_call") == "failed-look" for image in result["images"])
    assert sum(part.type == "image" for part in output) == 4


@pytest.mark.parametrize("phase", ["review", "accept"])
def test_failed_finish_journal_cannot_leave_accepted_or_review_state(review_scene, monkeypatch, phase):
    if phase == "accept":
        first(review_scene)
    original_record = review_scene.record_call
    def broken_journal(*args, **kwargs):
        raise OSError("finish journal was not persisted")
    monkeypatch.setattr(review_scene, "record_call", broken_journal)
    args = final_args() if phase == "accept" else {"summary": "Inspect"}
    failed = invoke(review_scene, "finish", args, cid="failed-finish")
    assert isinstance(failed, str) and "journal was not persisted" in failed
    assert "failed-finish" in review_scene.unseen
    assert review_scene.finish_review is None and review_scene.finish is None
    monkeypatch.setattr(review_scene, "record_call", original_record)
    next_result = record(invoke(review_scene, "finish", final_args("failed-finish"), cid="retry"))
    assert next_result["status"] == "review_required"
    assert not any(image.get("reused_from_call") == "failed-finish" for image in next_result["images"])


def test_unseen_cleanup_failure_preserves_the_original_delivery_error(review_scene, monkeypatch):
    def broken_output(*args, **kwargs):
        raise OSError("original lossless report failure")
    def broken_cleanup(call_id):
        review_scene.unseen.append(call_id)
        raise OSError("secondary unseen-state persistence failure")
    monkeypatch.setattr(T, "limit_output", broken_output)
    monkeypatch.setattr(review_scene, "mark_unseen", broken_cleanup)
    output = invoke(review_scene, "finish", {"summary": "Inspect"}, cid="failed")
    assert isinstance(output, str) and "original lossless report failure" in output
    assert "failed" in review_scene.unseen
    assert review_scene.finish_review is None and review_scene.finish is None


def test_sdk_review_receipt_survives_reload_and_second_call_accepts(review_scene):
    fake = review_scene
    durable = JobScene(fake.root, job={})
    fake.s.index = durable.s.index
    durable.s, durable.seeing = fake.s, fake.seeing
    initial = first(durable)
    assert initial["status"] == "review_required"
    reopened = JobScene(fake.root, job={})
    assert reopened.finish_review == durable.finish_review
    assert reopened.finish is None
    reopened.s, reopened.seeing = fake.s, fake.seeing
    final = record(invoke(reopened, "finish", final_args(), cid="second"))
    assert final["status"] == "accepted"
    assert JobScene(fake.root, job={}).finish["review"]["review_call_id"] == "first"


@pytest.mark.parametrize("change", ["mutate", "fit", "unseen", "new_run"])
def test_durable_review_invalidates_on_scene_events_and_run_boundary(tmp_path, change):
    scene = JobScene(tmp_path, job={})
    scene.finish_review = {"call_id": "first", "binding": {"fingerprint": "f"}}
    scene.save()
    assert JobScene(tmp_path, job={}).finish_review == scene.finish_review
    if change == "mutate":
        scene.record_call("mutation", "execute_blender_code", "c1", "c2", {})
    elif change == "fit":
        scene.record_call("fit", "fit_cameras", "c1", "c1", {})
    elif change == "unseen":
        scene.mark_unseen("first")
    else:
        scene.bind_review_log(SimpleNamespace())
    assert scene.finish_review is None and JobScene(tmp_path, job={}).finish_review is None


def test_late_first_result_cannot_restore_an_unseen_review_receipt(tmp_path):
    scene = JobScene(tmp_path, job={})
    scene.mark_unseen("first")
    scene.finish_review = {"call_id": "first", "binding": {"fingerprint": "f"}}
    scene.record_call("first", "finish", "c1", "c1", {"status": "review_required"})
    assert scene.finish_review is None and JobScene(tmp_path, job={}).finish_review is None
