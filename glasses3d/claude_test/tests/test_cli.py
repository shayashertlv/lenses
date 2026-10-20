from __future__ import annotations

from decimal import Decimal
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import socket
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import httpx2
from PIL import Image
import pytest

from glasses3d.agent import jobs
from glasses3d.claude_test import cli, runner


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
