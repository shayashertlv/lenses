"""Durable, byte-exact scene evidence and finish continuity across conversation renewal."""
import asyncio
import copy
import hashlib
import threading
from types import SimpleNamespace

from agents.tool import ToolOutputImage
from PIL import Image
import pytest

from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.loop import BlobSession, plan_rollback
from glasses3d.agent.receipts import BlobStore
from glasses3d.agent.temple_lab.lab import Lab
from glasses3d.tests.agent.test_job_tools import scene, finish_scene, invoke, record
from glasses3d.tests.agent.test_finish_review import review_scene, final_args


def image(root, name, **meta):
    path = root / "work" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Distinct bytes, including dimensions larger than a thumbnail; renewal must
    # neither decode nor re-encode them.
    Image.new("RGB", (1660, 1030), (len(name), sum(map(ord, name)) % 255, 60)).save(path)
    return {"path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "call_id": name, "checkpoint": "c0001", "view": "front", **meta}


def test_job_keeps_latest_full_call_per_photo_with_every_native_page_and_pick(tmp_path):
    obj = JobScene(tmp_path, job={})
    rows = [image(tmp_path, "old", kind="look_composed", ref="front"),
            image(tmp_path, "side", kind="compare_to_photos", view="side"),
            image(tmp_path, "new-full-1", kind="look_full_pair", ref="front", call_id="new"),
            image(tmp_path, "new-full-2", kind="look_full_pair", ref="front", call_id="new"),
            image(tmp_path, "new-detail", kind="look_detail", ref="front", call_id="new"),
            image(tmp_path, "new-surface", kind="surface_detail", ref="front", call_id="new"),
            image(tmp_path, "old-pick", kind="photo_read", view="corner"),
            image(tmp_path, "pick", kind="photo_read", view="corner"),
            image(tmp_path, "render", kind="render_views", view="front"),
            image(tmp_path, "unseen", kind="look_composed", ref="front")]
    obj.images = rows
    obj._unseen.add("unseen")
    obj.save()
    before = copy.deepcopy(rows)
    output = obj.renewal_evidence()
    assert {r["call_id"] for r in output} == {"side", "new", "pick"}
    assert len(output) == 6  # both full tiles, both detail types, side, pick
    assert rows == before
    for rec in output:
        assert Image.open(rec["path"]).size == (1660, 1030)
        assert hashlib.sha256(open(rec["path"], "rb").read()).hexdigest() == rec["sha256"]
    obj.renewal_started()
    assert JobScene(tmp_path, job={}).renewal_evidence() == output


def test_job_zoom_only_does_not_replace_full_comparison_and_compact_sheet_stays_whole(tmp_path):
    obj = JobScene(tmp_path, job={})
    old = image(tmp_path, "full-sheet", kind="compare_to_photos", view="front, side",
                panels=[{"view": "front"}, {"view": "side"}])
    new = image(tmp_path, "new-front", kind="compare_to_photos", view="front")
    crop = image(tmp_path, "zoom", kind="compare_to_photos", view="front crop 1")
    obj.images = [old, new, crop]
    assert {r["call_id"] for r in obj.renewal_evidence()} == {"full-sheet", "new-front"}


@pytest.mark.parametrize("defect", ["missing", "changed", "escape"])
def test_job_refuses_corrupt_or_unconfined_retained_evidence(tmp_path, defect):
    obj = JobScene(tmp_path, job={})
    row = image(tmp_path, "full", kind="look_composed", ref="front")
    obj.images = [row]
    if defect == "missing":
        (tmp_path / row["path"]).unlink()
    elif defect == "changed":
        (tmp_path / row["path"]).write_bytes(b"changed")
    else:
        row["path"] = "../outside.png"
    with pytest.raises(ValueError, match="renewal evidence"):
        obj.renewal_evidence()


def test_job_state_accessor_does_not_restart_review_and_explicit_restart_persists(tmp_path):
    obj = JobScene(tmp_path, job={})
    obj.finish_review = {"call_id": "first", "binding": {"checkpoint": "c0001", "photos": ["front"]}}
    obj.best_checkpoint = "c0001"
    obj._emitted_images = {"image": "first"}
    obj.save()
    saved = obj.state_path.read_bytes()
    state = obj.renewal_state()
    assert state["finish_review"]["status"] == "pending"
    state["finish_review"]["binding"]["photos"].append("not a photo")
    assert obj.state_path.read_bytes() == saved and obj.finish_review["binding"]["photos"] == ["front"]
    assert obj.renewal_started() == {"finish_review_restarted": True, "previous_review_call_id": "first"}
    assert obj._emitted_images == {} and JobScene(tmp_path, job={}).finish_review is None
    assert obj.renewal_state()["finish_review"] == {"status": "not_pending"}
    assert obj.best_checkpoint == "c0001"


def test_real_finish_tool_can_restart_and_complete_after_renewal(review_scene):
    obj = review_scene
    obj._state_lock = threading.RLock()
    obj.save = lambda: None
    assert record(invoke(obj, "finish", {"summary": "Inspect"}, cid="first"))["status"] == "review_required"
    assert obj._emitted_images
    JobScene.renewal_started(obj)
    fresh = invoke(obj, "finish", final_args("first"), cid="fresh")
    assert record(fresh)["status"] == "review_required"
    assert sum(isinstance(part, ToolOutputImage) for part in fresh) == 4
    assert record(invoke(obj, "finish", final_args("fresh"), cid="complete"))["status"] == "accepted"


def lab(root):
    obj = Lab.__new__(Lab)
    obj.root = root
    obj.state_lock = threading.RLock()
    obj._unseen = set()
    obj.s = SimpleNamespace(head={"id": "c0003", "fingerprint": "f3"})
    obj.state = {"events": [], "compares": [], "images": {}, "readings": {}, "heads": [
        {"checkpoint": "c0001", "fingerprint": "f1", "revision": 1},
        {"checkpoint": "c0003", "fingerprint": "f3", "revision": 3}], "best_checkpoint": "c0001"}
    return obj


def lab_image(obj, name, **meta):
    row = image(obj.root, name, **meta)
    obj.state["images"][row["sha256"]] = {"file": row["path"], **meta}
    return row["sha256"]


def test_lab_retains_full_groups_pending_zoom_and_only_current_readings(tmp_path):
    obj = lab(tmp_path)
    for cid, photos, stacked in [("old", ["front", "side"], ["front", "side"]),
                                 ("new", ["front"], ["front"]),
                                 ("zoom", ["side"], [])]:
        obj.state["compares"].append({"compare_id": cid, "call_id": "call-" + cid,
            "checkpoint": "c0003", "revision": 3, "photos": photos, "stacked": stacked,
            "by": "astra", "judged": cid != "zoom"})
        for pid in photos:
            lab_image(obj, cid + pid, kind="compare", photo=pid, ref=cid, revision=3)
    lab_image(obj, "new-detail", kind="compare", photo="front", ref="new", revision=3, zoom=True)
    lab_image(obj, "pick-old", kind="reading", ref="read1", reading="Q", revision=1)
    lab_image(obj, "pick-new", kind="reading", ref="read2", reading="Q", revision=3, checkpoint="c0003")
    lab_image(obj, "pick-dropped", kind="reading", ref="read2", reading="R", revision=3)
    obj.state["readings"]["Q"] = {"id": "read2-Q", "call_id": "call-read", "photos": ["front", "side"]}
    before = copy.deepcopy(obj.state)
    rows = obj.renewal_evidence()
    assert len(rows) == 5
    assert {r["call_id"] for r in rows} == {"call-old", "call-new", "call-zoom", "call-read"}
    assert all(r["checkpoint"] == "c0003" for r in rows)
    assert obj.state == before
    state = obj.renewal_state()
    assert state["pending_compare"]["compare_id"] == "zoom"
    state["pending_compare"]["photos"].clear()
    assert obj.state == before
    obj.renewal_started()
    assert obj.renewal_evidence() == rows and obj.state == before


def test_lab_unseen_compare_never_supersedes_and_missing_selected_bytes_refuse(tmp_path):
    obj = lab(tmp_path)
    for cid in ("seen", "unseen"):
        obj.state["compares"].append({"compare_id": cid, "call_id": cid, "checkpoint": "c0001",
            "revision": 1, "photos": ["front"], "by": "astra", "judged": True, "unseen": cid == "unseen"})
        lab_image(obj, cid, kind="compare", photo="front", ref=cid, revision=1)
    assert [r["call_id"] for r in obj.renewal_evidence()] == ["seen"]
    (tmp_path / "work/seen.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="renewal evidence"):
        obj.renewal_evidence()


@pytest.mark.parametrize("lost_after_renewal", [False, True])
def test_renewed_session_recovery_keeps_archived_completed_mutations_and_undoes_only_lost_new_call(
        tmp_path, lost_after_renewal):
    """A fresh conversation is not evidence that successful old tool outputs were lost."""
    async def check():
        path, blobs = tmp_path / "session.sqlite", BlobStore(tmp_path / "blobs")
        session = BlobSession("astra", path, blobs=blobs)
        old = [{"type": "function_call", "call_id": "completed", "name": "execute_blender_code",
                "arguments": "{}"},
               {"type": "function_call_output", "call_id": "completed", "output": "checkpoint c0001"}]
        journal = [{"kind": "call", "tool": "execute_blender_code", "call_id": "completed",
                    "mutating": True, "checkpoint_before": "c0000", "checkpoint_after": "c0001"}]
        try:
            await session.add_items(old)
            active_id = await session.renew([{"role": "user", "content": "Fresh conversation; head c0001."}])
            if lost_after_renewal:
                await session.add_items([{"type": "function_call", "call_id": "lost", "name": "execute_blender_code",
                                          "arguments": "{}"}])
                journal.append({"kind": "call", "tool": "execute_blender_code", "call_id": "lost",
                                "mutating": True, "checkpoint_before": "c0001", "checkpoint_after": "c0002"})
        finally:
            session.close()
        # Exercise the process restart path, not just the original Python object's
        # active pointer. No post-renewal function_call_output was ever recorded.
        reopened = BlobSession("astra", path, blobs=blobs)
        try:
            assert reopened.session_id == active_id
            active = await reopened.get_items()
            assert all(item.get("call_id") != "completed" for item in active)
            recovery = await reopened.recovery_items()
            assert recovery[:len(old)] == old
            rollback = plan_rollback(recovery, journal)
            if lost_after_renewal:
                assert rollback["checkpoint_before"] == "c0001"
                assert rollback["lost_calls"] == ["lost"]
                assert rollback["last_output_call_id"] == "completed"
            else:
                assert rollback is None
        finally:
            reopened.close()
    asyncio.run(check())
