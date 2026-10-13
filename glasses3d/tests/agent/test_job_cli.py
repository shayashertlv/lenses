"""M1 CLI integration: real job/approval files, no model clients or Blender starts."""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PIL import Image
import pytest

from glasses3d.agent import approvals as A, cli, job_run, jobs
from glasses3d.agent.budget import PaidRunBusy
from glasses3d.tests import glbfab


@pytest.fixture
def job(tmp_path):
    source = tmp_path / "photos"
    source.mkdir()
    Image.new("RGB", (23, 11), "navy").save(source / "front.png")
    views = tmp_path / "views.json"
    views.write_text(json.dumps({"front.png": {"kind": "front", "side": "both"}}))
    return jobs.create_job(images=source, views=views, name="CLI proof", description="Synthetic product.",
                           jobs_root=tmp_path / "jobs")


def command(*args):
    return cli.dispatch(cli.parser().parse_args(["job", *map(str, args)]))


def approved(job):
    request = command("estimate", job, "--turns", "2", "--cap", "1", "--settled-stop", "0.8")
    command("approve", job, "--run-id", request["run_id"], "--max-usd", "1", "--owner-words", "Offline test only.")
    return request


def test_cli_create_returns_saved_job_id_and_path(job, tmp_path, capsys):
    created_root = tmp_path / "cli-created-jobs"
    views = tmp_path / "views.json"
    assert cli.main(["job", "--jobs-root", str(created_root), "create", "--images", str(job / "images"),
                     "--views", str(views), "--name", "Created via CLI", "--description", "Exact CLI text."]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "created"
    saved = jobs.load_job(created_root / result["job_id"])
    assert saved["name"] == "Created via CLI" and saved["description"] == "Exact CLI text."


def test_create_show_estimate_and_approve_are_offline_and_do_not_consume(job):
    shown = command("show", job)
    assert shown["job"]["schema"] == "g3d.job/1" and shown["pending_run"] is None
    request = command("estimate", job)
    run_dir = job / "runs" / request["run_id"]
    assert request["effort"] == "max" and request["max_turns"] is None
    assert request["requested_cap_micro_usd"] == 23_000_000
    assert request["request"]["settings"]["settled_stop_usd"] == "20.000000"
    assert request["estimate"]["requests"] == 60
    assert command("show", job)["pending_run"] == request["run_id"]
    approval = command("approve", job, "--max-usd", "23", "--owner-words", "Offline test approval.")
    assert approval["owner_words"] == "Offline test approval."
    assert approval["request_sha256"] == request["request_sha256"]
    assert not (run_dir / "budget.json").exists()
    assert not (job / "inputs/seed.blend").exists()


def test_prepared_settings_preserve_bound_nondefault_behaviour(job):
    settings = replace(job_run.settings_default(), max_turns=7, max_output_tokens=1024,
                       settled_stop_usd="0.8", fit_output_to_budget=True, finish_k=2,
                       context_guard_tokens=150000, context_guard_bytes=2_000_000)
    spec, _ = job_run.prepare_run(job, "settings-roundtrip", settings=settings, cap="1", write_request=True)
    restored, cap = job_run.prepared_settings(job, spec.run_id)
    assert restored.approval_settings() == settings.approval_settings()
    assert cap == "1.000000"
    rebuilt, _ = job_run.prepare_run(job, spec.run_id, settings=restored, cap=cap)
    assert rebuilt.current_request_sha256 == spec.current_request_sha256


def test_missing_approval_is_refused_before_foreground_or_detached_execution(job, monkeypatch, capsys):
    request = command("estimate", job)
    execute = AsyncMock(side_effect=AssertionError("must not run"))
    detach = Mock(side_effect=AssertionError("must not detach"))
    monkeypatch.setattr(job_run, "execute_run", execute)
    monkeypatch.setattr(cli.procs, "spawn_detached", detach)
    for foreground in ([], ["--foreground"]):
        assert cli.main(["job", "run", str(job), *foreground]) == 3
        assert "approval" in json.loads(capsys.readouterr().err)["message"].lower()
    assert not execute.called and not detach.called
    assert not (job / "runs" / request["run_id"] / "budget.json").exists()


def test_approved_foreground_dispatch_preserves_run_and_resume(job, monkeypatch):
    request = approved(job)
    execute = AsyncMock(return_value={"result": {"status": "completed"}, "delivery": {"status": "delivered"}})
    monkeypatch.setattr(job_run, "execute_run", execute)
    out = command("run", job, "--run-id", request["run_id"], "--foreground", "--resume")
    assert out["result"]["status"] == "completed"
    execute.assert_awaited_once_with(job, request["run_id"], resume=True)


def test_detached_dispatch_uses_python_and_exact_approved_run(job, monkeypatch):
    request = approved(job)
    detach = Mock(return_value=SimpleNamespace(record=lambda: {"pid": 123, "method": "test"}))
    monkeypatch.setattr(cli.procs, "spawn_detached", detach)
    result = command("run", job, "--run-id", request["run_id"])
    assert result["status"] == "started" and result["run_id"] == request["run_id"]
    argv = detach.call_args.args[0]
    assert argv == [cli.sys.executable, "-m", "glasses3d", "job", "run", str(job),
                    "--run-id", request["run_id"], "--foreground"]
    assert detach.call_args.kwargs["log_path"] == job / "runs" / request["run_id"] / "runner.log"
    assert detach.call_args.kwargs["keep_awake"] is True


def test_tampered_photo_refuses_before_execution(job, monkeypatch, capsys):
    approved(job)
    (job / "images/front.png").write_bytes(b"photo changed after approval")
    execute = AsyncMock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(job_run, "execute_run", execute)
    assert cli.main(["job", "run", str(job), "--foreground"]) == 2
    assert "digest differs" in json.loads(capsys.readouterr().err)["message"]
    assert not execute.called


def test_stop_writes_request_in_correct_job(job):
    result = command("stop", job)
    assert result["status"] == "stop_requested"
    assert (job / "stop.requested").read_text() == "Owner requested stop.\n"


@pytest.mark.parametrize("option,value", [("--cap", "24"), ("--settled-stop", "21"),
                                          ("--cap", "nonsense"), ("--settled-stop", "nonsense")])
def test_invalid_money_settings_are_reported_without_traceback(job, capsys, option, value):
    assert cli.main(["job", "estimate", str(job), option, value]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "refused"
    assert not list(job.glob("runs/*/approval-request.json"))


def test_busy_paid_run_is_reported_without_launch(job, monkeypatch, capsys):
    approved(job)
    monkeypatch.setattr(cli.JobLedger, "preflight", Mock(side_effect=PaidRunBusy("another paid run is active")))
    detach = Mock(side_effect=AssertionError("must not detach"))
    monkeypatch.setattr(cli.procs, "spawn_detached", detach)
    assert cli.main(["job", "run", str(job)]) == 2
    assert "another paid run" in json.loads(capsys.readouterr().err)["message"]
    assert not detach.called


def delivery_fixture(job):
    path = job / "finished.glb"
    path.write_bytes(glbfab.build())
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    scene = SimpleNamespace(finish={"checkpoint": "c0001", "summary": "Offline fixture.",
                                    "export": {"path": str(path), "sha256": sha,
                                               "validate_spec": {"ok": True, "errors": []}, "seat_check": None}},
                            last_ar={"export_sha256": sha, "exit_code": 0, "status": "ok"})
    result = {"status": "completed", "run_id": "offline", "exit_checkpoint": {"verified": True, "id": "c0001"}}
    return scene, result


@pytest.mark.parametrize("failure", ["unverified", "wrong_checkpoint", "ar_error", "ar_corrupt"])
def test_delivery_refuses_unverified_head_or_unsuccessful_ar_even_with_zero_exit(job, failure):
    scene, result = delivery_fixture(job)
    if failure == "unverified":
        result["exit_checkpoint"]["verified"] = False
    elif failure == "wrong_checkpoint":
        result["exit_checkpoint"]["id"] = "c0002"
    else:
        scene.last_ar["status"] = "error" if failure == "ar_error" else "corrupt"
    with pytest.raises(ValueError, match="verified at shutdown|successful AR capture"):
        job_run.deliver_finished(job, scene, result)
    assert not (job / "deliverable").exists()


def test_delivery_copies_the_verified_finished_bytes_with_report(job):
    scene, result = delivery_fixture(job)
    delivery = job_run.deliver_finished(job, scene, result)
    assert delivery["status"] == "delivered"
    assert (job / "deliverable/model.glb").read_bytes() == (job / "finished.glb").read_bytes()
    report = json.loads((job / "deliverable/report.json").read_text())
    assert report["glb_sha256"] == scene.finish["export"]["sha256"] and report["checkpoint"] == "c0001"
