"""First-finish reuse through the real SDK wrappers; fake renders, real file hashes."""
import copy
import json
from pathlib import Path

from PIL import Image
import pytest

from glasses3d.agent.tools import arview, finish_review as F, look
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke


@pytest.fixture
def reusable_scene(finish_scene, monkeypatch):
    current = finish_scene
    current.instruction_profile = "decision-20261021"
    current.active_run_id = "test-finish-reuse"
    current.look_calls = []
    current.source_looks = {}
    current.basis_files = {}
    for name in ("basis", "cameras", "photo-front", "photo-top"):
        path = current.root / "work" / (name + ".json")
        path.write_text(json.dumps({"fixture": name}), encoding="utf-8")
        current.basis_files[name] = path

    def evidence(root, head, export):
        return {"checkpoint": head["id"], "fingerprint": head["fingerprint"],
                "export_sha256": export["sha256"], "basis": "basis-01",
                "basis_sha256": arview.sha256(current.basis_files["basis"]),
                "cameras_sha256": arview.sha256(current.basis_files["cameras"]),
                "photos": [{"ref": ref, "sha256": arview.sha256(current.basis_files["photo-" + ref])}
                           for ref in ("front", "top")]}

    def capture(root, export, cid, refs=None, **kwargs):
        current.look_calls.append(cid)
        binding = evidence(root, current.s.head, export)
        directory = current.root / "work" / ("look-" + cid)
        directory.mkdir()
        rows, images = [], []
        for index, photo in enumerate(binding["photos"]):
            if refs and photo["ref"] not in refs:
                continue
            path = directory / (photo["ref"] + ".png")
            Image.new("RGB", (80, 40), (index * 90, 160, 50)).save(path)
            image = {"path": str(path), "sha256": arview.sha256(path), "view": photo["ref"],
                     "kind": "look_full_pair", "ref": photo["ref"]}
            rows.append({"ref": photo["ref"], "photo_sha256": photo["sha256"], "images": [image],
                         "details": [{"kind": kind, "status": "unavailable", "reason": "not visible",
                                      "images": []} for kind in F.DETAIL_KINDS]})
            images.append(image)
        worn = directory / "worn.png"
        Image.new("RGB", (50, 40), "pink").save(worn)
        images.append({"path": str(worn), "sha256": arview.sha256(worn), "view": "worn front"})
        result = {"status": "ok", **binding, "export_path": export["path"],
                  "views": rows, "images": images, "image_delivery": "separate",
                  "worn_front_path": str(worn), "fixed_scale": True,
                  "assets": [{"path": str(path), "sha256": arview.sha256(path)}
                             for path in directory.glob("*.png")]}
        report = directory / "comparison-report.json"
        report.write_text(json.dumps(result), encoding="utf-8")
        result.update(report=str(report), report_sha256=arview.sha256(report))
        current.source_looks[cid] = copy.deepcopy(result)
        return result

    monkeypatch.setattr(F, "evidence", evidence)
    monkeypatch.setattr(look, "capture", capture)
    return current


def call(current, name, cid, **args):
    output = invoke(current, name, args, cid=cid)
    return current.calls[-1][-1], output


def present(current, result, *, renewed=False):
    """An admitted request has all these exact delivered image bytes in context."""
    current._segment_images = {
        row["sha256"]: {"call_id": "renewal-current" if renewed else result["call_id"],
                        "label": "Renewed evidence" if renewed else "Delivered look"}
        for row in result["images"]}
    current._emitted_images = {sha: record["call_id"] for sha, record in current._segment_images.items()}


def compared(current, **args):
    result, output = call(current, "compare_look_to_photos", "look", **args)
    assert result["status"] == "ok"
    present(current, result)
    return result


def test_first_finish_reuses_delivered_full_look_and_keeps_two_call_review(reusable_scene):
    current = reusable_scene
    compared(current)
    result, output = call(current, "finish", "first", summary="Inspect")
    assert current.look_calls == ["look"], "unchanged delivered look must not be rendered again"
    assert result["status"] == "review_required" and current.finish is None
    assert result["review"]["review_call_id"] == "first"
    assert result["review"]["required_refs"] == ["front", "top"]
    assert result["review"]["evidence_reuse"]["call_id"] == "look"
    assert len([part for part in output if part.type == "image"]) == 1  # fresh AR sheet
    assert any(step[0] == "ar" for step in current.finish_steps)
    final, _ = call(current, "finish", "second", summary="Reviewed", review_call_id="first",
                    per_photo=[{"ref": ref, "differences": "none"} for ref in ("front", "top")])
    assert final["status"] == "accepted"
    assert current.look_calls == ["look", "second"]  # confirmation keeps its original checks


def test_complete_look_carried_into_renewed_context_can_be_reused(reusable_scene):
    current = reusable_scene
    result = compared(current)
    present(current, result, renewed=True)
    finish, _ = call(current, "finish", "first", summary="Inspect")
    assert current.look_calls == ["look"]
    assert finish["review"]["evidence_reuse"]["delivery_calls"] == ["renewal-current"]


@pytest.mark.parametrize("change", ["checkpoint", "fingerprint", "export", "basis", "cameras", "photo-front",
                                    "source_image", "report", "transport_image", "missing_context", "unseen", "run"])
def test_stale_or_undelivered_look_gets_fresh_evidence(reusable_scene, change):
    current = reusable_scene
    delivered = compared(current)
    original = current.source_looks["look"]
    if change in {"checkpoint", "fingerprint"}:
        field = "id" if change == "checkpoint" else change
        current.s.head[field] = "changed"
        current.export[change] = "changed"
    elif change == "export":
        model = Path(current.export["path"])
        model.write_bytes(b"changed export")
        current.export["sha256"] = current.export["validation_sha256"] = arview.sha256(model)
        current.export["print_check"]["glb_sha256"] = current.export["sha256"]
    elif change in current.basis_files:
        current.basis_files[change].write_text("changed", encoding="utf-8")
    elif change == "source_image":
        Path(original["images"][0]["path"]).write_bytes(b"changed source image")
    elif change == "report":
        Path(original["report"]).write_text("changed", encoding="utf-8")
    elif change == "transport_image":
        (current.root / delivered["images"][0]["path"]).write_bytes(b"changed transport bytes")
    elif change == "missing_context":
        current._segment_images.pop(delivered["images"][0]["sha256"])
    elif change == "unseen":
        current.mark_unseen("look")
    else:
        current.active_run_id = "another-run"
    result, _ = call(current, "finish", "first", summary="Inspect")
    assert current.look_calls == ["look", "first"]
    assert "evidence_reuse" not in result.get("review", {})


@pytest.mark.parametrize("condition", ["partial", "no_context", "legacy", "invented_review"])
def test_reuse_requires_full_current_decision_evidence(reusable_scene, condition):
    current = reusable_scene
    if condition == "legacy":
        current.instruction_profile = "legacy-20261021"
    compared(current, **({"refs": ["front"]} if condition == "partial" else {}))
    if condition == "no_context":
        current._segment_images = {}
    args = {"review_call_id": "invented"} if condition == "invented_review" else {}
    result, _ = call(current, "finish", "first", summary="Inspect", **args)
    assert current.look_calls == ["look", "first"]
    assert "evidence_reuse" not in result.get("review", {})
    assert result["status"] == "review_required"


def test_reused_look_never_bypasses_export_or_ar_refusals(reusable_scene):
    current = reusable_scene
    compared(current)
    current.ar_result = {"status": "infrastructure_failed", "code": "E_AR", "images": []}
    result, _ = call(current, "finish", "first", summary="Inspect")
    assert result["status"] == "refused" and result["code"] == "E_AR"
    assert current.finish is None and current.finish_review is None


def test_cached_result_is_a_copy_of_the_delivered_evidence(reusable_scene):
    current = reusable_scene
    delivered = compared(current)
    delivered["views"][0]["photo_sha256"] = "caller changed dictionary"
    result, _ = call(current, "finish", "first", summary="Inspect")
    assert current.look_calls == ["look"]
    assert result["status"] == "review_required"
    assert result["look_comparison"]["views"][0]["photo_sha256"] != "caller changed dictionary"


def test_paged_finish_keeps_the_reused_look_citation_immediate(reusable_scene):
    current = reusable_scene
    compared(current)
    current.export["diagnostic_rows"] = [{"details": "unchanged diagnostic " * 100} for _ in range(40)]
    result, output = call(current, "finish", "first", summary="Inspect")
    shown = json.loads(output[0].text)
    assert "full_report" in shown
    assert shown["review"]["evidence_reuse"]["call_id"] == "look"
    assert shown["review"]["review_call_id"] == "first"
    assert result["status"] == "review_required" and current.look_calls == ["look"]
