"""Owner capture pixels/roles remain separate, immutable and renewable ($0)."""
import asyncio
import base64
import copy
from dataclasses import replace
import hashlib
import io
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent import approvals as A, session_inputs as S, renewal as N
from .test_job_cli import job
from .test_job_tools import scene, invoke, record


def fixture_records(tmp_path, monkeypatch, size=(1600, 900)):
    from glasses3d.agent import owner_captures
    folder = tmp_path / "inputs/owner_captures/C1"
    folder.mkdir(parents=True)
    pixels = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    pixels[:, :, 0] = np.arange(size[0], dtype=np.uint16) % 251
    pixels[:, :, 1] = np.arange(size[1], dtype=np.uint16)[:, None] % 247
    record = {"item_id": "I1", "capture_id": "C1", "labels": ["A"],
              "request_text": "  Move A inward.\nKeep this exact text.  ", "metadata_sha256": "b" * 64,
              "payload": {"source": {"glb_sha256": "c" * 64}, "camera": {"position_m": [0, 0, .4]},
                          "marks": [{"label": "A", "kind": "point", "vertices": [
                              {"xy_px": [10, 12], "anchor": {"kind": "background"}}],
                              "footprint": {"pixels": 0, "faces": []}}]}}
    for role in ("annotated", "viewer"):
        path = folder / ("annotated.png" if role == "annotated" else "viewer.png")
        Image.fromarray(pixels).save(path)
        record[role + "_path"] = path
        record[role + "_sha256"] = A.file_sha256(path)
    monkeypatch.setattr(owner_captures, "load_round_captures", lambda root, owner_edit=None: [record])
    return record, pixels


def test_native_tiles_cover_every_pixel_and_roles_have_separate_images(tmp_path, monkeypatch):
    record, pixels = fixture_records(tmp_path, monkeypatch, (3201, 1703))
    refs = S.owner_capture_references(tmp_path)
    assert len(refs) == 12  # 3 columns x 2 rows, each clean and annotated
    for role in ("annotated", "viewer"):
        rebuilt = np.zeros_like(pixels)
        counts = np.zeros(pixels.shape[:2], dtype=np.uint8)
        for ref in refs:
            m = ref.evidence_metadata
            if m["image_role"] != role:
                continue
            assert ref.evidence_kind == "owner_capture" and "not a product photo" in ref.label
            assert record["request_text"] in ref.evidence_text
            assert "unresolved" in ref.evidence_text and "bridge-relative mm" in ref.evidence_text
            x0, y0, x1, y1 = m["crop_box"]
            with Image.open(io.BytesIO(base64.b64decode(ref.image_url.split(",")[1]))) as image:
                assert image.size == ref.size_px
                rebuilt[y0:y1, x0:x1] = np.asarray(image)
            counts[y0:y1, x0:x1] += 1
        assert np.array_equal(rebuilt, pixels) and np.all(counts == 1)
    plan = S.plan_session_input([], prompt="brief", photos=refs, invocation_text="run")
    assert plan.receipt["images_sent"] == 12  # equal clean/annotated bytes still retain their own roles


def test_capture_replay_needs_its_own_label_and_never_mutates_history(tmp_path, monkeypatch):
    fixture_records(tmp_path, monkeypatch)
    refs = S.owner_capture_references(tmp_path)
    photo = replace(refs[0], label="Reference 1 of 1", evidence_kind="product_photo",
                    evidence_metadata=None, evidence_text=None)
    photo_plan = S.plan_session_input([], prompt="brief", photos=[photo], invocation_text="one")
    history = [{"role": "user", "content": photo_plan.content}]
    before = copy.deepcopy(history)
    captures = S.plan_session_input(history, prompt="brief", photos=refs, invocation_text="two")
    assert captures.receipt["images_sent"] == 2 and history == before
    history.append({"role": "user", "content": captures.content})
    repeated = S.plan_session_input(history, prompt="brief", photos=refs, invocation_text="three")
    assert repeated.receipt["images_sent"] == 0
    assert [row["evidence_kind"] for row in repeated.receipt["references"]] == ["owner_capture"] * 2


def binding_fixture(tmp_path, monkeypatch):
    record, _ = fixture_records(tmp_path, monkeypatch)
    image = tmp_path / "images/front.png"
    image.parent.mkdir()
    Image.new("RGB", (32, 24), "red").save(image)
    (tmp_path / "job.json").write_text(json.dumps({"schema": "g3d.job/1", "images": [{"file": "images/front.png"}]}))
    photo = S.ReferenceInput("Reference 1 of 1", "images/front.png",
        "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode(), view="front", size_px=(32, 24))
    return record, [photo, *S.owner_capture_references(tmp_path)]


def test_binding_keeps_photo_contract_and_adds_capture_source_and_tile_identity(tmp_path, monkeypatch):
    _, refs = binding_fixture(tmp_path, monkeypatch)
    result = A.run_input_binding(tmp_path, "brief", refs)
    assert result["photos"][0]["label"] == "Reference 1 of 1"
    for row in result["photos"][1:]:
        assert row["evidence_kind"] == "owner_capture"
        assert row["evidence_metadata"]["metadata_sha256"] == "b" * 64
        assert row["evidence_metadata"]["source_image_sha256"] == row["sha256"]


@pytest.mark.parametrize("change", ["label", "metadata", "words", "bytes", "kind", "missing", "photo_missing"])
def test_capture_descriptor_tampering_or_missing_evidence_refuses(tmp_path, monkeypatch, change):
    _, refs = binding_fixture(tmp_path, monkeypatch)
    if change == "label":
        refs[1] = replace(refs[1], label="Owner mark B")
    elif change == "metadata":
        refs[1] = replace(refs[1], evidence_metadata={**refs[1].evidence_metadata, "labels": ["B"]})
    elif change == "words":
        refs[1] = replace(refs[1], evidence_text="different request")
    elif change == "bytes":
        refs[1] = replace(refs[1], image_url=refs[0].image_url)
    elif change == "kind":
        refs[1] = replace(refs[1], evidence_kind="product_photo")
    elif change == "missing":
        refs.pop()
    else:
        refs.pop(0)
    with pytest.raises(A.ApprovalRefused):
        A.run_input_binding(tmp_path, "brief", refs)


def test_capture_file_change_refuses_even_when_reference_object_is_old(tmp_path, monkeypatch):
    record, refs = binding_fixture(tmp_path, monkeypatch)
    record["annotated_path"].write_bytes(record["annotated_path"].read_bytes() + b"changed")
    with pytest.raises(A.ApprovalRefused, match="changed"):
        A.run_input_binding(tmp_path, "brief", refs)


def test_capture_fields_cannot_be_smuggled_into_photo_role(tmp_path, monkeypatch):
    _, refs = binding_fixture(tmp_path, monkeypatch)
    refs[0] = replace(refs[0], evidence_text="unbound extra content")
    with pytest.raises(A.ApprovalRefused, match="product photos"):
        A.run_input_binding(tmp_path, "brief", refs)


@pytest.mark.parametrize("preceding", [None, 1, [], {"type": "input_text", "text": []}])
def test_malformed_history_cannot_prove_capture_role(tmp_path, monkeypatch, preceding):
    fixture_records(tmp_path, monkeypatch)
    refs = S.owner_capture_references(tmp_path)
    history = [{"role": "user", "content": [preceding,
        {"type": "input_image", "image_url": refs[0].image_url, "detail": "high"}]}]
    plan = S.plan_session_input(history, prompt="brief", photos=refs, invocation_text="run")
    assert plan.receipt["images_sent"] == 2


def test_renewal_carries_both_roles_with_same_labels_and_pixels(tmp_path, monkeypatch):
    _, refs = binding_fixture(tmp_path, monkeypatch)
    initial = S.plan_session_input([], prompt="brief", photos=refs, invocation_text="run")
    class Session:
        session_id = "old"
        items = [{"role": "user", "content": initial.content}]
        async def get_items(self):
            return copy.deepcopy(self.items)
        async def add_items(self, items):
            self.items.extend(items)
        async def renew(self, items, receipt):
            self.new_items = items
            self.session_id = "new"
            return "new"
    class Responses:
        async def create(self, **kwargs):
            return SimpleNamespace(id="summary", usage={}, output_text="Preserve A", output=[], status="completed")
    async def hook(*args):
        pass
    session = Session()
    old = copy.deepcopy(session.items)
    scene = SimpleNamespace(renewal_evidence=lambda: [], renewal_started=lambda: {}, renewal_state=lambda: {})
    log = SimpleNamespace(run_id="r", tool_calls=[], interactions=1, event=lambda *a, **kw: None,
                          note=lambda *a, **kw: None)
    guard = SimpleNamespace(request_pressure=None, summary=lambda: {"requests": 1})
    renewal = N.ConversationRenewal(spec=SimpleNamespace(brief="brief", photos=refs, owner_notes=[]), scene=scene,
        session=session, log=log, guard=guard, client=SimpleNamespace(responses=Responses()), directory=tmp_path)
    trigger = N.RenewalNeeded({"input": old}, {}, [])
    asyncio.run(renewal.renew(trigger, agent=SimpleNamespace(instructions="instructions"), context=None,
                            hooks=SimpleNamespace(on_llm_start=hook, on_llm_end=hook)))
    assert session.items[:len(old)] == old
    contents = session.new_items[0]["content"]
    assert [p["image_url"] for p in contents if p["type"] == "input_image"] == [r.image_url for r in refs]
    for ref in refs:
        assert any(ref.label in p.get("text", "") for p in contents)
    assert [r.get("evidence_kind", "product_photo") for r in renewal.records[0]["input_receipt"]["references"]] == [
        "product_photo", "owner_capture", "owner_capture"]


def test_read_image_allows_only_capture_pngs_and_keeps_distinct_tiles(tmp_path):
    from glasses3d.agent.tools import _read_image_path, _sheets
    capture = tmp_path / "inputs/owner_captures/C1/viewer.png"
    capture.parent.mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(capture)
    assert _read_image_path(tmp_path, "inputs/owner_captures/C1/viewer.png") == capture
    for name in ("inputs/seed.blend", "inputs/owner_captures/items.json", "inputs/owner_captures/C1/capture.json"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private")
        with pytest.raises(ValueError):
            _read_image_path(tmp_path, name)
    records = [{"path": str(capture), "region": [0, 0, 5, 10]}, {"path": str(capture), "region": [5, 0, 10, 10]}]
    assert _sheets({"images": records}, "read_image") == records


def test_preparation_estimate_counts_capture_images_and_text(job, monkeypatch):
    from glasses3d.agent import job_run
    baseline = []
    original = job_run.E.initial_context_tokens
    def count(chars, images):
        baseline.append((chars, images))
        return original(chars, images)
    monkeypatch.setattr(job_run.E, "initial_context_tokens", count)
    plain, _ = job_run.prepare_run(job, "plain", write_request=True)
    fixture_records(job, monkeypatch, (1601, 900))
    captured, _ = job_run.prepare_run(job, "captures", write_request=True)
    assert baseline[-1][1] == baseline[0][1] + 4
    assert len(captured.photos) == len(plain.photos) + 4
    assert baseline[-1][0] > baseline[0][0] + sum(len(p.evidence_text) for p in captured.photos[1:])


def test_read_image_tool_sends_every_native_capture_tile(scene, monkeypatch):
    _, pixels = fixture_records(scene.root, monkeypatch, (3201, 1703))
    output = invoke(scene, "read_image", {"path": "inputs/owner_captures/C1/viewer.png"})
    result = record(output)
    assert result["status"] == "ok" and len(result["images"]) == 6
    rebuilt = np.zeros_like(pixels)
    for row in result["images"]:
        assert row["context_role"] == row["kind"] == "owner_capture"
        assert row["native_pixels"] is True and "not a product photo" in row["view"]
        x0, y0, x1, y1 = row["region"]
        sent = output[row["output_index"]]
        data = base64.b64decode(sent.image_url.split(",")[1])
        assert hashlib.sha256(data).hexdigest() == row["sha256"]
        with Image.open(io.BytesIO(data)) as image:
            assert image.size == (x1 - x0, y1 - y0)
            rebuilt[y0:y1, x0:x1] = np.asarray(image)
    assert np.array_equal(rebuilt, pixels)


@pytest.mark.parametrize("mode", ["RGBA", "L", "I;16"])
def test_capture_transport_and_crops_preserve_raw_grid_and_channels(tmp_path, monkeypatch, mode):
    from glasses3d.agent.tools import _image
    record, _ = fixture_records(tmp_path, monkeypatch, (30, 20))
    dtype = np.uint16 if mode == "I;16" else np.uint8
    shape = (20, 30, 4) if mode == "RGBA" else (20, 30)
    pixels = np.arange(np.prod(shape), dtype=dtype).reshape(shape)
    source = Image.fromarray(pixels)
    exif = Image.Exif()
    exif[274] = 6  # A photo reader would rotate; capture anchors must keep the raw grid.
    for role in ("viewer", "annotated"):
        source.save(record[role + "_path"], exif=exif)
        record[role + "_sha256"] = A.file_sha256(record[role + "_path"])
    for ref in S.owner_capture_references(tmp_path):
        with Image.open(io.BytesIO(base64.b64decode(ref.image_url.split(",")[1]))) as sent:
            assert np.array_equal(np.asarray(sent), pixels)
            assert sent.getexif().get(274) is None
    path = record["viewer_path"].relative_to(tmp_path).as_posix()
    meta, _ = _image(tmp_path, path, region=[3, 4, 17, 12])
    with Image.open(tmp_path / meta["path"]) as cropped:
        assert np.array_equal(np.asarray(cropped), pixels[4:12, 3:17])
        assert cropped.getexif().get(274) is None
    assert A.file_sha256(record["viewer_path"]) == record["viewer_sha256"]


def test_capture_image_helper_refuses_implicit_shrinking(tmp_path, monkeypatch):
    from glasses3d.agent.tools import _image
    fixture_records(tmp_path, monkeypatch, (1601, 900))
    with pytest.raises(ValueError, match="cannot be shrunk"):
        _image(tmp_path, "inputs/owner_captures/C1/viewer.png")


def test_read_image_tool_delivers_alpha_without_second_conversion(scene):
    path = scene.root / "inputs/owner_captures/C1/viewer.png"
    path.parent.mkdir(parents=True)
    pixels = np.arange(20 * 30 * 4, dtype=np.uint8).reshape(20, 30, 4)
    exif = Image.Exif()
    exif[274] = 6
    Image.fromarray(pixels).save(path, exif=exif)
    output = invoke(scene, "read_image", {"path": path.relative_to(scene.root).as_posix(), "region": [3, 4, 17, 12]})
    row = record(output)["images"][0]
    data = base64.b64decode(output[row["output_index"]].image_url.split(",")[1])
    assert hashlib.sha256(data).hexdigest() == row["sha256"]
    with Image.open(io.BytesIO(data)) as image:
        assert np.array_equal(np.asarray(image), pixels[4:12, 3:17])
        assert image.getexif().get(274) is None
