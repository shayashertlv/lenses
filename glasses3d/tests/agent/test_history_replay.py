"""Honest offline projections reject lost evidence and unexplained history changes."""
from copy import deepcopy

import pytest

from glasses3d.agent.history_replay import ReplayError, payload_units, reprice, restore_append_only
from glasses3d.agent.pricing import Tariff


def image(digest):
    return {"type": "input_image", "image_url": {"sha256": digest, "mime": "image/png", "bytes": 1}, "detail": "high"}


def photo_input():
    return {"role": "user", "content": [image(f"photo-{n}") for n in range(6)]}


PHOTOS = {f"photo-{n}" for n in range(6)}


def usage(tokens, cached=0, plain=0, output=10):
    return {"input_tokens": tokens, "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": tokens-cached-plain},
            "output_tokens": output, "output_tokens_details": {"reasoning_tokens": 5}}


def request(items):
    return {"model": "saved-model", "input": deepcopy(items)}


def output(call="call-one", digest="original"):
    return {"type": "function_call_output", "call_id": call,
            "output": [{"type": "input_text", "text": '{"status":"ok","geometry":"unchanged"}'}, image(digest)]}


def dims(_):
    return 1024, 1024


def identity(item):
    return item


def test_pruned_history_is_restored_once_and_original_requests_unchanged():
    first = request([photo_input()])
    second = request([photo_input(), output()])
    third = deepcopy(second)
    third["input"][1]["output"][1] = {"type": "input_text", "text": "[superseded render image: call old]"}
    third["input"].append({"type": "function_call", "call_id": "next", "arguments": "{}"})
    saved = [first, second, third]
    before = deepcopy(saved)
    calls = []
    def transform(item):
        calls.append(item["call_id"])
        item["output"][1] = image("compact")
        return item
    rebuilt, rewrites = restore_append_only(saved, transform)
    assert saved == before and calls == ["call-one"]
    assert rewrites == [3]
    assert rebuilt[2]["input"][:2] == rebuilt[1]["input"]
    assert rebuilt[2]["input"][1]["output"][1]["image_url"]["sha256"] == "compact"


@pytest.mark.parametrize("change", ["text", "call-id", "headers", "shortened", "unrecognized-stub"])
def test_unexplained_saved_history_changes_are_refused(change):
    a = request([photo_input(), output()])
    b = deepcopy(a)
    if change == "text":
        b["input"][1]["output"][0]["text"] = "replacement diagnostic"
    elif change == "call-id":
        b["input"][1]["call_id"] = "wrong"
    elif change == "headers":
        b["model"] = "different-model"
    elif change == "shortened":
        b["input"].pop()
    else:
        b["input"][1]["output"][1] = {"type": "input_text", "text": "image omitted"}
    with pytest.raises(ReplayError):
        restore_append_only([a, b], identity)


@pytest.mark.parametrize("change", ["identity", "two-images", "not-dict"])
def test_bad_new_policy_cannot_be_reported_as_success(change):
    def transform(item):
        if change == "identity":
            item["call_id"] = "wrong"
        elif change == "two-images":
            item["output"].append(image("extra"))
        else:
            return None
        return item
    with pytest.raises(ReplayError):
        restore_append_only([request([photo_input(), output()])], transform)


def test_six_photos_are_required_on_every_source_and_replay_request():
    a = request([photo_input()])
    a["input"][0]["content"].pop()
    with pytest.raises(ReplayError, match="authoritative photos absent"):
        reprice([a], [usage(1000)], photos=PHOTOS, transform=identity, dimensions=dims)


def test_no_payload_change_reproduces_known_prefix_cache_cost_and_keeps_output_tokens():
    reqs = [request([photo_input()]), request([photo_input(), output()])]
    usages = [usage(1000, plain=100), usage(2000, cached=900, plain=100)]
    report = reprice(reqs, usages, photos=PHOTOS, transform=identity, dimensions=dims)
    actual = sum(Tariff().settle_micro(u) for u in usages)
    assert report["observed_micro_usd"] == report["stable_prefix_micro_usd"] == actual
    assert report["zero_cache_micro_usd"] > actual
    assert report["all_photos_every_request"] and report["provider_cache_misses_measured_for_replay"] is None
    assert [r["estimated_input_tokens"] for r in report["rows"]] == [1000, 2000]
    assert report["observed_prefix_rewrites"] == []


def test_image_delta_uses_dimensions_and_does_not_treat_base64_as_text():
    a = request([photo_input(), output()])
    b = deepcopy(a)
    b["input"][1]["output"][1]["image_url"]["bytes"] = 9999999
    assert payload_units(a, dims) == payload_units(b, dims)
    def transform(item):
        item["output"][1] = image("compact")
        return item
    report = reprice([a], [usage(10000)], photos=PHOTOS, transform=transform,
                     dimensions=lambda ref: (512, 512) if ref["sha256"] == "compact" else (1024, 1024))
    # One image shrinks by 768 patches; reference photos are unchanged.
    assert report["rows"][0]["estimated_input_tokens"] == 9252


def test_metadata_sensitivity_is_cumulative_and_can_cross_long_context_threshold():
    reqs = [request([photo_input(), output()]), request([photo_input(), output(), output("second", "b")])]
    usages = [usage(270000), usage(270001, cached=270000)]
    nominal = reprice(reqs, usages, photos=PHOTOS, transform=identity, dimensions=dims)
    stress = reprice(reqs, usages, photos=PHOTOS, transform=identity, dimensions=dims, text_overhead_per_tool=5000)
    assert nominal["long_context_requests"] == []
    assert stress["long_context_requests"] == [1, 2]
    assert stress["maximum_estimated_input_tokens"] == 274201
    assert stress["stable_prefix_micro_usd"] > nominal["stable_prefix_micro_usd"]


def test_prior_provider_cache_miss_is_not_asserted_as_new_provider_savings():
    a = request([photo_input(), output()])
    b = deepcopy(a)
    b["input"][1]["output"][1] = {"type": "input_text", "text": "[superseded image: old]"}
    report = reprice([a,b], [usage(10000),usage(10001,cached=100)], photos=PHOTOS, transform=identity, dimensions=dims)
    assert report["observed_cache_losses_at_rewrites"] == [2]
    assert report["modelled_prefix_invalidation_misses"] == 0
    assert report["provider_cache_misses_measured_for_replay"] is None
    assert any("assumption" in x for x in report["assumptions"])


def test_raw_calibration_decrease_is_reported_but_pricing_uses_conservative_envelope():
    reqs = [request([photo_input()]), request([photo_input(), output()])]
    report = reprice(reqs, [usage(10000), usage(9900)], photos=PHOTOS,
                     transform=identity, dimensions=dims)
    assert report["estimated_input_decreases"] == [2]
    assert [row["raw_estimated_input_tokens"] for row in report["rows"]] == [10000, 9900]
    assert [row["estimated_input_tokens"] for row in report["rows"]] == [10000, 10000]
    assert report["rows"][1]["cumulative_max_adjustment_tokens"] == 100
    assert report["cumulative_max_adjustment_tokens"] == 100


@pytest.mark.parametrize("shape", [(0,100), (-1,100), (100.5,100)])
def test_invalid_image_dimensions_fail(shape):
    with pytest.raises(ReplayError, match="invalid image dimensions"):
        payload_units(request([photo_input()]), lambda _: shape)
