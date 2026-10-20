"""L2b regional measurements and actual production-tool transport, with no renderer or API."""
import asyncio
import copy
import inspect
import json
from pathlib import Path
import subprocess
import threading
import types
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from agents.tool import ToolOutputImage, ToolOutputText
from agents.tool_context import ToolContext

from glasses3d.agent import tool_reports, tools
from glasses3d.agent.tools import look
from glasses3d.lens_lab import regional, variants


def _rows(photo, drawing, mask, ref="reference-01"):
    return [{"ref": ref, "colours": {"lens": {
        "photo": look.colour_stats(photo, mask), "drawing": look.colour_stats(drawing, mask),
        "regions": regional.lens_region_stats(photo, drawing, mask)}}}]


@pytest.mark.parametrize("width,counts", [(1, [2, 0, 0]), (2, [2, 2, 0]),
                                         (4, [4, 2, 2]), (5, [4, 4, 2]), (6, [4, 4, 4])])
def test_horizontal_extent_uses_mask_offset_and_partitions_uneven_widths(width, counts):
    mask = np.zeros((4, width + 10), bool)
    mask[1:3, 4:4 + width] = True
    photo = np.full((*mask.shape, 3), 255, np.uint8)
    photo[mask] = [21, 40, 83]
    drawing = np.full_like(photo, 199)
    drawing[mask] = [44, 52, 60]
    actual = regional.lens_region_stats(photo, drawing, mask)
    assert [r["region"] for r in actual] == ["left", "centre", "right"]
    assert [r["photo"]["pixels"] for r in actual] == counts
    assert sum(counts) == mask.sum()
    for index, row in enumerate(actual):
        assert row["drawing"]["pixels"] == counts[index]
        assert row["x_extent_px"] == pytest.approx([4 + index * width / 3, 4 + (index + 1) * width / 3])
        if counts[index]:
            assert row["photo"]["median_srgb_8bit"] == [21, 40, 83]
            assert row["drawing"]["median_srgb_8bit"] == [44, 52, 60]
        else:
            assert row["photo"]["status"] == "unavailable"


def test_disconnected_mask_and_holes_do_not_sample_background_or_redivide_by_pixel_count():
    mask = np.zeros((3, 14), bool)
    mask[1, [2, 3, 8, 9, 10]] = True  # extent 2..11; centre third is empty
    photo = np.full((*mask.shape, 3), 255, np.uint8)
    photo[1, 2] = [10, 20, 30]
    photo[1, 3] = [11, 21, 31]
    photo[1, 8:11] = [90, 80, 70]
    result = regional.lens_region_stats(photo, photo, mask)
    assert [r["photo"]["pixels"] for r in result] == [2, 0, 3]
    assert result[0]["photo"]["median_srgb_8bit"] == [10.5, 20.5, 30.5]
    assert result[1]["photo"]["reason"] == "no interior pixels in this horizontal third"
    assert result[2]["photo"]["median_srgb_8bit"] == [90, 80, 70]


def test_lightness_is_median_of_pixel_lightness_and_fractional_rgb_survives_text():
    mask = np.ones((1, 6), bool)
    photo = np.array([[[0, 0, 0], [255, 255, 255]] * 3], np.uint8)
    drawing = np.array([[[20, 30, 40], [21, 31, 41]] * 3], np.uint8)
    rows = _rows(photo, drawing, mask)
    for record in rows[0]["colours"]["lens"]["regions"]:
        assert record["photo"]["median_lightness_Lstar"] == pytest.approx(50)
        assert record["photo"]["median_srgb_8bit"] == [127.5] * 3
    median_rgb_l = look.colour_stats(np.full((1, 1, 3), 127.5), np.ones((1, 1), bool))["median_lightness_Lstar"]
    assert median_rgb_l != pytest.approx(50)
    whole = look.lens_colour_lines(rows)
    before = copy.deepcopy(rows)
    line = regional.regional_lens_colour_lines(rows, whole)[0]
    assert line.startswith(whole[0] + "\nreference-01 lens regions left / centre / right: AR L* ")
    assert "RGB (sRGB 8-bit) 20.5,30.5,40.5 / 20.5,30.5,40.5 / 20.5,30.5,40.5" in line
    assert "photo L* 50.0 / 50.0 / 50.0, RGB (sRGB 8-bit) 127.5,127.5,127.5" in line
    assert rows == before


def test_empty_mask_has_three_unavailable_regions_and_retains_existing_line():
    image = np.zeros((4, 6, 3), np.uint8)
    rows = _rows(image, image, np.zeros(image.shape[:2], bool))
    for region in rows[0]["colours"]["lens"]["regions"]:
        assert region["x_extent_px"] is None
        assert region["photo"]["status"] == region["drawing"]["status"] == "unavailable"
    whole = look.lens_colour_lines(rows)
    line = regional.regional_lens_colour_lines(rows, whole)[0]
    assert line.split("\n")[0] == whole[0]
    assert line.count("unavailable") == 13
    with pytest.raises(ValueError):
        regional.regional_lens_colour_lines(rows, [])


def test_unapplied_patch_applies_exactly_to_isolated_candidate_and_embeds_identical_helpers(tmp_path):
    original, candidate = variants.regional_source()
    path = tmp_path / variants.LOOK_PATH
    path.parent.mkdir(parents=True)
    path.write_text(original, encoding="utf-8", newline="\n")
    patch = variants.production_patch()
    for args in (("--check",), ()):
        subprocess.run(["git", "apply", *args, "-"], cwd=tmp_path, input=patch.encode("utf-8"),
                       capture_output=True, check=True)
    assert path.read_text(encoding="utf-8") == candidate
    assert Path(look.__file__).read_text(encoding="utf-8") == original
    for fn in (regional.lens_region_stats, regional.regional_lens_colour_lines):
        assert inspect.getsource(fn).rstrip() in candidate
    module = variants.regional_module()
    assert module.capture is not look.capture
    assert module.tool_for.__doc__ == look.tool_for.__doc__
    image = np.arange(90, dtype=np.uint8).reshape(3, 10, 3)
    mask = np.zeros((3, 10), bool)
    mask[1, [1, 2, 4, 8]] = True
    assert module.lens_region_stats(image, image, mask) == regional.lens_region_stats(image, image, mask)
    rows = _rows(image, image, mask)
    whole = look.lens_colour_lines(rows)
    assert module.regional_lens_colour_lines(rows, whole) == regional.regional_lens_colour_lines(rows, whole)


def test_source_anchor_drift_refuses_instead_of_running_unmodified_regional(monkeypatch):
    original_read = Path.read_text
    def changed(path, *args, **kwargs):
        text = original_read(path, *args, **kwargs)
        return text.replace("def box_of(mask, margin=8):", "def box_of(mask, margin=9):") if path == Path(look.__file__) else text
    monkeypatch.setattr(Path, "read_text", changed)
    with pytest.raises(ValueError, match="port anchors"):
        variants.regional_module()


def _scene(root):
    model = root / "work/exports/model.glb"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"mock model; no Blender")
    session = SimpleNamespace(head={"id": "c0001", "fingerprint": "f" * 64},
                              call_read=lambda *a, **kw: {"status": "ok", "result": {"objects": []}})
    export = {"status": "exported", "path": str(model), "sha256": look.arview.sha256(model), "checkpoint": "c0001"}
    scene = SimpleNamespace(root=root, s=session, job={}, lock=threading.RLock(), calls=[], images=[], unseen=[])
    scene.seeing = SimpleNamespace(export_head=lambda **kw: copy.deepcopy(export))
    scene.record_call = lambda *args: scene.calls.append(args)
    scene.record_images = lambda cid, result: scene.images.extend(result["images"])
    scene.mark_unseen = scene.unseen.append
    return scene, export


def _invoke(registry, name="compare_look_to_photos", cid="same-call"):
    tool = next(t for t in registry if t.name == name)
    raw = "{}"
    ctx = ToolContext(context=None, tool_name=name, tool_call_id=cid, tool_arguments=raw)
    return asyncio.run(tool.on_invoke_tool(ctx, raw))


def _wire(parts):
    return json.dumps([p.model_dump() if hasattr(p, "model_dump") else p for p in parts],
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def test_existing_registries_are_independent_and_all_schemas_and_nonlook_output_match(tmp_path, monkeypatch):
    scene, _ = _scene(tmp_path)
    module = variants.regional_module()
    monkeypatch.setattr(module, "capture", lambda *a, **kw: {"status": "ok", "origin": "regional", "images": []})
    monkeypatch.setattr(variants, "regional_module", lambda: module)
    monkeypatch.setattr(look, "capture", lambda *a, **kw: {"status": "ok", "origin": "baseline", "images": []})
    original = look.capture
    baseline = variants.build_variant_tools(scene)
    modified = variants.build_variant_tools(scene, "regional")
    historical = variants.build_variant_tools(scene, "historical-label")
    def schemas(registry):
        return [(t.name, t.description, t.params_json_schema, t.timeout_seconds) for t in registry]
    assert schemas(baseline) == schemas(modified) == schemas(historical)
    assert json.loads(_invoke(modified)[0].text)["origin"] == "regional"
    assert json.loads(_invoke(baseline)[0].text)["origin"] == "baseline"
    assert json.loads(_invoke(historical)[0].text)["origin"] == "baseline"
    assert look.capture is original
    assert _wire(_invoke(baseline, "scene_summary")) == _wire(_invoke(modified, "scene_summary"))
    def fail(*a, **kw):
        raise ValueError("synthetic regional capture error")
    monkeypatch.setattr(module, "capture", fail)
    assert json.loads(_invoke(modified)[0].text)["status"] == "refused"
    assert look.capture is original
    assert json.loads(_invoke(baseline)[0].text)["origin"] == "baseline"


def test_baseline_json_footer_and_image_payload_equal_pre_l2b_head(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[3]
    source = subprocess.run(["git", "show", "HEAD:" + variants.LOOK_PATH], cwd=repo,
                            capture_output=True, check=True).stdout
    frozen = types.ModuleType("glasses3d.agent.tools._baseline_before_l2b")
    frozen.__package__ = look.__package__
    exec(compile(source, "HEAD:" + variants.LOOK_PATH, "exec"), frozen.__dict__)
    image = tmp_path / "work/fixture.png"
    image.parent.mkdir()
    Image.new("RGB", (16, 12), (21, 40, 83)).save(image)
    pixels = np.asarray(Image.open(image))
    rows = _rows(pixels, pixels, np.ones(pixels.shape[:2], bool))
    rows[0]["colours"]["lens"].pop("regions")
    def capture(module):
        return lambda *a, **kw: {"schema": look.SCHEMA, "status": "ok", "views": copy.deepcopy(rows),
                                "lens_colour_gap": module.lens_colour_lines(rows),
                                "images": [{"path": str(image), "view": "fixture lens"}]}
    frozen.capture = capture(frozen)
    monkeypatch.setattr(look, "capture", capture(look))
    old_scene, _ = _scene(tmp_path)
    new_scene, _ = _scene(tmp_path)
    builder = types.FunctionType(tools.build_tools.__code__, {**tools.build_tools.__globals__, "look": frozen},
                                 tools.build_tools.__name__, tools.build_tools.__defaults__, tools.build_tools.__closure__)
    before = _invoke(builder(old_scene))
    after = _invoke(variants.build_variant_tools(new_scene, "baseline"))
    assert any(isinstance(p, ToolOutputImage) for p in before)
    assert len([p for p in before if isinstance(p, ToolOutputText)]) >= 3
    assert _wire(before) == _wire(after)
    assert b"lens regions" not in _wire(after)


def test_mocked_full_capture_measures_existing_eroded_masks_saves_report_and_preserves_first_page(tmp_path, monkeypatch):
    module = variants.regional_module()
    scene, export = _scene(tmp_path)
    h, w = 21, 37
    yy, xx = np.indices((h, w))
    photo = np.stack([2 * xx + yy, 80 + xx, 200 - xx], axis=2).astype(np.uint8)
    drawing = np.stack([180 - xx, 60 + yy, 20 + xx], axis=2).astype(np.uint8)
    ids = np.zeros((h, w, 3), np.uint8)
    ids[2:19, 4:29] = [255, 0, 0]
    palette = [{"class": "lens", "rgb": [255, 0, 0], "decode_tolerance": 0}]
    refs = {f"reference-{i:02}": {"view_kind": "front" if i == 1 else "angled", "photo_sha256": "p" * 64}
            for i in range(1, 7)}
    basis_dir = tmp_path / "cameras/basis-fixture"
    basis_dir.mkdir(parents=True)
    (basis_dir / "basis.json").write_text("{}", encoding="utf-8")
    basis = SimpleNamespace(id="basis-fixture", dir=basis_dir, frame=None, refs=refs,
                            meta={"cameras_json_sha256": "c" * 64}, usable_refs=lambda: list(refs),
                            unusable_refs=lambda: [], photo=lambda rid: photo, shape=lambda rid: (h, w),
                            camera=lambda rid: None, pose=lambda rid: {}, roi=lambda rid: [0, 0, w, h])
    monkeypatch.setattr(module.C, "load_basis", lambda *a: basis)
    monkeypatch.setattr(module.C, "resample_box", lambda image, *a: image.copy())
    monkeypatch.setattr(module, "explicit_camera", lambda *a, name, **kw:
                        {"id": name, "width": w, "height": h, "meta": {"pixel_scale": 1}})
    monkeypatch.setattr(module.shutil, "which", lambda name: "mock-node")
    monkeypatch.setattr(module.look_panels, "load_geometry", lambda *a: {})
    monkeypatch.setattr(module.look_panels, "build_panels", lambda *a, **kw: {"images": [], "details": []})
    from glasses3d.g3d_host import surface_quality
    monkeypatch.setattr(surface_quality, "report", lambda *a: {"status": "measured"})
    def fake_capture(argv, directory, **kwargs):
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir()
        cameras = json.loads(Path(argv[argv.index("--cameras") + 1]).read_text(encoding="utf-8"))["cameras"]
        images = []
        for camera in cameras:
            for suffix, pixels in (("beauty", drawing), ("part-id", ids)):
                target = out / f"{camera['id']}__{suffix}.png"
                Image.fromarray(pixels).save(target)
                images.append({"file": target.name, "sha256": look.arview.sha256(target)})
        report = {"input": {"sha256": export["sha256"]}, "images": images, "id_palette": palette}
        (out / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return 0, False
    monkeypatch.setattr(module.arview, "run_capture", fake_capture)
    worn = tmp_path / "work/worn"
    worn.mkdir()
    portrait = worn / "portrait.png"
    Image.new("RGB", (8, 8), "white").save(portrait)
    worn_report = worn / "report.json"
    worn_report.write_text(json.dumps({"input": {"sha256": export["sha256"]}, "images": [{
        "file": portrait.name, "sha256": look.arview.sha256(portrait), "kind": "portrait",
        "variant": "condition-a", "region": "eyes"}]}), encoding="utf-8")
    receipt = {"report": str(worn_report), "report_sha256": look.arview.sha256(worn_report),
               "export_sha256": export["sha256"]}
    result = module.capture(tmp_path, export, "regional-fixture", worn=receipt)
    assert result["status"] == "ok", result
    saved = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert saved == {k: v for k, v in result.items() if k not in {"report", "report_sha256"}}
    assert look.arview.sha256(Path(result["report"])) == result["report_sha256"]
    # Independently declared interiors: the 25-column original lens erodes to 19 columns.
    for row in saved["views"]:
        for region, (left, right) in zip(row["colours"]["lens"]["regions"], [(7, 14), (14, 20), (20, 26)], strict=True):
            mask = np.zeros((h, w), bool)
            mask[5:16, left:right] = True
            assert region["photo"] == look.colour_stats(photo, mask)
            assert region["drawing"] == look.colour_stats(drawing, mask)
        assert "regions" not in row["colours"]["frame"]
    assert saved["lens_colour_gap"] == regional.regional_lens_colour_lines(saved["views"], look.lens_colour_lines(saved["views"]))
    envelope = {**result, "force_paging": ["x" * 1000] * 50}
    parts = tool_reports.limit_output(tmp_path, "regional-paged", "compare_look_to_photos",
                                     [ToolOutputText(text=json.dumps(envelope)), ToolOutputText(text="unchanged footer")])
    first = json.loads(parts[0].text)
    assert first["paged"] is True
    assert first["lens_colour_gap"] == saved["lens_colour_gap"]
    assert len(first["lens_colour_gap"]) == 6
    assert parts[-1].text == "unchanged footer"
    read = tool_reports.read_report(tmp_path, first["full_report"]["id"], "/result/views/0/colours/lens/regions")
    assert [e["value"] for e in read["entries"]] == saved["views"][0]["colours"]["lens"]["regions"]
