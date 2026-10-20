"""Offline, native-pixel proofs; optional owner evidence is read only."""

from dataclasses import replace
import hashlib
import io
import json
import math
from pathlib import Path
import random
import struct
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import pytest

from glasses3d.claude_test import images


def encoded(image, fmt="PNG", **kwargs):
    output = io.BytesIO()
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def pattern(w, h, mode="RGB"):
    return Image.frombytes(mode, (w, h), random.Random(501).randbytes(w * h * len(mode)))


def assert_pixels_and_grid(data, tiles, label):
    with Image.open(io.BytesIO(data)) as source:
        source.load()
        coverage = Image.new("1", source.size)
        xs = sorted({(t.x0, t.x0 + t.w) for t in tiles})
        ys = sorted({(t.y0, t.y0 + t.h) for t in tiles})
        assert len(tiles) == len(xs) * len(ys)
        assert [(t.y0, t.x0) for t in tiles] == [(y[0], x[0]) for y in ys for x in xs]
        for bounds, extent in ((xs, source.width), (ys, source.height)):
            assert bounds[0][0] == 0
            assert bounds[-1][1] == extent
            assert max(b - a for a, b in bounds) - min(b - a for a, b in bounds) <= 1
            assert all(left[1] - right[0] == 32 for left, right in zip(bounds, bounds[1:]))
        for t in tiles:
            assert images.fits(t.w, t.h)
            assert t.sha256 == hashlib.sha256(t.png).hexdigest()
            box = (t.x0, t.y0, t.x0 + t.w, t.y0 + t.h)
            with Image.open(io.BytesIO(t.png)) as decoded:
                assert decoded.size == (t.w, t.h)
                assert decoded.mode == source.mode
                assert decoded.tobytes() == source.crop(box).tobytes()
                # Palette transparency must survive too.
                assert decoded.convert("RGBA").tobytes() == source.crop(box).convert("RGBA").tobytes()
            coverage.paste(1, box)
            if len(tiles) == 1:
                assert t.label == label
            else:
                r, c = ys.index((t.y0, t.y0 + t.h)) + 1, xs.index((t.x0, t.x0 + t.w)) + 1
                assert t.label == (
                    f"{label} - tile {r}/{c} of {len(ys)}x{len(xs)} - "
                    f"{box[0]},{box[1]}-{box[2]},{box[3]} px of {source.width}x{source.height}"
                )
        assert coverage.getextrema() == (1, 1)


@pytest.mark.parametrize("w,h,tokens", [(1, 1, 1), (28, 28, 1), (29, 28, 2),
                                       (1920, 1280, 3174), (1932, 1932, 4761)])
def test_visual_tokens(w, h, tokens):
    assert images.visual_tokens(w, h) == tokens


@pytest.mark.parametrize("w,h", [(0, 1), (1, -1), (1.5, 28), (True, 28), ("28", 28)])
def test_invalid_dimensions(w, h):
    assert not images.fits(w, h)
    with pytest.raises(ValueError, match="positive integers"):
        images.visual_tokens(w, h)


def test_independent_dimension_boundaries():
    assert images.fits(2000, 28)
    assert not images.fits(2001, 28)
    assert images.fits(2001, 28, many=False)
    assert images.fits(2576, 28, many=False)
    assert not images.fits(2577, 28, many=False)
    assert images.fits(1932, 1932)
    assert not images.fits(1933, 1933, many=False)
    assert images.fits(1932, 1932) == images.fits(1932, 1932, many=False)
    assert images.visual_tokens(1456, 2576) == 4784
    assert images.fits(1456, 2576, many=False)
    assert not images.fits(1457, 2576, many=False)


@pytest.mark.parametrize("w,h,count", [(2924, 864, 2), (2016, 560, 2), (1920, 1280, 1),
                                      (4000, 4000, 9), (1, 9000, 5)])
def test_native_synthetic_cases(w, h, count):
    data = encoded(pattern(w, h))
    tiles = images.tile(data, "comparison")
    assert len(tiles) == count
    assert_pixels_and_grid(data, tiles, "comparison")
    assert images.tile(data, "comparison") == tiles


def test_exact_panel_labels():
    data = encoded(Image.new("RGB", (2924, 864)))
    assert [t.label for t in images.tile(data, "front")] == [
        "front - tile 1/1 of 1x2 - 0,0-1478,864 px of 2924x864",
        "front - tile 1/2 of 1x2 - 1446,0-2924,864 px of 2924x864",
    ]


@pytest.mark.parametrize("w,h", [(3900, 1900), (1900, 3900), (1933, 1933), (5000, 31),
                                (31, 5000), (3500, 2700), (2700, 3500), (12000, 80)])
def test_minimal_grid_against_exhaustive_oracle(w, h):
    tiles = images.prepare(encoded(Image.new("L", (w, h))), "grid")
    n = len(tiles)
    candidates = []
    for rows in range(1, n + 1):
        for cols in range(1, n // rows + 1):
            tw = math.ceil((w + 32 * (cols - 1)) / cols)
            th = math.ceil((h + 32 * (rows - 1)) / rows)
            # Independent arithmetic, not fits() or the production grid helper.
            if max(tw, th) <= 2000 and math.ceil(tw / 28) * math.ceil(th / 28) <= 4784:
                candidates.append((rows * cols, rows if w >= h else cols))
    actual_rows, actual_cols = len({t.y0 for t in tiles}), len({t.x0 for t in tiles})
    assert (n, actual_rows if w >= h else actual_cols) == min(candidates)


@pytest.mark.parametrize("w,h", [(1920, 1280), (1448, 1086), (1536, 1024)])
@pytest.mark.parametrize("operation", [images.prepare, images.tile])
def test_owner_photo_sizes_untouched(w, h, operation, monkeypatch):
    data = encoded(Image.new("RGB", (w, h), (29, 130, 244)))

    def forbidden(*args, **kwargs):
        raise AssertionError("fitting image must not be encoded or resampled")

    monkeypatch.setattr(Image.Image, "save", forbidden)
    monkeypatch.setattr(Image.Image, "resize", forbidden)
    result = operation(data, "owner photo")
    assert result == [images.Tile(data, w, h, 0, 0, "owner photo", hashlib.sha256(data).hexdigest())]
    assert result[0].png is data


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP", "GIF"])
def test_supported_formats_preserve_original_bytes(fmt):
    data = encoded(pattern(63, 47), fmt)
    prepared = images.prepare(data, fmt)
    assert prepared[0].png is data
    assert_pixels_and_grid(data, prepared, fmt)


@pytest.mark.parametrize("fmt", ["BMP", "TIFF"])
def test_other_formats_reencode_losslessly(fmt):
    data = encoded(pattern(63, 47), fmt)
    prepared = images.prepare(data, fmt)
    assert prepared[0].png.startswith(b"\x89PNG\r\n\x1a\n")
    assert prepared[0].png != data
    assert_pixels_and_grid(data, prepared, fmt)
    assert images.prepare(data, fmt) == prepared


@pytest.mark.parametrize("fmt,mode", [("WEBP", "RGBA"), ("JPEG", "RGB"),
                                    ("PNG", "RGBA"), ("GIF", "P")])
def test_crops_preserve_decoded_channels_and_transparency(fmt, mode, monkeypatch):
    source = pattern(2016, 64, mode)
    if mode == "P":
        source.putpalette([v for i in range(256) for v in (i, 255 - i, i // 2)])
        source.info["transparency"] = 7
    data = encoded(source, fmt)

    def forbidden(*args, **kwargs):
        raise AssertionError("native preparation must not resize")

    monkeypatch.setattr(Image.Image, "resize", forbidden)
    tiles = images.prepare(data, "native")
    assert len(tiles) == 2
    assert_pixels_and_grid(data, tiles, "native")


def test_oversized_cmyk_refuses_lossy_mode_conversion():
    data = encoded(Image.new("CMYK", (2016, 32)), "JPEG")
    with pytest.raises(ValueError, match="without changing pixel"):
        images.prepare(data, "CMYK")


@pytest.mark.parametrize("fmt,mode", [("PNG", "I;16"), ("TIFF", "I;16"), ("TIFF", "I;16B")])
def test_native_16_bit_values_survive_png_crops(fmt, mode):
    source = Image.frombytes(mode, (2016, 32), random.Random(18).randbytes(2016 * 32 * 2))
    data = encoded(source, fmt)
    prepared = images.prepare(data, "16 bit")
    assert len(prepared) == 2
    for t in prepared:
        with Image.open(io.BytesIO(t.png)) as decoded:
            expected = source.crop((t.x0, t.y0, t.x0 + t.w, t.y0 + t.h))
            endian = ">" if mode == "I;16B" else "<"
            assert struct.unpack(f"{endian}{t.w * t.h}H", expected.tobytes()) == struct.unpack(
                f"<{t.w * t.h}H", decoded.tobytes())


@pytest.mark.parametrize("size", [(32, 32), (2016, 32)])
def test_animated_gif_is_kept_or_refused_without_frame_loss(size):
    data = encoded(Image.new("RGB", size, "red"), "GIF", save_all=True,
                   append_images=[Image.new("RGB", size, "blue")], duration=80, loop=0)
    if size[0] == 32:
        assert images.prepare(data, "animation")[0].png is data
    else:
        with pytest.raises(ValueError, match="losing frames"):
            images.prepare(data, "animation")


def test_upload_cache_persists_and_reuses_across_existing_instances(tmp_path):
    path = tmp_path / "cache" / "files.json"
    calls = []

    def upload(data, filename):
        calls.append((data, filename))
        return f"file-{len(calls)}"

    first = images.FileCache(path, upload)
    second = images.FileCache(path, upload)
    t = images.prepare(encoded(pattern(40, 30)), "one")[0]
    assert first.file_id_for(t) == "file-1"
    assert second.file_id_for(replace(t, label="other label")) == "file-1"
    assert images.FileCache(path, upload).file_id_for(t) == "file-1"
    other = images.prepare(encoded(Image.new("RGB", (40, 30))), "two")[0]
    assert second.file_id_for(other) == "file-2"
    assert first.file_id_for(other) == "file-2"
    assert calls == [(t.png, f"{t.sha256}.png"), (other.png, f"{other.sha256}.png")]
    assert json.loads(path.read_text()) == {
        t.sha256: {"file_id": "file-1", "w": 40, "h": 30, "bytes": len(t.png)},
        other.sha256: {"file_id": "file-2", "w": 40, "h": 30, "bytes": len(other.png)},
    }
    assert not list(path.parent.glob("*.tmp"))


def test_concurrent_cache_instances_upload_once(tmp_path):
    calls = []
    caches = [images.FileCache(tmp_path / "files.json", lambda *args: calls.append(args) or "file-one")
              for _ in range(4)]
    t = images.prepare(encoded(Image.new("RGB", (12, 14))), "tiny")[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda cache: cache.file_id_for(t), caches)) == ["file-one"] * 4
    assert len(calls) == 1


@pytest.mark.parametrize("fmt,suffix", [("JPEG", "jpg"), ("WEBP", "webp"), ("GIF", "gif")])
def test_upload_uses_actual_format_extension(tmp_path, fmt, suffix):
    t = images.prepare(encoded(Image.new("RGB", (5, 5)), fmt), fmt)[0]
    calls = []
    cache = images.FileCache(tmp_path / "files.json", lambda *args: calls.append(args) or "file-x")
    assert cache.file_id_for(t) == "file-x"
    assert calls == [(t.png, f"{t.sha256}.{suffix}")]


def test_bad_tiles_and_byte_cap_refuse_before_upload(tmp_path, monkeypatch):
    calls = []
    cache = images.FileCache(tmp_path / "files.json", lambda *args: calls.append(args) or "file-x")
    t = images.prepare(encoded(Image.new("RGB", (5, 5))), "tiny")[0]
    for invalid in (replace(t, sha256="bad"), replace(t, w=6)):
        with pytest.raises(ValueError):
            cache.file_id_for(invalid)
    monkeypatch.setitem(images.LIMITS, "max_image_bytes", len(t.png) - 1)
    with pytest.raises(ValueError, match="bytes"):
        cache.file_id_for(t)
    monkeypatch.setitem(images.LIMITS, "max_image_bytes", len(t.png))
    assert cache.file_id_for(t) == "file-x"
    assert len(calls) == 1


def test_failed_upload_is_not_cached(tmp_path):
    path = tmp_path / "files.json"
    t = images.prepare(encoded(Image.new("RGB", (5, 5))), "tiny")[0]

    def fail(*args):
        raise RuntimeError("fake upload failed")

    with pytest.raises(RuntimeError, match="fake upload"):
        images.FileCache(path, fail).file_id_for(t)
    assert not path.exists()
    with pytest.raises(ValueError, match="file_id"):
        images.FileCache(path, lambda *args: "").file_id_for(t)
    assert not path.exists()


@pytest.mark.parametrize("contents", ["{broken", "[]", '{"bad": {}}'])
def test_corrupt_cache_refuses_instead_of_reuploading(tmp_path, contents):
    path = tmp_path / "files.json"
    path.write_text(contents)
    t = images.prepare(encoded(Image.new("RGB", (5, 5))), "tiny")[0]
    calls = []
    with pytest.raises(ValueError):
        images.FileCache(path, lambda *args: calls.append(args) or "file-x").file_id_for(t)
    assert calls == []
    assert path.read_text() == contents


def body_with(*blocks):
    return {"messages": [{"role": "user", "content": list(blocks)}]}


def tool_result(*blocks):
    return {"type": "tool_result", "tool_use_id": "test-call", "content": list(blocks)}


def test_blocks_and_nested_valid_request():
    assert images.image_block("file-x") == {
        "type": "image", "source": {"type": "file", "file_id": "file-x"}}
    assert images.label_block("front") == {"type": "text", "text": "front"}
    body = body_with(images.label_block("front"), images.image_block("file-x"),
                     tool_result(images.label_block("compare"),
                                 tool_result(images.image_block("file-y"))))
    assert images.check_request(body, {"file-x": (1920, 1280), "file-y": (1448, 1086)}) == []


@pytest.mark.parametrize("nested", [False, True])
def test_inline_base64_refused(nested):
    block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}
    problems = images.check_request(body_with(tool_result(block) if nested else block), {})
    assert len(problems) == 1 and "inline base64" in problems[0]


def test_many_threshold_counts_all_occurrences_including_nested_results():
    image = images.image_block("file-x")
    body = body_with(*([image] * 20))
    dims = {"file-x": (2001, 28)}
    assert images.check_request(body, dims) == []
    body["messages"].append({"role": "user", "content": [tool_result(image)]})
    problems = images.check_request(body, dims)
    assert len(problems) == 21
    assert all("many=True" in p for p in problems)
    assert images.check_request(body, {"file-x": (2000, 28)}) == []


def test_600_allowed_and_601_refused_even_in_tool_result():
    block = images.image_block("file-x")
    assert images.check_request(body_with(tool_result(*([block] * 600))), {"file-x": (1, 1)}) == []
    problems = images.check_request(body_with(block, tool_result(*([block] * 600))), {"file-x": (1, 1)})
    assert problems == ["request has 601 images; maximum is 600"]


@pytest.mark.parametrize("dims", [(2577, 28), (1933, 1933), (0, 10), (True, 28)])
def test_dimensions_refused_in_small_request(dims):
    problems = images.check_request(body_with(images.image_block("file-x")), {"file-x": dims})
    assert len(problems) == 1 and "dimensions" in problems[0]


@pytest.mark.parametrize("source", [None, {"type": "file"}, {"type": "url", "url": "data:image/png;base64,AA=="},
                                   {"type": "url", "url": "https://example.invalid/image.png"}])
def test_unverifiable_source_refused(source):
    assert images.check_request(body_with({"type": "image", "source": source}), {})


def test_missing_dimensions_refused():
    assert "no known dimensions" in images.check_request(body_with(images.image_block("file-x")), {})[0]


@pytest.mark.parametrize("size_delta", [-1, 0, 1])
def test_serialized_request_limit_is_strict(size_delta):
    body = body_with(images.label_block(""))
    overhead = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    body["messages"][0]["content"][0]["text"] = "a" * (
        images.LIMITS["max_request_bytes"] + size_delta - overhead)
    problems = images.check_request(body, {})
    assert bool(problems) == (size_delta >= 0)
    if problems:
        assert "bytes; must be below 32000000" in problems[0]


def test_request_size_counts_utf8_bytes(monkeypatch):
    body = body_with(images.label_block("שלום 😀"))
    size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    monkeypatch.setitem(images.LIMITS, "max_request_bytes", size)
    assert images.check_request(body, {})
    monkeypatch.setitem(images.LIMITS, "max_request_bytes", size + 1)
    assert images.check_request(body, {}) == []


@pytest.mark.parametrize("value", [float("nan"), object()])
def test_non_json_request_returns_problem(value):
    assert "not serializable JSON" in images.check_request({"messages": [], "extra": value}, {})[0]


# One read-only sample per real evidence size; no bytes are saved or added to Git.
_BLOBS = Path(__file__).resolve().parents[2] / "data/jobs/oakley-oo9208-v2-911a2b0e/blobs"


def _real_samples():
    sizes = {}
    for path in sorted(_BLOBS.glob("*.png")):
        with Image.open(path) as image:
            sizes.setdefault(image.size, path)
    return list(sizes.values())


@pytest.mark.parametrize("path", _real_samples() or [
    pytest.param(None, marks=pytest.mark.skip(reason="local Oakley PNG evidence is absent"))
], ids=lambda path: path.name[:16] if path is not None else "absent")
def test_real_oakley_evidence_sizes(path):
    original = path.read_bytes()
    tiles = images.prepare(original, path.stem)
    assert_pixels_and_grid(original, tiles, path.stem)
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(original).digest()
