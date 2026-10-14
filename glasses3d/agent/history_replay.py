"""Offline append-only policy replay; scenario costs, never provider billing predictions.

Model replies and tool reports are held fixed. Saved provider token counts anchor each
request; only changed payload text and image dimensions are estimated. No transport,
model client, network call or application state mutation is constructed here.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from typing import Callable

from .pricing import Tariff, ceil_div, validate_usage

# Existing local temple_lab/cli.py calibration, not a tokenizer/API guarantee.
TEXT_TOKENS_PER_CHAR = Fraction(21, 50)
TOKENS_PER_PATCH = Fraction(39, 40)
PATCH_PX = 32
IMAGE_OVERHEAD = 112


class ReplayError(ValueError):
    pass


def _ceil(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def image_refs(value):
    return [item for item in walk(value) if item.get("type") == "input_image"]


def _is_saved_image_pruning(original, current) -> bool:
    """Only the known image-to-stub substitution is an allowed historical edit."""
    if original == current:
        return True
    if isinstance(original, dict) and isinstance(current, dict):
        if (original.get("type") == "input_image" and current.get("type") == "input_text"
                and isinstance(current.get("text"), str) and current["text"].startswith("[superseded ")):
            return True
        return original.keys() == current.keys() and all(
            _is_saved_image_pruning(original[key], current[key]) for key in original)
    if isinstance(original, list) and isinstance(current, list):
        return len(original) == len(current) and all(
            _is_saved_image_pruning(a, b) for a, b in zip(original, current))
    return False


def restore_append_only(requests: list[dict], transform: Callable[[dict], dict]):
    """Admit each item once; transform new tool outputs once; reject hidden history edits.

Returns reconstructed requests and observed prefix-rewrite request numbers. First-seen
images are restored even when later saved requests contain image-policy text stubs.
"""
    originals, admitted, rebuilt, rewrites = [], [], [], []
    previous = None
    headers = None
    for number, request in enumerate(requests, 1):
        items = request.get("input")
        if not isinstance(items, list):
            raise ReplayError("saved request input must be a list")
        current_headers = {key: value for key, value in request.items() if key != "input"}
        if headers is None:
            headers = deepcopy(current_headers)
        elif current_headers != headers:
            raise ReplayError(f"request {number}: non-input fields changed")
        if len(items) < len(originals):
            raise ReplayError(f"request {number}: history was shortened")
        if previous is not None and items[:len(previous)] != previous:
            rewrites.append(number)
        for index, item in enumerate(items):
            if index < len(originals):
                if not _is_saved_image_pruning(originals[index], item):
                    raise ReplayError(f"request {number}: unexplained edit to history item {index}")
                continue
            originals.append(deepcopy(item))
            new = transform(deepcopy(item)) if item.get("type") == "function_call_output" else deepcopy(item)
            if not isinstance(new, dict):
                raise ReplayError("tool policy must return an input item")
            if new.get("type") != item.get("type") or new.get("call_id") != item.get("call_id"):
                raise ReplayError("tool policy changed the call identity")
            if len(image_refs(new)) > 1 and item.get("type") == "function_call_output":
                raise ReplayError("compact policy returned more than one tool image")
            admitted.append(new)
        replayed = {**deepcopy(headers), "input": deepcopy(admitted)}
        if rebuilt and replayed["input"][:len(rebuilt[-1]["input"])] != rebuilt[-1]["input"]:
            raise ReplayError("replayed history is not append-only")
        rebuilt.append(replayed)
        previous = deepcopy(items)
    return rebuilt, rewrites


def payload_units(request: dict, dimensions: Callable[[dict], tuple[int, int]]):
    """Text serialization and empirical image units; all image bytes stay out of text counts."""
    image_units = Fraction(0)

    def stripped(value):
        nonlocal image_units
        if isinstance(value, dict):
            if value.get("type") == "input_image":
                width, height = dimensions(value["image_url"])
                if type(width) is not int or type(height) is not int or min(width, height) <= 0:
                    raise ReplayError("invalid image dimensions")
                image_units += IMAGE_OVERHEAD + TOKENS_PER_PATCH * ceil_div(width, PATCH_PX) * ceil_div(height, PATCH_PX)
                return {"type": "input_image"}
            return {key: stripped(item) for key, item in value.items()}
        if isinstance(value, list):
            return [stripped(item) for item in value]
        return value

    cleaned = stripped(request)
    chars = len(json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")))
    return chars, image_units


def _photo_check(request, photos: set[str]):
    present = [item.get("image_url", {}).get("sha256") for item in image_refs(request)]
    missing = photos.difference(present)
    if missing:
        raise ReplayError(f"authoritative photos absent: {sorted(missing)}")


def reprice(requests: list[dict], usages: list[dict], *, photos: set[str],
            transform: Callable[[dict], dict], dimensions: Callable[[dict], tuple[int, int]],
            tariff: Tariff | None = None, text_overhead_per_tool: int = 0):
    """Price recorded usage exactly, then explicit cache scenarios for counterfactual inputs.

    Stable-prefix assumption: preceding estimated input except its observed non-cacheable
    suffix is reusable; newly added input is cache-written. Zero reuse is a sensitivity
    scenario, not a bound on a real future conversation. Actual reply/usage changes, routing,
    eviction, provider image accounting and tokenizer behavior cannot be known offline.
    """
    if len(requests) != len(usages) or not requests:
        raise ReplayError("requests and usage must be nonempty and have the same length")
    if not photos or any(not isinstance(photo, str) for photo in photos):
        raise ReplayError("authoritative photo hashes are required")
    if type(text_overhead_per_tool) is not int or text_overhead_per_tool < 0:
        raise ReplayError("extra metadata sensitivity must be nonnegative integer characters")
    tariff = tariff or Tariff()
    blocks = [validate_usage(usage) for usage in usages]
    rebuilt, rewrites = restore_append_only(requests, transform)
    rows, previous, previous_plain, decreases = [], 0, 0, []
    for number, (source, target, flat, usage) in enumerate(zip(requests, rebuilt, blocks, usages), 1):
        _photo_check(source, photos)
        _photo_check(target, photos)
        old_chars, old_images = payload_units(source, dimensions)
        new_chars, new_images = payload_units(target, dimensions)
        outputs = sum(item.get("type") == "function_call_output" for item in target["input"])
        extra_chars = outputs * text_overhead_per_tool
        delta = TEXT_TOKENS_PER_CHAR * (new_chars + extra_chars - old_chars) + new_images - old_images
        raw_count = _ceil(flat["input_tokens"] + delta)
        if raw_count < 0:
            raise ReplayError("offline image/text estimate produced a negative input count")
        if number > 1 and raw_count < previous:
            decreases.append(number)
        # Image calibration and provider token counts are not perfectly interchangeable.
        # Never let that uncertainty make an append-only pricing scenario shrink.
        count = max(previous, raw_count)
        plain = flat["input_tokens"] - flat["cached_tokens"] - flat["cache_write_tokens"]
        plain = min(plain, count)
        cached = min(max(0, previous - previous_plain), count - plain)
        def scenario(cache):
            block = {"input_tokens": count, "output_tokens": flat["output_tokens"],
                     "input_tokens_details": {"cached_tokens": cache, "cache_write_tokens": count - plain - cache},
                     "output_tokens_details": {"reasoning_tokens": flat["reasoning_tokens"]}}
            return tariff.settle_micro(block)
        rows.append({"sequence": number, "observed_input_tokens": flat["input_tokens"],
                     "raw_estimated_input_tokens": raw_count,
                     "estimated_input_tokens": count, "estimated_delta_tokens": _ceil(delta),
                     "cumulative_max_adjustment_tokens": count - raw_count,
                     "observed_images": len(image_refs(source)), "replay_images": len(image_refs(target)),
                     "photos_present": len(photos), "tool_outputs": outputs,
                     "estimated_cached_tokens": cached, "estimated_cache_write_tokens": count - plain - cached,
                     "observed_micro_usd": tariff.settle_micro(usage),
                     "stable_prefix_micro_usd": scenario(cached), "zero_cache_micro_usd": scenario(0)})
        previous, previous_plain = count, plain
    return {"schema": "g3d.offline_history_replay/1", "requests": len(rows), "rows": rows,
            "observed_prefix_rewrites": rewrites, "replay_prefix_rewrites": [],
            "observed_cache_losses_at_rewrites": [n for n in rewrites if blocks[n-1]["cached_tokens"] <
                                                   blocks[n-2]["input_tokens"] - (blocks[n-2]["input_tokens"] -
                                                   blocks[n-2]["cached_tokens"] - blocks[n-2]["cache_write_tokens"])],
            "modelled_prefix_invalidation_misses": 0, "provider_cache_misses_measured_for_replay": None,
            "photo_hashes": sorted(photos), "all_photos_every_request": True,
            "text_overhead_per_tool_chars": text_overhead_per_tool,
            "estimated_input_decreases": decreases,
            "cumulative_max_adjustment_tokens": sum(row["cumulative_max_adjustment_tokens"] for row in rows),
            "maximum_estimated_input_tokens": max(row["estimated_input_tokens"] for row in rows),
            "long_context_requests": [row["sequence"] for row in rows if row["estimated_input_tokens"] > tariff.long_context_input_tokens],
            **{key: sum(row[key] for row in rows) for key in
               ("observed_micro_usd", "stable_prefix_micro_usd", "zero_cache_micro_usd")},
            "assumptions": ["Offline scenario, not provider billing or a prediction of model quality/actions.",
                            "All saved model outputs, tool order and output-token counts are held fixed.",
                            "Saved provider input counts plus empirical payload deltas; exact new tokenizer counts unknown.",
                            "Pricing uses the cumulative maximum of raw input estimates; every adjustment is reported.",
                            "Text delta uses 0.42 tokens/character; images use 0.975 per 32px patch plus112.",
                            "Original full diagnostic JSON is retained; no text-summary savings are assumed.",
                            "Saved budget notices and other model-visible numeric facts are held fixed, not regenerated.",
                            "Stable-prefix reuse is an assumption; append-only bytes do not guarantee provider cache hits.",
                            "Zero-cache sensitivity holds the same replies/requests; it is not a future-run money bound."]}
