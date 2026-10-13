"""M1 CLI integration: real job/approval files, no model clients or Blender starts."""
from dataclasses import replace
import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from PIL import Image
import pytest

from glasses3d.agent import approvals as A, cli, job_run, jobs
from glasses3d.agent.budget import PaidRunBusy


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
    assert request["request"]["settings"]["context_guard"]["every_requests"] == 3
    assert command("show", job)["pending_run"] == request["run_id"]
    approval = command("approve", job, "--max-usd", "23", "--owner-words", "Offline test approval.")
    assert approval["owner_words"] == "Offline test approval."
    assert approval["request_sha256"] == request["request_sha256"]
    assert not (run_dir / "budget.json").exists()
    assert not (job / "inputs/seed.blend").exists()


def test_show_labels_draft_and_keeps_failed_checks_visible(job, monkeypatch):
    pointer = job / "deliveries/current.json"
    pointer.parent.mkdir()
    pointer.write_text("{}")
    report = {"finished": False, "accepted_finish": False, "run_status": "budget_limit",
              "validate_spec": {"ok": False, "errors": ["fixture mesh is open"]},
              "seating": {"ok": False, "failures": ["lens does not seat"]},
              "print_check": {"ok": True}, "arview": {"status": "spec_failed", "exit_code": 3}}
    monkeypatch.setattr(cli.delivery, "load_delivery", lambda root: {
        "delivery_id": "draft-id", "glb_sha256": "a" * 64, "report": "immutable/report.json", "report_data": report})
    shown = command("show", job)["delivery"]
    assert shown["status"] == "draft" and shown["finished"] is shown["accepted_finish"] is False
    assert shown["run_status"] == "budget_limit"
    assert shown["checks"] == {key: report[key] for key in ("validate_spec", "seating", "print_check", "arview")}


def test_show_reports_a_no_usable_checkpoint_attempt_without_inventing_delivery(job):
    attempt = job / "runs/no-scene/delivery.json"
    attempt.parent.mkdir(parents=True)
    record = {"status": "no_usable_checkpoint", "run_status": "turn_limit", "reason": "empty scene"}
    attempt.write_text(json.dumps(record))
    shown = command("show", job)
    assert shown["delivery"] is None and shown["last_delivery_attempt"] == record


def test_prepared_settings_preserve_bound_nondefault_behaviour(job):
    settings = replace(job_run.settings_default(), max_turns=7, max_output_tokens=1024,
                       settled_stop_usd="0.8", fit_output_to_budget=True, finish_k=2,
                       context_guard_tokens=150000, context_guard_bytes=2_000_000, context_guard_every_requests=4)
    spec, _ = job_run.prepare_run(job, "settings-roundtrip", settings=settings, cap="1", write_request=True)
    restored, cap = job_run.prepared_settings(job, spec.run_id)
    assert restored.approval_settings() == settings.approval_settings()
    assert cap == "1.000000"
    rebuilt, _ = job_run.prepare_run(job, spec.run_id, settings=restored, cap=cap)
    assert rebuilt.current_request_sha256 == spec.current_request_sha256


def test_generic_request_checks_host_prose_and_binds_the_complete_owner_brief(job):
    from glasses3d.agent.loop import contract_problems

    record = jobs.load_job(job)
    record["description"] = "Exact supplementary photo note; unverified hypothesis is the owner's wording."
    record["description_sha256"] = hashlib.sha256(record["description"].encode()).hexdigest()
    (job / "job.json").write_text(json.dumps(record), encoding="utf-8")
    spec, _ = job_run.prepare_run(job, "verbatim-owner-evidence")
    assert record["description"] in spec.brief
    assert record["description"] not in spec.contract_brief
    assert contract_problems(spec.contract, instructions=spec.instructions,
                             brief=spec.contract_brief, tools=spec.tools) == []
    # Host prose filtering never changes the approval binding of the complete prompt.
    changed = dict(record, description=record["description"] + " Changed.")
    changed["description_sha256"] = hashlib.sha256(changed["description"].encode()).hexdigest()
    (job / "job.json").write_text(json.dumps(changed), encoding="utf-8")
    rebuilt, _ = job_run.prepare_run(job, spec.run_id)
    assert rebuilt.current_request_sha256 != spec.current_request_sha256


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


def test_review_dispatch_preserves_delivery_and_port(job, monkeypatch):
    start = Mock(return_value={"status": "ready", "url": "http://127.0.0.1:8251/"})
    monkeypatch.setattr(cli.review, "review_job", start)
    assert command("review", job, "--delivery-id", "chosen", "--port", "8252")["status"] == "ready"
    start.assert_called_once_with(job, delivery_id="chosen", port=8252)


def test_verdict_defaults_to_reviewed_bytes_and_preserves_exact_notes(job, monkeypatch):
    notes = "  Make this meeting point further out.\nKeep the lens colour.  "
    monkeypatch.setattr(cli.review, "reviewed_delivery", lambda root: {
        "delivery_id": "earlier-reviewed", "glb_sha256": "a" * 64})
    append = Mock(return_value={"status": "recorded"})
    monkeypatch.setattr(cli.verdicts, "append_verdict", append)
    command("verdict", job, "changes", "--notes", notes)
    append.assert_called_once_with(job, "changes", notes, delivery_id="earlier-reviewed", expected_sha256="a" * 64)
    append.reset_mock()
    command("verdict", job, "accept", "--notes", "Looks right.", "--delivery-id", "explicit", "--sha256", "b" * 64)
    append.assert_called_once_with(job, "accept", "Looks right.", delivery_id="explicit", expected_sha256="b" * 64)


def test_changed_owner_notes_invalidate_approval_before_launch(job, monkeypatch, capsys):
    notes = ["Owner changes for delivery one:\nExact first note."]
    monkeypatch.setattr(job_run.verdicts, "pending_notes", lambda root: list(notes))
    request = approved(job)
    saved = json.loads((job / "runs" / request["run_id"] / "approval-request.json").read_text())
    assert saved["request"]["unsent_owner_notes"]
    notes.append("Owner changes for delivery two:\nA newly recorded note.")
    execute = AsyncMock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(job_run, "execute_run", execute)
    assert cli.main(["job", "run", str(job), "--foreground", "--resume"]) == 3
    assert "changed" in json.loads(capsys.readouterr().err)["message"]
    assert not execute.called


def test_changed_specialist_settings_invalidate_parent_approval_before_launch(job, monkeypatch, capsys):
    approved(job)
    original = job_run.temple_stage.settings
    monkeypatch.setattr(job_run.temple_stage, "settings",
                        lambda parent=None: replace(original(parent), max_output_tokens=8192))
    execute = AsyncMock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(job_run, "execute_run", execute)
    assert cli.main(["job", "run", str(job), "--foreground"]) == 3
    assert "changed" in json.loads(capsys.readouterr().err)["message"]
    assert not execute.called


def test_generic_resume_sends_verbatim_owner_notes_once_using_actual_sdk_history(job, monkeypatch):
    from glasses3d.agent import loop as L, replay as R
    from glasses3d.agent.session_inputs import NOTE_PREFIX
    from glasses3d.tests.agent.loop_support import FakeScene

    notes = []
    monkeypatch.setattr(job_run.verdicts, "pending_notes", lambda root: list(notes))
    settings = replace(job_run.settings_default(), max_turns=2, max_output_tokens=1024,
                       settled_stop_usd="0.8", finish_tool=None, tool_choice="auto", reset_tool_choice=True)
    scene = FakeScene()
    transports = []
    for index in range(3):
        if index == 1:
            notes.append("Owner changes [verdict v1; GLB " + "a" * 64 + "]:\n"
                         "  The facets meeting point should be further out.\nKeep these exact spaces.  ")
        transport = R.ScriptedOpenAI([R.message("Offline note receipt.")], id_start=10 * index + 1)
        spec, _ = job_run.prepare_run(job, f"notes-{index}", settings=settings, cap="1", write_request=True,
                                      resume=index > 0, scene=scene, offline_handler=transport.handle)
        A.write_approval(job / "runs" / spec.run_id, max_usd="1", owner_words="Offline proof only.")
        assert asyncio.run(L.run_astra(spec, scene=scene))["status"] == "completed"
        assert not transport.violations and len(transport.creates) == 1
        transports.append(transport)
    for index, transport in enumerate(transports):
        texts = [part["text"] for item in transport.creates[0]["input"] if item.get("role") == "user"
                 for part in item.get("content", []) if part.get("type") == "input_text"]
        assert texts.count(NOTE_PREFIX + notes[0]) == (0 if index == 0 else 1)
        receipt = json.loads((job / "runs" / f"notes-{index}" / "input-receipt.json").read_text())
        assert receipt["notes_sent"] == (1 if index == 1 else 0)


def delivery_fixture(job):
    from glasses3d.tests.agent.test_delivery import make_finished
    return make_finished(job)


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
