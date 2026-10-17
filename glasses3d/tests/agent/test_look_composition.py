"""M9 lossless look pages, through both SDK tools; no network or rendering."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path

from agents.tool import ToolOutputImage
import numpy as np
from PIL import Image, ImageDraw
import pytest

from glasses3d.agent.look_cost import documented_image
from glasses3d.agent.tools import look, look_composition as composition
from glasses3d.agent.tools.look_panels import DETAIL_KINDS
from glasses3d.tests.agent.test_finish_review import final_args, review_scene
from glasses3d.tests.agent.test_job_tools import finish_scene, invoke, record, scene


def panel(directory, size, number, *, ref="front", kind="look_full_pair"):
    """Coordinates, edge pixels and labels make scaling/cropping visibly testable."""
    directory.mkdir(parents=True, exist_ok=True)
    width, height = size
    x, y = np.arange(width)[None, :], np.arange(height)[:, None]
    pixels = np.empty((height, width, 3), np.uint8)
    pixels[..., 0] = (x * 13 + y * 7 + number * 29) % 256
    pixels[..., 1] = (x * 3 + y * 11 + number * 47) % 256
    pixels[..., 2] = (x * 17 + y * 5 + number * 61) % 256
    image = Image.fromarray(pixels)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width - 1, height - 1), outline="magenta")
    draw.text((3, 3), f"{ref} / PHOTO | MODEL / {kind} {number}", fill="white", stroke_width=1,
              stroke_fill="black")
    path = directory / f"panel-{number}.png"
    image.save(path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size_px": list(size), "kind": kind, "ref": ref, "view": f"{ref} {kind} {number}"}


def assert_lossless_pages(images, pages):
    expected = {source["path"]: source for source in images}
    seen = []
    for page in pages:
        assert page["kind"] == "look_composed"
        assert hashlib.sha256(Path(page["path"]).read_bytes()).hexdigest() == page["sha256"]
        with Image.open(page["path"]) as combined:
            assert list(combined.size) == page["size_px"]
            estimate = documented_image(*combined.size, "high")
            assert estimate["processed_dimensions"] == list(combined.size)
            assert estimate["patches"] == page["patches_32px"] <= 2500
            for part in page["parts"]:
                source = expected[part["path"]]
                assert {key: part[key] for key in source} == source
                left, top, right, bottom = part["box"]
                assert 0 <= left < right <= combined.width
                assert 0 <= top < bottom <= combined.height
                with Image.open(part["path"]) as original:
                    assert (right - left, bottom - top) == original.size
                    assert combined.crop(part["box"]).tobytes() == original.tobytes()
                seen.append(part["path"])
            for i, a in enumerate(page["parts"]):
                for b in page["parts"][i + 1:]:
                    al, at, ar, ab = a["box"]
                    bl, bt, br, bb = b["box"]
                    assert ar <= bl or br <= al or ab <= bt or bb <= at
    assert sorted(seen) == sorted(expected)


def test_full_pair_and_all_labels_and_details_keep_every_pixel(tmp_path):
    sizes = [(2048, 601), (640, 301), (640, 301), (640, 301)]
    sources = [panel(tmp_path / "sources", size, i,
                     kind="look_full_pair" if i == 0 else "look_detail")
               for i, size in enumerate(sizes)]
    before = copy.deepcopy(sources)
    source_bytes = [Path(source["path"]).read_bytes() for source in sources]
    pages = composition.compose(sources, tmp_path / "combined", "front")
    assert len(pages) == 1
    assert_lossless_pages(sources, pages)
    assert sources == before
    assert [Path(source["path"]).read_bytes() for source in sources] == source_bytes


def test_full_budget_pair_spills_closeup_into_second_image_without_shrinking(tmp_path):
    sources = [panel(tmp_path / "sources", size, i) for i, size in
               enumerate([(1600, 1600), (33, 35)])]
    pages = composition.compose(sources, tmp_path / "combined", "front")
    assert len(pages) == 2
    assert_lossless_pages(sources, pages)
    assert sorted(page["size_px"] for page in pages) == [[33, 35], [1600, 1600]]


@pytest.mark.parametrize("sizes", [
    [(1600, 1600)],       # exactly 2,500 patches
    [(1599, 1599)],       # rounded-up patch count, not only pixel area
    [(1601, 1537)],       # 51 * 49 = 2,499 despite nonaligned edges
    [(2048, 1248)],
    [(65535, 1)],         # documented maximum edge
    [(2048, 610), (614, 293), (711, 317)],
    [(1600, 1600), (33, 35)],
    [(2500, 1), (1, 2500)],  # individually valid, no common budget-sized canvas
])
def test_documented_high_detail_never_resizes_any_planned_page(tmp_path, sizes):
    sources = [panel(tmp_path / "sources", size, i) for i, size in enumerate(sizes)]
    pages = composition.compose(sources, tmp_path / "combined", "front")
    assert_lossless_pages(sources, pages)
    planned = composition.layout(sizes)
    assert [page["size_px"] for page in pages] == [page["size_px"] for page in planned]
    assert sorted(part["index"] for page in planned for part in page["parts"]) == list(range(len(sizes)))


@pytest.mark.parametrize("size", [(1601, 1600), (65536, 1)])
def test_oversized_individual_panel_is_refused_instead_of_shrunk(tmp_path, size):
    source = panel(tmp_path / "sources", size, 0)
    original = Path(source["path"]).read_bytes()
    with pytest.raises(ValueError):
        composition.compose([source], tmp_path / "combined", "front")
    assert Path(source["path"]).read_bytes() == original


@pytest.fixture
def composed_scene(review_scene, monkeypatch):
    result = {"status": "ok", "image_delivery": "separate", "views": [], "images": [],
              **copy.deepcopy(review_scene.binding)}
    for photo_number, photo in enumerate(review_scene.binding["photos"]):
        ref = photo["ref"]
        folder = review_scene.root / "work" / ref
        full = panel(folder, (2048, 620), photo_number * 10, ref=ref)
        details = [panel(folder, (402, 202), photo_number * 10 + i + 1,
                         ref=ref, kind="look_detail_" + kind)
                   for i, kind in enumerate(DETAIL_KINDS)]
        sources = [full, *details]
        pages = composition.compose(sources, folder / "combined", ref)
        assert len(pages) == 1
        result["views"].append({"ref": ref, "photo_sha256": photo["sha256"], "images": sources,
                                "details": [{"kind": kind, "status": "visible", "images": [detail]}
                                            for kind, detail in zip(DETAIL_KINDS, details)],
                                "composed_images": pages})
        result["images"].extend(pages)
    worn = panel(review_scene.root / "work", (600, 380), 99, ref="worn", kind="worn_front")
    result["images"].append(worn)
    result["worn_front_path"] = worn["path"]
    review_scene.composition_result = result

    def capture(root, export, cid, **kwargs):
        review_scene.look_calls.append(kwargs)
        return {**copy.deepcopy(result), "export_sha256": export["sha256"]}

    monkeypatch.setattr(look, "capture", capture)
    return review_scene


@pytest.mark.parametrize("tool_name", ["compare_look_to_photos", "finish"])
def test_both_sdk_tools_emit_native_per_photo_pages_and_leave_prior_output_unchanged(composed_scene, tool_name):
    args = {"summary": "Inspect"} if tool_name == "finish" else {}
    output = invoke(composed_scene, tool_name, args, cid="first")
    first_bytes = [part.model_dump() for part in output]
    result = record(output)
    assert composed_scene.look_calls[-1]["combine"] is True
    if result.get("paged"):
        report = composed_scene.root / result["full_report"]["path"]
        result = json.loads(report.read_text(encoding="utf-8"))["result"]
    assert result["status"] == ("review_required" if tool_name == "finish" else "ok")
    pages = [row for row in result["images"] if row["kind"] == "look_composed"]
    assert [page["ref"] for page in pages] == ["front", "top"]
    for page in pages:
        source_page = next(source for source in composed_scene.composition_result["images"]
                           if source["kind"] == "look_composed" and source["ref"] == page["ref"])
        assert {part["ref"] for part in source_page["parts"]} == {page["ref"]}
        sdk_image = output[page["output_index"]]
        assert isinstance(sdk_image, ToolOutputImage) and sdk_image.detail == "high"
        encoded = base64.b64decode(sdk_image.image_url.split(",", 1)[1])
        with Image.open(io.BytesIO(encoded)) as received, Image.open(source_page["path"]) as original:
            assert received.size == original.size
            assert received.tobytes() == original.tobytes()
            assert documented_image(*received.size, "high")["processed_dimensions"] == list(received.size)
        assert_lossless_pages(source_page["parts"], [source_page])
    assert sum(isinstance(part, ToolOutputImage) for part in output) == (4 if tool_name == "finish" else 3)
    second_args = final_args() if tool_name == "finish" else {}
    second = invoke(composed_scene, tool_name, second_args, cid="second")
    assert record(second)["status"] == ("accepted" if tool_name == "finish" else "ok")
    assert not any(isinstance(part, ToolOutputImage) for part in second)
    assert [part.model_dump() for part in output] == first_bytes


def test_finish_rejects_a_closeup_missing_from_composed_delivery(composed_scene):
    composed_scene.composition_result["images"][0]["parts"].pop()
    output = record(invoke(composed_scene, "finish", {"summary": "Inspect"}, cid="first"))
    assert output["status"] == "refused" and output["code"] == "E_FINISH_REVIEW"
    assert composed_scene.finish is None and composed_scene.finish_review is None
