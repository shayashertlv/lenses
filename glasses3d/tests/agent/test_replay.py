"""The scripted api.openai.com itself (agent/replay.py): what it answers, what it records and what it refuses the way
the Responses API does. These pin the instrument the loop tests and the stage-F proofs rely on."""
import json
import math

import httpx2
import pytest

from glasses3d.agent import replay as R
from glasses3d.agent.pricing import Tariff

COUNT_URL = "https://api.openai.com/v1/responses/input_tokens"
CREATE_URL = "https://api.openai.com/v1/responses"


def post(transport, url, body):
    return transport.handle(httpx2.Request("POST", url, json=body))


def counted_create(transport, body):
    count_body = {key: value for key, value in body.items() if key in ("input", "instructions", "model", "tools")}
    counted = post(transport, COUNT_URL, count_body).json()["input_tokens"]
    return counted, post(transport, CREATE_URL, body)


def test_counts_answer_the_scripted_value_else_the_deterministic_estimate():
    transport = R.ScriptedOpenAI([R.message("a", counted=24_787), R.message("b")])
    image = "data:image/png;base64," + "A" * 4000
    body = {"model": "gpt-6-astra", "input": [{"role": "user", "content": [
        {"type": "input_text", "text": "x" * 100}, {"type": "input_image", "image_url": image}]}]}
    assert counted_create(transport, body)[0] == 24_787
    estimate = counted_create(transport, body)[0]
    stripped = json.dumps({"input": [{"content": [{"text": "x" * 100, "type": "input_text"},
                                                  {"image_url": "[image]", "type": "input_image"}],
                                      "role": "user"}], "model": "gpt-6-astra"},
                          sort_keys=True, separators=(",", ":"))
    assert estimate == math.ceil(len(stripped) / 4) + 1_500 == R.estimate_tokens(body)
    assert transport.count_answers == [24_787, estimate] and transport.violations == []


def test_a_200_carries_deterministic_ids_and_the_default_usage_prices_as_expected():
    transport = R.ScriptedOpenAI([R.tool_call("scene_summary", {"objects": "all"}, summary="Plan."),
                                  R.message("done", usage=R.default_usage(10, output_tokens=7))], id_start=41)
    counted, answer = counted_create(transport, {"model": "gpt-6-astra", "input": "go"})
    body = answer.json()
    assert answer.headers["x-request-id"] == "req_0041" and body["id"] == "resp_0041"
    assert (body["model"], body["status"], body["service_tier"]) == ("gpt-6-astra", "completed", "default")
    reasoning, call = body["output"]
    assert reasoning["id"] == "rs_0041" and reasoning["summary"][0]["text"] == "Plan."
    assert (call["id"], call["call_id"], call["name"], json.loads(call["arguments"])) == (
        "fc_0041", "call_0041", "scene_summary", {"objects": "all"})
    assert body["usage"] == R.default_usage(counted)
    assert Tariff.frozen().settle_micro(body["usage"]) == math.ceil(counted * 12.5) + 50 * 50
    second = counted_create(transport, {"model": "gpt-6-astra", "input": "go"})[1].json()
    assert second["id"] == "resp_0042" and second["usage"]["output_tokens"] == 7


def test_incomplete_and_status_replies():
    transport = R.ScriptedOpenAI([R.incomplete(), R.rate_limited(), R.origin_error(),
                                  R.http_error(500, json_body={"error": {"type": "server_error"}})])
    first = counted_create(transport, {"model": "gpt-6-astra", "input": "go"})[1]
    assert first.json()["status"] == "incomplete"
    assert first.json()["incomplete_details"] == {"reason": "max_output_tokens"}
    limited = counted_create(transport, {"model": "gpt-6-astra", "input": "go"})[1]
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "rate_limit_exceeded"
    origin = counted_create(transport, {"model": "gpt-6-astra", "input": "go"})[1]
    assert (origin.status_code, origin.content) == (520, b"error code: 520")
    assert counted_create(transport, {"model": "gpt-6-astra", "input": "go"})[1].status_code == 500
    assert transport.answered == ["incomplete", "http 429", "http 520", "http 500"]


@pytest.mark.parametrize("reply,error", [(R.drop(), httpx2.RemoteProtocolError),
                                         (R.connect_error(), httpx2.ConnectError),
                                         (R.read_timeout(), httpx2.ReadTimeout)])
def test_transport_failures_raise_after_the_request_was_recorded(reply, error):
    transport = R.ScriptedOpenAI([reply])
    post(transport, COUNT_URL, {"model": "gpt-6-astra", "input": "go"})
    with pytest.raises(error):
        post(transport, CREATE_URL, {"model": "gpt-6-astra", "input": "go"})
    assert len(transport.creates) == 1              # the request reached the provider: its outcome is unknown


def test_a_function_call_and_its_output_must_travel_together():
    call = {"type": "function_call", "call_id": "call_a", "name": "x", "arguments": "{}"}
    output = {"type": "function_call_output", "call_id": "call_a", "output": "ok"}
    orphan = {"type": "function_call_output", "call_id": "call_b", "output": "ok"}
    assert R.function_call_problems([call, output]) == []
    assert R.function_call_problems([call]) == ["No tool output found for function call call_a."]
    assert R.function_call_problems([call, output, orphan]) == [
        "No tool call found for function call output with call_id call_b."]
    transport = R.ScriptedOpenAI([R.message("never")])
    _, answer = counted_create(transport, {"model": "gpt-6-astra", "input": [call]})
    assert answer.status_code == 400 and answer.json()["error"]["type"] == "invalid_request_error"
    assert transport.violations == ["create 1: No tool output found for function call call_a."]


def test_every_create_must_follow_the_count_of_exactly_its_payload():
    transport = R.ScriptedOpenAI([R.message("a"), R.message("b"), R.message("c")])
    post(transport, CREATE_URL, {"model": "gpt-6-astra", "input": "uncounted"})
    assert transport.violations == ["create 1: 0 count calls before it (every request is counted before it is sent)"]
    post(transport, COUNT_URL, {"model": "gpt-6-astra", "input": "counted"})
    post(transport, COUNT_URL, {"model": "gpt-6-astra", "input": "other"})
    post(transport, CREATE_URL, {"model": "gpt-6-astra", "input": "different", "max_output_tokens": 5})
    assert transport.violations[-1] == "create 2: the payload differs from the one counted before it"


def test_the_script_running_out_and_unexpected_requests_are_recorded():
    transport = R.ScriptedOpenAI([])
    post(transport, COUNT_URL, {"model": "gpt-6-astra"})
    assert post(transport, CREATE_URL, {"model": "gpt-6-astra"}).status_code == 500
    assert transport.violations == ["create 1: the script has only 0 replies"]
    assert transport.handle(httpx2.Request("GET", "https://api.openai.com/v1/models")).status_code == 404
    assert post(transport, "https://example.com/v1/responses", {}).status_code == 404
    assert transport.unexpected == ["GET https://api.openai.com/v1/models", "POST https://example.com/v1/responses"]
    assert transport.exhausted


def test_input_images_finds_brief_and_tool_output_images():
    url = "data:image/png;base64,iVBORw0KGgo="
    body = {"input": [{"role": "user", "content": [{"type": "input_image", "image_url": url}]},
                      {"type": "function_call_output", "call_id": "c",
                       "output": [{"type": "input_image", "image_url": url}]}]}
    assert R.input_images(body) == [url, url] and R.input_images({"input": "text"}) == []
