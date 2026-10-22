"""Decision-profile image reuse in nested and standalone temple tool outputs."""
import copy
import hashlib
import json
import threading
from types import MethodType, SimpleNamespace

from PIL import Image
import pytest

from glasses3d.agent import image_delivery
from glasses3d.agent.temple_lab import tools as T
from glasses3d.agent.temple_lab.stage import StageLab
from glasses3d.tests.agent.test_temple_lab_v3 import FakeLab, call


@pytest.fixture(params=[False, True], ids=["standalone", "nested"])
def lab(tmp_path, request):
    result = FakeLab()
    result.root = tmp_path.resolve()
    result.state_lock = threading.RLock()
    result.state = {"images": {}}
    result.s = SimpleNamespace(head={"id": "c0001"})
    result.save = lambda: None
    result.delivered = []
    result.mark_delivered = lambda cid, name: result.delivered.append(cid)
    if request.param:
        result.tool_output = MethodType(StageLab.tool_output, result)
    paths = []
    for name, colour in [("one", "red"), ("two", "blue")]:
        path = tmp_path / (name + ".png")
        Image.new("RGB", (60, 40), colour).save(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result.state["images"][digest] = {"kind": "compare", "revision": 1, "photo": name}
        paths.append(str(path))
    result.paths = paths
    result.result = {"status": "ok", "images": paths[:1]}
    return result


def invoke(lab, name="photo_compare", cid="call", **kwargs):
    args = {"photos": None, "crop": None, "zoom": None, "points_px": None}
    if name == "photo_image":
        args = {"photo": "01", "crop": None, "enhance": False, "scale": 1}
    elif name == "facet_crease_compare":
        args = {"parent_revision": 0, "facets": ["crown", "logo"], "moving_facet": "crown",
                "shape": "ridge", "angles_deg": [4, 8, 12], "photos": ["01"], "judgement": None}
    args.update(kwargs)
    return call({tool.name: tool for tool in T.registry(lab)}[name], args, call_id=cid)


def images(output):
    return [part for part in output if getattr(part, "type", None) == "image"]


def present(lab, output, cid="first"):
    parts = [{"type": "input_image", "image_url": part.image_url, "detail": part.detail}
             for part in images(output)]
    image_delivery.sync_context(lab, [{"type": "function_call_output", "call_id": cid, "output": parts}])


def test_stage_duplicate_is_reference_with_exact_original_bytes(lab):
    first = invoke(lab, cid="first")
    prior = copy.deepcopy([part.model_dump() for part in first])
    present(lab, first)
    second = invoke(lab, cid="second")
    assert images(first) and not images(second)
    assert any("delivered in call first, still above" in getattr(part, "text", "") for part in second)
    assert [part.model_dump() for part in first] == prior
    if hasattr(lab, "tool_output"):
        receipt = json.JSONDecoder().raw_decode(second[0].text)[0]["image_receipt"]
        assert receipt["output_index"] is None and receipt["reused_from_call"] == "first"
        assert receipt["panels"] and receipt["sha256"]
        assert lab.state["stage_images"][-1]["output_index"] is None
        assert lab.state["stage_images"][-1]["reused_from_call"] == "first"


def test_stage_renewal_carries_then_drops_exact_membership(lab):
    first = invoke(lab, cid="first")
    present(lab, first, "renewal-evidence")
    assert not images(invoke(lab, cid="after-renewal"))
    image_delivery.sync_context(lab, [])
    fresh = invoke(lab, cid="fresh-segment")
    assert [part.image_url for part in images(fresh)] == [part.image_url for part in images(first)]


def test_photo_image_explicit_read_always_redelivers(lab):
    first = invoke(lab, "photo_image", "first")
    present(lab, first)
    second = invoke(lab, "photo_image", "requested-again")
    assert [part.image_url for part in images(second)] == [part.image_url for part in images(first)]


def test_changed_image_bytes_are_always_delivered(lab):
    first = invoke(lab, cid="first")
    present(lab, first)
    Image.new("RGB", (60, 40), (254, 0, 0)).save(lab.paths[0])
    second = invoke(lab, cid="changed")
    assert images(second) and images(second)[0].image_url != images(first)[0].image_url


def test_native_crease_mixed_and_all_references_keep_delivery_and_receipts(lab):
    first = invoke(lab, "facet_crease_compare", "first")
    present(lab, first)
    lab.result["images"] = lab.paths
    second = invoke(lab, "facet_crease_compare", "mixed")
    assert len(images(second)) == 1
    assert "mixed" in lab.delivered and "mixed" not in lab.unseen
    if hasattr(lab, "tool_output"):
        records = [record for record in lab.state["stage_images"] if record["call_id"] == "mixed"]
        assert len(records) == 2 and records[0]["output_index"] is None
        assert getattr(second[records[1]["output_index"]], "type", None) == "image"
    present(lab, first + second, "renewal-images")
    third = invoke(lab, "facet_crease_compare", "referenced")
    assert not images(third)
    assert "referenced" in lab.delivered and "referenced" not in lab.unseen


def test_output_failure_marks_unseen_and_never_suppresses_a_retry(lab, monkeypatch):
    real_limit = T.limit_output
    monkeypatch.setattr(T, "limit_output", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("report failed")))
    invoke(lab, cid="failed")
    assert "failed" in lab.unseen
    monkeypatch.setattr(T, "limit_output", real_limit)
    assert images(invoke(lab, cid="retry"))


def test_stage_legacy_keeps_exact_encoded_output(lab):
    lab.instruction_profile = "legacy-20261021"
    first = invoke(lab, cid="first")
    present(lab, first)
    expected = (lab.tool_output(copy.deepcopy(lab.result), T.footer_text(), "again", "photo_compare")
                if hasattr(lab, "tool_output") else T.output(copy.deepcopy(lab.result), T.footer_text()))
    second = invoke(lab, cid="again")
    assert [part.model_dump() for part in second] == [part.model_dump() for part in expected]
