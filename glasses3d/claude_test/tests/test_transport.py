"""Transport accounting proofs, entirely behind httpx2.MockTransport."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import format_datetime
import hashlib
import json
import gzip

import httpx2
import pytest

from glasses3d.claude_test.money import Ledger, consume_approval, write_approval
from glasses3d.claude_test.pricing import price_usage
from glasses3d.claude_test.transport import Refused, files_upload_params, guarded_client

MODEL = "claude-opus-5-5"
URL = "https://api.anthropic.com/v1/messages"


@pytest.fixture
def ledger_factory(tmp_path):
    count = 0

    def create(cutoff="1", model=MODEL):
        nonlocal count
        count += 1
        run_id = f"offline-{count}"
        folder = tmp_path / run_id
        path = folder / "approval.json"
        write_approval(path, words="OFFLINE TEST ONLY", amount_usd=Decimal(cutoff),
                       model=model, effort="max", run_id=run_id)
        return Ledger(folder / "money.jsonl", cutoff_usd=Decimal(cutoff),
                      approval=consume_approval(path, run_id))
    return create


def body(**extra):
    return {"model": MODEL, "stream": True, "max_tokens": 128000,
            "output_config": {"effort": "max"}, "service_tier": "standard_only",
            "messages": [{"role": "user", "content": "hello"}], **extra}


class Chunks(httpx2.SyncByteStream):
    def __init__(self, chunks, *, fail=False):
        self.chunks, self.fail = chunks, fail
        self.closed = False

    def __iter__(self):
        yield from self.chunks
        if self.fail:
            raise httpx2.ReadError("scripted disconnect")

    def close(self):
        self.closed = True


def reply(data, **kwargs):
    return httpx2.Response(200, headers={"content-type": "text/event-stream", "request-id": "req_test"},
                          stream=Chunks([data]), **kwargs)


def receipts(directory):
    return [json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.json")]


def rows(ledger):
    return [json.loads(line) for line in ledger.path.read_text(encoding="utf-8").splitlines()]


def test_cumulative_usage_crosses_cutoff_then_stops(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory("0.0005")
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return reply(sse_builder())

    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(handle)) as client:
        assert client.post(URL, json=body()).status_code == 200
        assert ledger.settled_usd() == Decimal("0.000600")
        assert ledger.money_status() == {"settled_usd": "0.0006", "cutoff_usd": "0.0005", "left_usd": "0"}
        with pytest.raises(Refused, match="cutoff"):
            client.post(URL, json=body())
    assert len(calls) == 1
    assert calls[0]["max_tokens"] == 128000
    receipt, = receipts(tmp_path / "receipts")
    assert receipt["request_id"] == "req_test"
    assert receipt["usage"] == {"input_tokens": 100, "output_tokens": 10}
    assert Decimal(receipt["cost_usd"]) == Decimal("0.0006")
    assert receipt["started"] <= receipt["finished"]
    assert receipt["http_status"] == 200
    assert receipt["stop_reason"] == "end_turn"
    assert receipt["raw_sse"] == sse_builder().decode()


def test_shared_parent_and_specialist_client_cutoff(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory("0.001")
    transport = httpx2.MockTransport(lambda request: reply(sse_builder()))
    with guarded_client(ledger, tmp_path / "parent", inner=transport) as parent, \
            guarded_client(ledger, tmp_path / "child", inner=transport) as child:
        parent.post(URL, json=body())
        child.post(URL, json=body())
        assert ledger.settled_usd() == Decimal("0.0012")
        with pytest.raises(Refused):
            parent.post(URL, json=body())


@pytest.mark.parametrize("field,value", [
    ("future_billing", 1), ("service_tier", "priority"), ("inference_geo", "us"),
    ("speed", "fast"), ("iterations", [{"type": "fallback", "model": MODEL}]),
])
def test_unpriceable_usage_stops(tmp_path, ledger_factory, sse_builder, field, value):
    ledger = ledger_factory()
    calls = []

    def handle(request):
        calls.append(request)
        return reply(sse_builder(final_usage={"output_tokens": 10, field: value}))

    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(handle)) as client:
        with pytest.raises(Refused):
            client.post(URL, json=body())
        with pytest.raises(Refused):
            client.post(URL, json=body())
    assert len(calls) == 1
    assert ledger.settled_usd() == 0
    assert rows(ledger)[-1]["event"] == "unknown"
    assert receipts(tmp_path / "receipts")[0]["outcome"] == "unknown"


@pytest.mark.parametrize("broken", ["no_final_usage", "no_stop", "sse_error", "disconnect", "wrong_model",
                                   "bad_json", "bad_utf8", "trailing_error", "missing_start_usage"])
def test_broken_stream_unknown(tmp_path, ledger_factory, sse_builder, broken):
    ledger = ledger_factory()
    data = sse_builder()
    if broken == "no_final_usage":
        data = data[:data.index(b"event: message_delta")]
    elif broken == "no_stop":
        data = sse_builder(stop=False)
    elif broken == "sse_error":
        data = b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error"}}\n\n'
    elif broken == "wrong_model":
        data = sse_builder(model="claude-fable-5-1")
    elif broken == "bad_json":
        data = b"event: message_start\ndata: nope\n\n"
    elif broken == "bad_utf8":
        data = b"\xff\n\n"
    elif broken == "trailing_error":
        data += b'event: error\ndata: {"type":"error"}\n\n'
    elif broken == "missing_start_usage":
        data = sse_builder(usage={})
    elif broken == "disconnect":
        data = data[:data.index(b"event: message_delta")]
    stream = Chunks([data], fail=broken == "disconnect")
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                          stream=stream))) as client:
        with pytest.raises(Refused):
            client.post(URL, json=body())
    assert rows(ledger)[-1]["event"] == "unknown"
    assert not ledger.may_start_request()[0]
    assert stream.closed


def test_unknown_initial_modifier_cannot_be_erased(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory()
    data = sse_builder(usage={"input_tokens": 10, "output_tokens": 1, "future_fee": 7},
                       final_usage={"output_tokens": 10, "future_fee": 0})
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(lambda request: reply(data))) as client:
        with pytest.raises(Refused):
            client.post(URL, json=body())
    assert not ledger.may_start_request()[0]


def test_cache_start_final_merge(tmp_path, ledger_factory, sse_builder):
    usage = {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 1000,
             "cache_creation_input_tokens": 500,
             "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 300},
             "service_tier": "standard", "inference_geo": "global"}
    ledger = ledger_factory()
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: reply(sse_builder(usage=usage)))) as client:
        client.post(URL, json=body())
    assert ledger.settled_usd() == price_usage(MODEL, {**usage, "output_tokens": 10})


@pytest.mark.parametrize("line_ending", [b"\n", b"\r\n", b"\r"])
def test_arbitrary_chunk_boundaries_utf8_and_sse_comments(tmp_path, ledger_factory, sse_builder, line_ending):
    ledger = ledger_factory()
    data = b": heartbeat\n\n" + sse_builder(text="שלום") + b": goodbye\n\n"
    data = data.replace(b"\n", line_ending)
    stream = Chunks([data[index:index + 1] for index in range(len(data))])
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                          stream=stream))) as client:
        assert client.post(URL, json=body()).content == data
    assert ledger.settled_usd() == Decimal("0.0006")


def test_closing_unconsumed_stream_records_unknown(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory()
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: reply(sse_builder()))) as client:
        response = client.send(client.build_request("POST", URL, json=body()), stream=True)
        with pytest.raises(Refused, match="early"):
            response.close()
    assert not ledger.may_start_request()[0]


def test_sdk_style_close_after_message_stop_settles(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory()
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: reply(sse_builder()))) as client:
        response = client.send(client.build_request("POST", URL, json=body()), stream=True)
        next(response.iter_bytes())  # consumer sees terminal event, then closes without another next()
        response.close()
        response.close()
    assert ledger.settled_usd() == Decimal("0.0006")
    assert len([row for row in rows(ledger) if row["event"] == "settled"]) == 1


@pytest.mark.parametrize("statuses", [(429, 529), (529, 429)])
def test_retry_after_resends_same_bytes_and_logs_waits(tmp_path, ledger_factory, sse_builder, statuses):
    ledger = ledger_factory()
    calls, sleeps = [], []

    def handle(request):
        calls.append(request.content)
        if len(calls) <= 2:
            return httpx2.Response(statuses[len(calls) - 1], headers={"retry-after": "2.5"},
                                  stream=Chunks([]))
        return reply(sse_builder())

    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(handle),
                        sleep=sleeps.append) as client:
        client.post(URL, json=body())
    assert sleeps == [2.5, 2.5]
    assert len(set(calls)) == 1
    assert ledger.settled_usd() == Decimal("0.0006")
    waits = [row for row in rows(ledger) if row["event"] == "wait"]
    assert len(waits) == 2
    assert all(row["assumed_unbilled_reconcile_with_console"] is True for row in waits)
    assert all(row["assumed_unbilled_reconcile_with_console"] is True
               for row in receipts(tmp_path / "receipts") if row["outcome"] == "retry")
    assert len(receipts(tmp_path / "receipts")) == 3


@pytest.mark.parametrize("status", [429, 529])
@pytest.mark.parametrize("retry_after", ["700", "3600"])
def test_wait_cap(tmp_path, ledger_factory, status, retry_after):
    ledger = ledger_factory()
    sleeps, calls = [], []

    def handle(request):
        calls.append(request)
        return httpx2.Response(status, headers={"retry-after": retry_after},
                              stream=Chunks([]))

    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(handle),
                        sleep=sleeps.append) as client:
        with pytest.raises(Refused, match="20 minutes"):
            client.post(URL, json=body())
        with pytest.raises(Refused):
            client.post(URL, json=body())
    assert sum(sleeps) == 1200
    assert len(calls) == (2 if retry_after == "700" else 1)
    assert ledger.settled_usd() == 0


@pytest.mark.parametrize("status", [429, 529])
@pytest.mark.parametrize("retry_after", [None, "", "invalid", "-1", "NaN", "Infinity", "1e2",
                                       "Wed, 99 Oct 2026 12:00:00 GMT"])
def test_missing_or_invalid_retry_after_stops_without_waiting(tmp_path, ledger_factory, status, retry_after):
    ledger = ledger_factory()
    calls, sleeps = [], []
    class Unreadable(Chunks):
        def __iter__(self):
            pytest.fail("pre-body rejection must not be read")
            yield b""  # pragma: no cover
    rejected = Unreadable([])
    def handle(request):
        calls.append(request)
        return httpx2.Response(status, headers={} if retry_after is None else {"retry-after": retry_after},
                               stream=rejected)
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(handle),
                        sleep=sleeps.append) as client:
        with pytest.raises(Refused, match="retry-after"):
            client.post(URL, json=body())
        with pytest.raises(Refused):
            client.post(URL, json=body())
    assert len(calls) == 1
    assert not sleeps
    assert rejected.closed
    assert ledger.settled_usd() == 0
    assert not any(row["event"] == "wait" for row in rows(ledger))
    assert rows(ledger)[-1]["event"] == "rejected"
    receipt, = receipts(tmp_path / "receipts")
    assert receipt["outcome"] == "rejected"
    assert "retry-after" in receipt["reason"]


def test_http_date_retry_after_is_honored(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory()
    calls, sleeps = [], []
    header = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
    def handle(request):
        calls.append(request.content)
        return (httpx2.Response(429, headers={"retry-after": header}, stream=Chunks([]))
                if len(calls) == 1 else reply(sse_builder()))
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(handle), sleep=sleeps.append) as client:
        client.post(URL, json=body())
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 60
    assert len(calls) == 2 and calls[0] == calls[1]
    assert ledger.settled_usd() == Decimal("0.0006")


@pytest.mark.parametrize("status", [301, 400, 401, 403, 408, 422, 500, 502, 503])
def test_other_http_errors_are_not_retried(tmp_path, ledger_factory, status):
    ledger = ledger_factory()
    calls = []

    def handle(request):
        calls.append(request)
        return httpx2.Response(status, headers={"location": URL}, stream=Chunks([]))

    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(handle)) as client:
        with pytest.raises(Refused, match=f"HTTP {status}"):
            client.post(URL, json=body())
        with pytest.raises(Refused):
            client.post(URL, json=body())
    assert len(calls) == 1
    assert ledger.settled_usd() == 0


@pytest.mark.parametrize("method,url", [
    ("POST", "http://api.anthropic.com/v1/messages"),
    ("POST", "https://api.anthropic.com.evil.test/v1/messages"),
    ("POST", "https://api.openai.com/v1/messages"),
    ("POST", "https://api.anthropic.com:444/v1/messages"),
    ("POST", "https://name:password@api.anthropic.com/v1/messages"),
    ("GET", URL), ("PUT", URL), ("DELETE", URL),
    ("POST", URL + "/batches"), ("POST", URL + "/"), ("POST", URL + "?unpriced=true"),
    ("POST", "https://api.anthropic.com/v1/%6dessages"),
    ("POST", "https://api.anthropic.com/v1/files/file_123/content"),
    ("GET", "https://api.anthropic.com/v1/files"),
    ("GET", "https://api.anthropic.com/v1/files?beta=true&limit=2&after_id=file_1"),
    ("GET", "https://api.anthropic.com/v1/files/file_123/content?beta=true"),
    ("DELETE", "https://api.anthropic.com/v1/files/file_123?beta=true"),
    ("PUT", "https://api.anthropic.com/v1/files/file_123"),
    ("GET", "https://api.anthropic.com/v1/models"),
])
def test_endpoint_allowlist_refuses_before_send(tmp_path, ledger_factory, method, url):
    ledger = ledger_factory()
    calls = []
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: calls.append(request))) as client:
        with pytest.raises(Refused):
            client.request(method, url, json=body())
    assert not calls
    assert ledger.may_start_request()[0]


@pytest.mark.parametrize("method,path", [
    ("POST", "/v1/messages/count_tokens"), ("POST", "/v1/files"), ("POST", "/v1/files?beta=true"),
    ("GET", "/v1/files/file_123"), ("GET", "/v1/files/file_123?beta=true"),
])
def test_documented_free_routes(tmp_path, ledger_factory, method, path):
    ledger = ledger_factory()
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: httpx2.Response(200, json={"id": "file_123", "input_tokens": 3}))) as client:
        assert client.request(method, "https://api.anthropic.com" + path, json={}).status_code == 200
    assert ledger.settled_usd() == 0
    assert ledger.may_start_request()[0]
    assert receipts(tmp_path / "receipts")[0]["cost_usd"] == "0"


def test_receipts_replace_image_data_with_hash_and_bytes(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory()
    image = b"\x89PNG\r\n\x1a\nfake image pixels"
    encoded = base64.b64encode(image).decode()
    payload = body(messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}},
    ]}])
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: reply(sse_builder(text=encoded)))) as client:
        client.post(URL, json=payload)
    for path in (tmp_path / "receipts").glob("*.json"):
        assert encoded not in path.read_text(encoding="utf-8")
    receipt, = receipts(tmp_path / "receipts")
    assert receipt["request"]["messages"][0]["content"][0]["source"]["data"] == {
        "sha256": hashlib.sha256(image).hexdigest(), "bytes": len(image)}


def test_requests_are_streaming_and_sdk_retries_disabled(tmp_path, ledger_factory):
    ledger = ledger_factory()
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(lambda request: pytest.fail("sent"))) as client:
        with pytest.raises(Refused, match="stream"):
            client.post(URL, json=body(stream=False))
        with pytest.raises(Refused, match="retries"):
            client.post(URL, json=body(), headers={"x-stainless-retry-count": "1"})
        with pytest.raises(Refused):
            client.post(URL, json=body(model="claude-fable-5-1"))
        with pytest.raises(Refused, match="effort"):
            client.post(URL, json=body(output_config={"effort": "high"}))


@pytest.mark.parametrize("field", [
    "service_tier", "speed", "inference_geo", "fallbacks", "model", "output_config", "stream",
])
@pytest.mark.parametrize("value", [None, False, 0, "", {}, []])
def test_invalid_request_selectors_refuse_before_admission(tmp_path, ledger_factory, field, value):
    ledger = ledger_factory()
    calls = []
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: calls.append(request))) as client:
        with pytest.raises(Refused):
            client.post(URL, json=body(**{field: value}))
    assert not calls
    assert not receipts(tmp_path / "receipts")
    assert len(rows(ledger)) == 1  # no request admitted and no spending authority consumed
    assert ledger.may_start_request()[0]


@pytest.mark.parametrize("overrides", [
    {"service_tier": "auto"}, {"service_tier": "standard"}, {"service_tier": "priority"},
    {"speed": "fast"}, {"inference_geo": "us"}, {"fallbacks": "default"},
    {"fallbacks": ["claude-opus-5"]}, {"model": "claude-fable-5-1"},
    {"output_config": {"effort": "high"}}, {"stream": 1},
])
def test_unapproved_request_selectors_never_reach_transport(tmp_path, ledger_factory, overrides):
    ledger = ledger_factory()
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(
            lambda request: pytest.fail("unapproved selector sent"))) as client:
        with pytest.raises(Refused):
            client.post(URL, json=body(**overrides))
    assert len(rows(ledger)) == 1


@pytest.mark.parametrize("field", ["model", "service_tier", "output_config", "stream"])
def test_required_request_selectors_must_be_present(tmp_path, ledger_factory, field):
    ledger = ledger_factory()
    request = body()
    del request[field]
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(
            lambda request: pytest.fail("missing selector sent"))) as client:
        with pytest.raises(Refused):
            client.post(URL, json=request)
    assert len(rows(ledger)) == 1


@pytest.mark.parametrize("selectors", [{}, {"speed": "standard"}, {"inference_geo": "global"},
                                      {"speed": "standard", "inference_geo": "global"}])
def test_standard_global_request_selectors_are_allowed(tmp_path, ledger_factory, sse_builder, selectors):
    ledger = ledger_factory()
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(
            lambda request: reply(sse_builder()))) as client:
        client.post(URL, json=body(**selectors))
    assert ledger.settled_usd() == Decimal("0.0006")


def test_guarded_client_under_real_anthropic_sdk(tmp_path, ledger_factory, sse_builder):
    """The pinned real SDK streams through the guard with no compatibility patches."""
    import anthropic

    ledger = ledger_factory()
    calls = []
    def handle(request):
        calls.append(json.loads(request.content))
        return reply(sse_builder())
    http_client = guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(handle))
    assert isinstance(http_client, httpx2.Client)
    try:
        with anthropic.Anthropic(api_key="offline-fake-key", http_client=http_client, max_retries=0) as client:
            with client.messages.stream(model=MODEL, max_tokens=128000,
                                        service_tier="standard_only",
                                        output_config={"effort": "max"},
                                        messages=[{"role": "user", "content": "hello"}]) as stream:
                message = stream.get_final_message()
        assert message.content[0].text == "Hello"
        assert message.usage.output_tokens == 10
        assert ledger.settled_usd() == Decimal("0.0006")
        assert len(calls) == 1
        assert calls[0]["stream"] is True
        assert calls[0]["service_tier"] == "standard_only"
        assert calls[0]["output_config"]["effort"] == "max"
    finally:
        http_client.close()


def test_file_expiration_helper_through_real_sdk_upload(tmp_path, ledger_factory):
    import anthropic

    ledger = ledger_factory()
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path, request.content))
        return httpx2.Response(200, json={"id": "file_123", "type": "file",
                                         "filename": "test.png", "mime_type": "image/png",
                                         "size_bytes": 3, "created_at": "2026-10-20T00:00:00Z",
                                         "expires_at": "2026-10-27T00:00:00Z", "downloadable": False})
    assert files_upload_params() == {"expires_in_seconds": 604800}
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(handle)) as http_client:
        with anthropic.Anthropic(api_key="offline-fake-key", http_client=http_client, max_retries=0) as client:
            uploaded = client.files.upload(file=("test.png", b"png", "image/png"), **files_upload_params())
            metadata = client.files.retrieve_metadata(uploaded.id)
    assert uploaded.id == metadata.id == "file_123"
    assert [(method, path) for method, path, _ in calls] == [
        ("POST", "/v1/files"), ("GET", "/v1/files/file_123")]
    assert b'name="expires_in_seconds"' in calls[0][2]
    assert b"\r\n604800\r\n" in calls[0][2]
    assert ledger.settled_usd() == 0
    assert all(record["outcome"] == "free" for record in receipts(tmp_path / "receipts"))


def test_close_after_incomplete_post_stop_event_is_unknown(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory()
    data = sse_builder() + b"event: error\n"
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(lambda request: reply(data))) as client:
        response = client.send(client.build_request("POST", URL, json=body()), stream=True)
        iterator = response.iter_bytes()
        next(iterator)
        with pytest.raises(Refused, match="incomplete"):
            response.close()
    assert rows(ledger)[-1]["event"] == "unknown"


@pytest.mark.parametrize("field,old,new", [
    ("input_tokens", 100, 0),
    ("cache_read_input_tokens", 50, None),
    ("iterations", [{"type": "compaction", "input_tokens": 5, "output_tokens": 5}], []),
])
def test_cumulative_usage_cannot_erase_a_charge(tmp_path, ledger_factory, sse_builder, field, old, new):
    ledger = ledger_factory()
    data = sse_builder(usage={"input_tokens": 100, "output_tokens": 1, field: old},
                       final_usage={"output_tokens": 10, field: new})
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(lambda request: reply(data))) as client:
        with pytest.raises(Refused, match="cumulative"):
            client.post(URL, json=body())
    assert rows(ledger)[-1]["event"] == "unknown"


def test_gzip_sse_settles_decoded_body(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory()
    data = sse_builder()
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream",
                                                        "content-encoding": "gzip"},
                                          stream=Chunks([gzip.compress(data)])))) as client:
        assert client.post(URL, json=body()).content == data
    assert ledger.settled_usd() == Decimal("0.0006")
    assert receipts(tmp_path / "receipts")[0]["raw_sse"] == data.decode()


def test_free_endpoint_retry_wait_is_in_ledger(tmp_path, ledger_factory):
    ledger = ledger_factory()
    calls, sleeps = [], []
    def handle(request):
        calls.append(request)
        return (httpx2.Response(429, headers={"retry-after": "2"}, stream=Chunks([]))
                if len(calls) == 1 else httpx2.Response(200, json={}))
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(handle), sleep=sleeps.append) as client:
        client.post(URL + "/count_tokens", json=body())
    assert sleeps == [2]
    assert any(row["event"] == "wait" and row["request_no"] is None
               and row["assumed_unbilled_reconcile_with_console"] is True for row in rows(ledger))
    assert ledger.settled_usd() == 0


@pytest.mark.parametrize("case", ["connection", "http_error", "non_sse", "sse"])
def test_post_send_receipt_failure_still_stops_spending(tmp_path, ledger_factory, monkeypatch, sse_builder, case):
    from glasses3d.claude_test import transport
    ledger = ledger_factory()
    def handle(request):
        if case == "connection":
            raise httpx2.ConnectError("scripted")
        if case == "http_error":
            return httpx2.Response(500, stream=Chunks([]))
        if case == "non_sse":
            return httpx2.Response(200, json={})
        return reply(sse_builder())
    def fail(*args, **kwargs):
        raise OSError("scripted receipt write failure")
    monkeypatch.setattr(transport, "write_json_atomic", fail)
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(handle)) as client:
        with pytest.raises(OSError):
            client.post(URL, json=body())
    assert not ledger.may_start_request()[0]
    assert rows(ledger)[-1]["event"] == "unknown"


def test_retry_rejection_body_is_never_read(tmp_path, ledger_factory, sse_builder):
    ledger = ledger_factory()
    calls = []
    class Unreadable(httpx2.SyncByteStream):
        def __iter__(self):
            pytest.fail("retry rejection body was read")
            yield b""  # pragma: no cover
    def handle(request):
        calls.append(request)
        return (httpx2.Response(529, headers={"retry-after": "1"}, stream=Unreadable())
                if len(calls) == 1 else reply(sse_builder()))
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(handle), sleep=lambda _: None) as client:
        client.post(URL, json=body())
    assert len(calls) == 2
    assert ledger.settled_usd() == Decimal("0.0006")


@pytest.mark.parametrize("malformed", ["duplicate", "null_stop_reason"])
def test_ambiguous_sse_is_unknown(tmp_path, ledger_factory, sse_builder, malformed):
    ledger = ledger_factory()
    data = sse_builder()
    if malformed == "duplicate":
        data = data.replace(b'"output_tokens": 10', b'"output_tokens": 100, "output_tokens": 10')
    else:
        data = data.replace(b'"stop_reason": "end_turn"', b'"stop_reason": null')
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(lambda request: reply(data))) as client:
        with pytest.raises(Refused):
            client.post(URL, json=body())
    assert rows(ledger)[-1]["event"] == "unknown"


def test_retry_rechecks_shared_cutoff_after_wait(tmp_path, ledger_factory):
    ledger = ledger_factory("0.0005")
    sibling = ledger.start_request(MODEL)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx2.Response(429, headers={"retry-after": "1"}, stream=Chunks([]))
    def settle_sibling(seconds):
        usage = {"input_tokens": 100, "output_tokens": 10}
        ledger.record_settled(sibling, "sibling_request", MODEL, usage, price_usage(MODEL, usage))
    with guarded_client(ledger, tmp_path, inner=httpx2.MockTransport(handle), sleep=settle_sibling) as client:
        with pytest.raises(Refused, match="cutoff"):
            client.post(URL, json=body())
    assert len(calls) == 1
    assert ledger.settled_usd() == Decimal("0.0006")


@pytest.mark.parametrize("details", [None, {"category": "bio"}, {"category": "other"}])
def test_refusal_settles_upper_bound_and_stops(tmp_path, ledger_factory, sse_builder, details):
    ledger = ledger_factory()
    data = sse_builder(stop_reason="refusal", stop_details=details)
    calls = []
    def handle(request):
        calls.append(request)
        return reply(data)
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            handle)) as client:
        assert client.post(URL, json=body()).status_code == 200
        assert ledger.may_start_request() == (False, "refusal")
        with pytest.raises(Refused, match="refusal"):
            client.post(URL, json=body())
    assert len(calls) == 1
    assert ledger.settled_usd() == Decimal("0.0006")
    settled = rows(ledger)[-1]
    assert settled["event"] == "settled"
    receipt, = receipts(tmp_path / "receipts")
    assert receipt["stop_reason"] == "refusal"
    assert receipt["outcome"] == "settled"
    for record in (settled, receipt):
        assert record["refusal"] is True
        assert record["upper_bound"] is True
        assert record["stop_details"] == details
        assert Decimal(record["cost_usd"]) == Decimal("0.0006")


@pytest.mark.parametrize("case", ["no_input", "no_output", "no_stop", "unpriced"])
def test_refusal_without_complete_priceable_usage_is_unknown(tmp_path, ledger_factory, sse_builder, case):
    ledger = ledger_factory()
    options = {
        "no_input": {"usage": {"output_tokens": 1}},
        "no_output": {"final_usage": {}},
        "no_stop": {"stop": False},
        "unpriced": {"final_usage": {"output_tokens": 10, "future_fee": 1}},
    }[case]
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: reply(sse_builder(stop_reason="refusal", **options)))) as client:
        with pytest.raises(Refused):
            client.post(URL, json=body())
    assert ledger.settled_usd() == 0
    assert rows(ledger)[-1]["event"] == "unknown"
    assert not ledger.may_start_request()[0]
    receipt, = receipts(tmp_path / "receipts")
    assert receipt["outcome"] == "unknown"
    assert receipt.get("upper_bound") is not True


@pytest.mark.parametrize("status,outcome", [(500, "unknown"), (200, "unknown"), (429, "rejected"), (529, "rejected")])
def test_response_close_failure_cannot_leave_spending_enabled(tmp_path, ledger_factory, status, outcome):
    ledger = ledger_factory()
    class BadClose(Chunks):
        def close(self):
            raise OSError("scripted close failure")
    with guarded_client(ledger, tmp_path / "receipts", inner=httpx2.MockTransport(
            lambda request: httpx2.Response(status, stream=BadClose([])))) as client:
        with pytest.raises(Refused, match="close failed"):
            client.post(URL, json=body())
    assert not ledger.may_start_request()[0]
    assert rows(ledger)[-1]["event"] == outcome
    assert receipts(tmp_path / "receipts")[0]["outcome"] == outcome
