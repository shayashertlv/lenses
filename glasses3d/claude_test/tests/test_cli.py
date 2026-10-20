from __future__ import annotations

import base64
import copy
from decimal import Decimal
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import socket
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import httpx2
from PIL import Image
import pytest

from glasses3d.agent import jobs
from glasses3d.claude_test import cli, money, runner, transport
from glasses3d.claude_test.scripted import ScriptedClaude


@pytest.fixture
def private_job(tmp_path):
    photos = tmp_path / "source-photos"
    photos.mkdir()
    Image.new("RGB", (32, 32), "white").save(photos / "front.png")
    views = tmp_path / "views.json"
    views.write_text(json.dumps({"front.png": {"kind": "front", "side": None}}), encoding="utf-8")
    source = jobs.create_job(images=photos, views=views, name="CLI fixture",
                             description="Owner's exact description.", jobs_root=tmp_path / "source")
    work = runner.WORK_ROOT / "tests" / ("cli-" + uuid4().hex)
    work.mkdir(parents=True)
    try:
        yield source, work / source.name
    finally:
        resolved = work.resolve()
        assert resolved.is_relative_to(runner.WORK_ROOT.resolve())
        shutil.rmtree(resolved)


def prepare(source, target, *, run_id="test-run"):
    cli.copy_job(source, target)
    return runner.prepare_run(target, settings=runner.ClaudeSettings(cutoff_usd=Decimal("1")),
                              run_id=run_id, write_request=True)


def test_copy_job_only_copies_verified_owner_inputs(private_job):
    source, target = private_job
    (source / "work").mkdir()
    (source / "work" / "private.blend").write_bytes(b"not owner input")
    (source / "approval.json").write_text("old authority", encoding="utf-8")
    copied = cli.copy_job(source, target)
    assert copied == target.resolve()
    job = jobs.load_job(target)
    assert (target / "job.json").read_bytes() == (source / "job.json").read_bytes()
    assert job["description"] == "Owner's exact description."
    names = {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}
    expected = {"job.json", "copy-provenance.json"}
    expected.update(row[key] for row in job["images"] for key in ("file", "sent_file"))
    assert names == expected
    with pytest.raises(FileExistsError):
        cli.copy_job(source, target)


def test_copy_refuses_changed_owner_bytes_and_wrong_job_id(private_job):
    source, target = private_job
    with pytest.raises(ValueError, match="job id"):
        cli.copy_job(source, target.with_name("another-id"))
    job = jobs.load_job(source)
    (source / job["images"][0]["file"]).write_bytes(b"changed")
    with pytest.raises(jobs.JobError):
        cli.copy_job(source, target)
    assert not target.exists()


def test_prepare_and_approval_bind_exact_explicit_settings(private_job, capsys):
    source, target = private_job
    assert cli.main(["prepare", "--source", str(source), "--destination", str(target),
                     "--cutoff", "1", "--run-id", "test-run"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "prepared"
    assert not (target / "runs/test-run/approval.json").exists()
    args = ["approve", "--job-dir", str(target), "--run-id", "test-run",
            "--model", "claude-opus-5-5", "--cutoff", "2", "--words", "Explicit test words"]
    assert cli.main(args) == 2
    assert not (target / "runs/test-run/approval.json").exists()
    args[args.index("2")] = "1"
    assert cli.main(args) == 0
    approval = json.loads((target / "runs/test-run/approval.json").read_text(encoding="utf-8"))
    assert approval["owner_words"] == "Explicit test words"
    assert not (target / "runs/test-run/approval.json.consumed.json").exists()
    assert cli.main(args) == 2


def test_model_effort_gate_and_parse_required_approval_words(private_job):
    source, target = private_job
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["approve", "--job-dir", str(target), "--run-id", "x",
                                      "--model", runner.MODEL, "--cutoff", "1"])
    assert cli.main(["prepare", "--source", str(source), "--destination", str(target),
                     "--cutoff", "1", "--model", "claude-fable-5-1"]) == 2


def test_status_is_read_only_and_preserves_small_settlement(private_job):
    source, target = private_job
    spec, _ = prepare(source, target)
    ledger = spec.run_dir / "ledger.jsonl"
    rows = [
        {"event": "started", "request_no": 1},
        {"event": "settled", "request_no": 1, "cost_usd": "0.00000001"},
        {"event": "started", "request_no": 2},
        {"event": "unknown", "request_no": 2, "reason": "interrupted stream"},
        {"event": "started", "request_no": 3},
        {"event": "rejected", "request_no": 3, "status": 400, "reason": "invalid body"},
    ]
    raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
    ledger.write_bytes(raw)
    record = cli.status(target, spec.run_id)
    assert record["budget"] == {"settled_usd": "0.00000001", "cutoff_usd": "1", "left_usd": "0.99999999"}
    assert record["unknown_charges"] == ["interrupted stream"]
    assert record["pending_requests"] == 0
    assert ledger.read_bytes() == raw
    assert cli.history(target.parent)[0] == record
    assert not (spec.run_dir / "approval.json").exists()


def test_stop_is_targeted_and_does_not_affect_prepared_job(private_job):
    source, target = private_job
    spec, _ = prepare(source, target)
    assert cli.request_stop(target, spec.run_id)["stop_written"] is False
    assert not (target / "stop.requested").exists()
    (spec.run_dir / "ledger.jsonl").write_text('{"event":"started","request_no":1}\n', encoding="utf-8")
    (target / "heartbeat.json").write_text('{"run_id":"different"}', encoding="utf-8")
    with pytest.raises(ValueError, match="another run"):
        cli.request_stop(target, spec.run_id)
    (target / "heartbeat.json").write_text('{"run_id":"test-run"}', encoding="utf-8")
    assert cli.request_stop(target, spec.run_id)["stop_written"] is True


def test_run_refuses_offline_preparation_before_key_or_client(private_job, monkeypatch):
    source, target = private_job
    cli.copy_job(source, target)
    spec, _ = runner.prepare_run(target, settings=runner.ClaudeSettings(cutoff_usd=Decimal("1")),
                                 run_id="test-run", write_request=True, offline=True)
    monkeypatch.setattr(runner, "_production_key", lambda *args: pytest.fail("must not read key"))
    assert cli.main(["run", "--job-dir", str(target), "--run-id", spec.run_id,
                     "--env-file", str(target / "never-read")]) == 3


def test_review_is_provider_labelled_read_only_and_stops_only_owned(private_job, monkeypatch):
    source, target = private_job
    spec, _ = prepare(source, target)
    delivered_root = target / "deliveries" / "fixture"
    delivered_root.mkdir(parents=True)
    data = (source / jobs.load_job(source)["images"][0]["file"]).read_bytes()
    (delivered_root / "panel.png").write_bytes(data)
    manifest = {"files": {"panel.png": {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}}}
    (delivered_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (spec.run_dir / "result.json").write_text(
        json.dumps({"model": runner.MODEL, "status": "completed", "tool_test": True}), encoding="utf-8")
    (spec.run_dir / "delivery.json").write_text('{"delivery_id":"fixture"}', encoding="utf-8")
    state_path = target / "review-state.json"
    state_path.write_text('{"instance":"native-owned","port":12345}', encoding="utf-8")
    native = {"url": "http://127.0.0.1:12345/verified", "server": {"state_path": str(state_path)}}
    monkeypatch.setattr(cli.delivery, "load_delivery", lambda *args: {
        "manifest": str(delivered_root / "manifest.json"), "report_data": {"run_id": spec.run_id}})
    monkeypatch.setattr(cli.review, "review_job", lambda *args, **kwargs: native)
    stopped = []
    monkeypatch.setattr(cli.review, "_stop_owned", lambda state: stopped.append(state))
    record = cli.review_run(target, spec.run_id)
    try:
        assert record["native_review"] == native
        address = urlsplit(record["page_url"])
        connection = http.client.HTTPConnection(address.hostname, address.port, timeout=3)
        connection.request("GET", "/")
        response = connection.getresponse()
        page = response.read().decode()
        assert response.status == 200
        assert "Anthropic" in page and runner.MODEL in page and "tool test" in page
        connection.request("GET", "/photos/1")
        response = connection.getresponse()
        assert response.status == 200 and response.read() == data
        connection.request("GET", "/../../.env")
        response = connection.getresponse()
        assert response.status == 404
        response.read()
        connection.request("POST", "/run")
        response = connection.getresponse()
        assert response.status == 501
        response.read()
        connection.close()
        cli.stop_review({"review_id": "not-ours"})
        assert stopped == []
    finally:
        cli.stop_review(record)
    assert stopped == [{"instance": "native-owned", "port": 12345}]
    cli.stop_review(record)
    assert len(stopped) == 1


def test_offline_fixture_still_blocks_external_sockets_and_production_http():
    with socket.socket() as sock:
        with pytest.raises(AssertionError, match="real network"):
            sock.connect(("192.0.2.1", 443))
    for module in (httpx, httpx2):
        with pytest.raises(AssertionError, match="real network"):
            module.HTTPTransport().handle_request(module.Request("GET", "https://api.anthropic.com"))


@pytest.fixture
def livecheck_job(tmp_path, monkeypatch):
    photos = tmp_path / "six-photos"
    photos.mkdir()
    labels = ("front", "side", "angled", "rear", "top", "top_oblique")
    views = {}
    for index, view in enumerate(labels):
        name = f"{index}.png"
        Image.new("RGB", (32 + index, 40 + index), (30 * index, 15, 75)).save(photos / name)
        views[name] = {"kind": view, "side": "left" if view == "side" else None}
    views_path = tmp_path / "views.json"
    views_path.write_text(json.dumps(views), encoding="utf-8")
    source = jobs.create_job(images=photos, views=views_path, name="Livecheck fixture",
                             description="Verbatim owner description.",
                             jobs_root=tmp_path / "source")
    suffix = "test-" + uuid4().hex
    check_dir = runner.WORK_ROOT / "livecheck" / ("livecheck-" + suffix)
    work = runner.WORK_ROOT / "tests" / ("livecheck-" + suffix)
    work.mkdir(parents=True)
    target = cli.copy_job(source, work / source.name)
    monkeypatch.setattr(cli, "uuid4", lambda: SimpleNamespace(hex=suffix))
    monkeypatch.setattr(runner, "_production_key", lambda *args: pytest.fail("must not read key"))
    monkeypatch.setattr(runner.JobScene, "start", lambda *args: pytest.fail("must not start Blender"))
    monkeypatch.setenv("G3D_OFFLINE", "1")
    try:
        yield target, check_dir
    finally:
        for path in (check_dir, work):
            resolved = path.resolve()
            assert resolved.is_relative_to(runner.WORK_ROOT.resolve())
            if resolved.exists():
                shutil.rmtree(resolved)


def test_livecheck_counts_the_exact_opening_and_deletes_six_uploads(livecheck_job):
    class StandardCountWire(ScriptedClaude):
        def _handle(self, request):
            if request.url.path == "/v1/messages/count_tokens":
                assert "anthropic-beta" not in request.headers
                assert not request.url.query
            return super()._handle(request)

    target, directory = livecheck_job
    wire = StandardCountWire([], token_counts=[{"input_tokens": 27461}])
    words = "Owner permits this free-only upload and token check."
    output = cli.livecheck(target, env_file=target / "never-read.env", owner_ok=words,
                          run_id="intended-run", cutoff_usd=Decimal("17"), inner=wire.transport())
    assert output["status"] == "completed", output
    assert output["input_tokens"] == 27461
    assert output["free_only"] is True and output["owner_words"] == words
    assert output["messages_requests"] == 0 and output["settled_usd"] == "0"
    assert len(output["uploads"]) == len(output["file_metadata"]) == len(output["deletions"]) == 6
    assert output["upload_cleanup"]["deleted"] == 6
    assert output["upload_cleanup"]["failed"] == []
    assert len(wire.uploads) == len(wire.deletions) == 6 and wire.requests == []
    assert all(row["deleted"] for row in wire.files.values())
    assert all(row["expires_in_seconds"] == 604800 for row in wire.uploads)
    assert [row["path"] for row in wire.calls[:6]] == ["/v1/files"] * 6
    assert [row["method"] for row in wire.calls[6:12]] == ["GET"] * 6
    assert wire.calls[12]["path"] == "/v1/messages/count_tokens"
    assert [row["method"] for row in wire.calls[13:]] == ["DELETE"] * 6
    assert len(output["http_calls"]) == 19
    assert all(row["http_status"] == 200 and row["request_id"] for row in output["http_calls"])

    spec, _ = runner.prepare_run(target, settings=runner.ClaudeSettings(cutoff_usd=Decimal("17")),
                                 run_id="intended-run", offline=True)
    opening = output["opening_request"]
    assert opening == cli.request_body(spec, opening["messages"], tools=cli.tool_definitions(spec.tools))
    assert "Verbatim owner description." in json.dumps(opening["messages"])
    assert "owner cutoff: $17" in json.dumps(opening["messages"])
    assert len(opening["tools"]) > 20 and all("strict" not in tool for tool in opening["tools"])
    counted = wire.counts[0]
    assert set(output["count_omitted_fields"]) == {
        "max_tokens", "stream", "service_tier", "inference_geo", "speed",
    }
    assert output["count_schema"] == "anthropic.types.message_count_tokens_params.MessageCountTokensParams"
    assert "context_management" not in opening and "context_management" not in counted
    assert set(counted) == set(opening) - set(output["count_omitted_fields"])
    for key in set(counted) - {"messages"}:
        assert counted[key] == opening[key]
    assert counted["output_config"] == {"effort": "max"}
    assert counted["cache_control"]["ttl"] == "1h"
    assert opening["speed"] == "standard"
    restored = copy.deepcopy(counted)
    for block, original in zip(restored["messages"][0]["content"], opening["messages"][0]["content"]):
        if block["type"] == "image":
            assert block["transformations"] == {"oversized_image": "error"}
            assert block["source"]["type"] == "base64"
            assert base64.b64decode(block["source"]["data"]) == wire.files[original["source"]["file_id"]]["bytes"]
            block["source"] = original["source"]
    assert restored["messages"] == opening["messages"]
    receipt = json.loads((directory / "livecheck.json").read_text(encoding="utf-8"))
    assert receipt == output
    assert all(isinstance(block["source"]["data"], dict) for block in receipt["count_request"]["messages"][0]["content"]
               if block["type"] == "image")
    assert not list(directory.rglob("approval*.json"))
    for path in (directory / "transport").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        assert row["free_only"] is True and row["owner_words"] == words
    assert all(runner.OFFLINE_KEY not in path.read_text(encoding="utf-8")
               for path in directory.rglob("*.json"))


def test_livecheck_guard_refuses_messages_before_mock_transport(livecheck_job, monkeypatch):
    target, directory = livecheck_job
    wire = ScriptedClaude([], token_counts=[{"input_tokens": 123}])
    actual = transport.guarded_client
    refused = []

    def checked_guard(ledger, receipts_dir, **kwargs):
        assert kwargs["free_only"] is True
        client = actual(ledger, receipts_dir, **kwargs)
        with pytest.raises(transport.Refused, match="free"):
            client.post("https://api.anthropic.com/v1/messages", json={"model": runner.MODEL})
        refused.append(True)
        return client

    monkeypatch.setattr(transport, "guarded_client", checked_guard)
    output = cli.livecheck(target, env_file=target / "never-read.env", owner_ok="Free check only.",
                          inner=wire.transport())
    assert refused == [True] and output["status"] == "completed"
    assert wire.requests == [] and all(row["path"] != "/v1/messages" for row in wire.calls)
    reopened = money.Ledger.for_livecheck(directory / "ledger.jsonl", run_id=output["id"],
                                         owner_words="Free check only.")
    assert reopened.may_start_request()[0] is False and reopened.settled_usd() == Decimal(0)


@pytest.mark.parametrize("failure", ["metadata", "count", "upload"])
def test_livecheck_deletes_uploads_when_a_free_step_fails(livecheck_job, failure):
    target, directory = livecheck_job
    wire = ScriptedClaude([], token_counts=[{"input_tokens": 123}])
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        fail = ((failure == "metadata" and request.method == "GET")
                or (failure == "count" and request.url.path.endswith("/count_tokens"))
                or (failure == "upload" and request.method == "POST" and request.url.path == "/v1/files"
                    and len(wire.uploads) == 2))
        if fail:
            return httpx2.Response(400, headers={"request-id": "req_free_failure"},
                                   json={"type": "error", "error": {"type": "invalid_request_error",
                                                                  "message": "Offline rejection"}})
        return wire(request)

    output = cli.livecheck(target, env_file=target / "never-read.env", owner_ok="Free check only.",
                          inner=httpx2.MockTransport(handle))
    assert output["status"] == "failed"
    expected = 2 if failure == "upload" else 6
    assert len(wire.uploads) == len(wire.deletions) == output["upload_cleanup"]["deleted"] == expected
    assert all(row["deleted"] for row in wire.files.values())
    assert output["error"]["http_status"] == 400 and output["error"]["request_id"] == "req_free_failure"
    assert output["messages_requests"] == 0 and output["settled_usd"] == "0"
    assert any(row["http_status"] == 400 for row in output["http_calls"])
    assert json.loads((directory / "livecheck.json").read_text(encoding="utf-8")) == output


def test_livecheck_failed_delete_is_recorded_once_with_expiry(livecheck_job):
    target, _ = livecheck_job
    wire = ScriptedClaude([], token_counts=[{"input_tokens": 123}], delete_statuses=[503])
    output = cli.livecheck(target, env_file=target / "never-read.env", owner_ok="Free check only.",
                          inner=wire.transport())
    assert output["status"] == "completed" and output["input_tokens"] == 123
    assert output["upload_cleanup"]["deleted"] == 5 and len(output["upload_cleanup"]["failed"]) == 1
    failed = output["upload_cleanup"]["failed"][0]
    assert failed["expires_at"] and failed["file_id"] == wire.deletions[0]["file_id"]
    assert len(wire.deletions) == 6
    assert output["messages_requests"] == 0 and output["settled_usd"] == "0"


def test_livecheck_command_requires_owner_words_and_passes_exact_arguments(livecheck_job, monkeypatch, capsys):
    target, _ = livecheck_job
    args = ["livecheck", str(target), "--env-file", str(target / "never-read.env")]
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(args)
    captured = []

    def capture(job_dir, **kwargs):
        captured.append((job_dir, kwargs))
        return {"status": "completed", "id": "test", "receipt": "livecheck.json", "input_tokens": 123,
                "messages_requests": 0, "settled_usd": "0", "upload_cleanup": {"failed": []}}

    monkeypatch.setattr(cli, "livecheck", capture)
    assert cli.main([*args, "--owner-ok", "Exact owner words.", "--run-id", "intended", "--cutoff", "9"]) == 0
    assert captured == [(target, {"env_file": target / "never-read.env", "owner_ok": "Exact owner words.",
                                 "run_id": "intended", "cutoff_usd": Decimal("9")})]
    assert json.loads(capsys.readouterr().out)["messages_requests"] == 0


def test_livecheck_requires_owner_words_and_six_photos_before_key_or_network(private_job, monkeypatch):
    source, target = private_job
    cli.copy_job(source, target)
    monkeypatch.setattr(runner, "_production_key", lambda *args: pytest.fail("must not read key"))
    with pytest.raises(ValueError, match="explicit words"):
        cli.livecheck(target, env_file=target / "never-read.env", owner_ok="  ")
    with pytest.raises(ValueError, match="six owner photos"):
        cli.livecheck(target, env_file=target / "never-read.env", owner_ok="Free check only.")
