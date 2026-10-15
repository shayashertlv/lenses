"""Image-only estimates preserve receipt anchors, cache partitions and append-only histories."""
from copy import deepcopy

import pytest

from glasses3d.agent.look_cost import documented_image, price_history, image_wire_bytes
from glasses3d.agent.history_replay import ReplayError


@pytest.mark.parametrize("size,processed,tokens", [((1024,1024),(1024,1024),1229),
    ((2048,2048),(1600,1600),3000), ((4096,512),(4096,512),2458),
    ((2560,1280),(2240,1120),2940), ((2048,1365),(1920,1280),2880), ((512,512),(512,512),308)])
def test_official_high_examples_and_no_additive_overhead(size, processed, tokens):
    result = documented_image(*size)
    assert result["processed_dimensions"] == list(processed) and result["tokens"] == tokens


def test_high_no_upscale_and_original_rejection():
    assert documented_image(10,10)["tokens"] == 2
    assert documented_image(10000,10000,"original")["would_reject"]
    assert documented_image(10000,10000)["tokens"] <= 3000


@pytest.mark.parametrize("detail", ["high", "original", "auto"])
def test_oversized_source_edges_are_explicitly_outside_offline_estimator(detail):
    with pytest.raises(ValueError, match="above 65535"):
        documented_image(1000000,1,detail)


def image(digest, size=100):
    return {"type":"input_image","detail":"high","image_url":{"sha256":digest,"mime":"image/png","bytes":size}}


def usage(count,cached=0,written=None):
    return {"input_tokens":count,"output_tokens":10,"input_tokens_details":{"cached_tokens":cached,
            "cache_write_tokens":count-cached-100 if written is None else written},
            "output_tokens_details":{"reasoning_tokens":2}}


def scenario(base=10000):
    photo={"role":"user","content":[image("photo")]}
    call={"type":"function_call_output","call_id":"call", "output":[{"type":"input_text","text":"keep exact text"}, image("old")]}
    reqs=[{"model":"gpt-6-astra","input":[photo]}, {"model":"gpt-6-astra","input":[photo,call]},
          {"model":"gpt-6-astra","input":[photo,call,{"type":"function_call","call_id":"next","arguments":"{}"}]}]
    usages=[usage(base),usage(base+2000,cached=base-100),usage(base+2500,cached=base+1900)]
    manifest={key:{"dimensions":shape} for key,shape in {"photo":[1024,1024],"old":[512,512],"a":[1024,1024],"b":[512,512]}.items()}
    def replace(item):
        item["output"][1:]=[image("a"),image("b")]
        return item
    return reqs,usages,manifest,replace


def test_new_images_written_once_then_cached_and_actual_usage_anchors_unchanged():
    reqs,usages,manifest,transform=scenario()
    original=deepcopy(reqs)
    result=price_history(reqs,usages,transform=transform,manifest=manifest)
    assert reqs==original
    assert [row["image_delta_tokens"] for row in result["rows"]]==[0,1229,1229]
    assert [row["first_use_cache_write_delta_tokens"] for row in result["rows"]]==[0,1229,0]
    assert [row["stable_cached_delta_tokens"] for row in result["rows"]]==[0,0,1229]
    assert result["rows"][1]["new_usage_image_only"]["cache_write_tokens"]==3229
    assert result["rows"][1]["stable_image_delta_micro_usd"]==15363
    assert result["rows"][2]["stable_image_delta_micro_usd"]==1229
    assert result["rows"][1]["new_image_count"]==3
    assert result["input_requests"][2]["input"][:2]==result["input_requests"][1]["input"]
    assert result["totals"]["no_cached_reads_all_writes_micro_usd"]>result["totals"]["stable_prefix_micro_usd"]


def test_long_threshold_is_assessed_on_whole_anchored_request():
    reqs,usages,manifest,transform=scenario(270000)
    result=price_history(reqs,usages,transform=transform,manifest=manifest)
    assert result["long_context_sequences"]==[2,3]
    assert result["rows"][1]["long_context_uplift_micro_usd"]>0


def test_identity_reproduces_every_receipt_partition_and_does_not_invent_savings():
    reqs,usages,manifest,_=scenario()
    result=price_history(reqs,usages,transform=lambda item:item,manifest=manifest)
    assert result["totals"]["stable_image_delta_micro_usd"]==0
    assert result["totals"]["stable_prefix_micro_usd"]==result["totals"]["observed_micro_usd"]


def test_unexplained_history_rewrite_still_refuses_multiple_image_policy():
    reqs,usages,manifest,transform=scenario()
    reqs[2]["input"][1]=deepcopy(reqs[2]["input"][1])
    reqs[2]["input"][1]["output"][0]["text"]="changed prior statement"
    with pytest.raises(ReplayError,match="unexplained edit"):
        price_history(reqs,usages,transform=transform,manifest=manifest)


def test_base64_wire_length_accounts_for_new_image_bytes():
    a={"input":[image("a",3)]}; b={"input":[image("a",6)]}
    assert image_wire_bytes(b)-image_wire_bytes(a)==4
