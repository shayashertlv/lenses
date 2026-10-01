import base64
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from blender_agent import studio_jobs as jobs


def photo(name="../untrusted.png"):
    buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="PNG")
    return {"name": name, "view": "front", "data_url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()}


def test_upload_paths_generated_and_draft_replacement_atomic(tmp_path):
    store = jobs.JobStore(tmp_path)
    job = store.create({"images": [photo()], "budget_usd": 20})
    directory = store.directory(job["id"])
    assert (directory / "images/reference-01.png").is_file()
    assert job["images"][0]["original_name"] == "../untrusted.png"
    kept = store.update(job["id"], {"images": [{"name": "reference-01.png", "view": "angled"}, photo("new.png")]})
    assert len(kept["images"]) == 2 and kept["images"][0]["view"] == "angled"
    with pytest.raises(jobs.JobError):
        store.update(job["id"], {"images": [{"data_url": "data:image/png;base64,AA=="}]})
    assert store.get(job["id"])["images"] == kept["images"]


@pytest.mark.parametrize("cap", [0, -1, True, "nan", "inf", 1001, "20.001"])
def test_invalid_budgets_rejected(tmp_path, cap):
    with pytest.raises(jobs.JobError):
        jobs.JobStore(tmp_path).create({"budget_usd": cap})


def test_frozen_draft_and_checkpoint_identity(tmp_path):
    store = jobs.JobStore(tmp_path)
    public = store.create({"description": "shield"})
    job = store.metadata(public["id"])
    job.update(started_once=True, status="failed")
    checkpoint = store.directory(job["id"]) / "checkpoint.blend"
    checkpoint.write_bytes(b"verified")
    job["resume_checkpoint"] = {"path": checkpoint.name, "sha256": jobs.sha(checkpoint)}
    store.save(job)
    assert store._checkpoint(job["id"]) == checkpoint
    with pytest.raises(jobs.JobError, match="frozen"):
        store.update(job["id"], {"budget_usd": 30})
    checkpoint.write_bytes(b"changed")
    with pytest.raises(jobs.JobError, match="changed"):
        store._checkpoint(job["id"])


def test_algorithm_brief_uses_sorted_specifics_provenance_not_legacy_description(tmp_path):
    store = jobs.JobStore(tmp_path)
    public = store.create({"name": "Oakley OO9208 44", "description": "obsolete invented description",
                           "specs": {"lens_color": "Rose", "frame_color": "Black"}})
    job = store.metadata(public["id"])
    job["specifics"] = {"schema_version": 1, "specs": {"lens_color": "Rose"}, "evidence": {"lens_color": [{"url": "https://example.com/", "quote": "Base: Rose"}]},
                         "search_suggestions_html": "<div>must not enter artist prompt</div>"}
    prompt = store._prompt(job)
    assert "obsolete invented description" not in prompt and "must not enter artist prompt" not in prompt
    assert '"kind": "user"' in prompt and '"quote": "Base: Rose"' in prompt
    assert prompt.index('"frame_color"') < prompt.index('"lens_color"')
    assert "not the front reflection colour" in prompt


def test_progress_sums_settlements_and_unknown_holds_not_all_reservations(tmp_path):
    store = jobs.JobStore(tmp_path)
    agent = tmp_path / "agent"
    ledger = agent / "runs/one/budget.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({"reservations": [{"reserved_micro_usd": 3000000, "settled_micro_usd": 200000}, {"reserved_micro_usd": 1500000}]}))
    assert store._costs(agent) == {"accounting_available": True, "spent_usd": .2, "reserved_usd": 1.5}


def test_orphan_live_process_blocks_duplicate_start_without_signals(tmp_path, monkeypatch):
    store = jobs.JobStore(tmp_path)
    job = store.create({})
    metadata = store.metadata(job["id"])
    metadata.update(status="running", started_once=True)
    store.save(metadata)
    jobs.atomic_json(store.directory(job["id"]) / "execution.json", {"pid": 123, "process_created": 44, "prior_runs": []})
    monkeypatch.setattr(jobs, "process_identity", lambda pid: 44)
    assert store.get(job["id"])["status"] == "running"
    with pytest.raises(jobs.JobError):
        store.start(job["id"], resume=True)
    monkeypatch.setattr(jobs, "process_identity", lambda pid: False)
    assert store.get(job["id"])["status"] == "interrupted"


def test_executor_new_and_resume_commands_use_real_route_and_fixed_cap(tmp_path, monkeypatch):
    store = jobs.JobStore(tmp_path, python="python", blender=tmp_path / "blender.exe")
    job = store.create({"description": "shield", "budget_usd": 17})
    directory = store.directory(job["id"])
    (directory / "brief.txt").write_text("brief")
    commands = []
    def launch(command, **kwargs):
        commands.append(command)
        target = Path(command[command.index("--output") + 1]); target.mkdir()
        jobs.atomic_json(target / "session.json", {"ready": True, "source": command[command.index("--seed") + 1] if "--seed" in command else None})
        return SimpleNamespace(returncode=0)
    class Run:
        pid = 12345
        def __init__(self, command, **kwargs):
            commands.append(command)
            agent = directory / "agent"; run = agent / "runs" / str(len(commands)); run.mkdir(parents=True)
            checkpoint = run / "stop-checkpoint.blend"; checkpoint.write_bytes(b"preserved live scene")
            jobs.atomic_json(run / "result.json", {"status": "completed", "stop_checkpoint": {"path": str(checkpoint), "saved": True}})
        def wait(self): return 0
    monkeypatch.setattr(jobs.subprocess, "run", launch)
    monkeypatch.setattr(jobs.subprocess, "Popen", Run)
    monkeypatch.setattr(jobs, "process_identity", lambda _: 123)
    store._execute(job["id"], False)
    assert "--empty" in commands[0] and "--seed" not in commands[0]
    assert "--run-paid" in commands[1] and commands[1][commands[1].index("--reasoning-effort")+1] == "max"
    assert commands[1][commands[1].index("--max-usd")+1] == "17.0"
    assert "--stop-file" in commands[1]
    original = store._checkpoint(job["id"])
    store._execute(job["id"], True)
    assert commands[2][commands[2].index("--seed") + 1] == str(original)
    assert "--resume" in commands[3]
    assert commands[3][commands[3].index("--max-usd")+1] == "17.0"


def test_child_environment_does_not_give_gemini_key_to_blender_or_ar(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "private-google")
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai")
    assert "GEMINI_API_KEY" not in jobs.clean_process_env(include_openai=True)
    assert "OPENAI_API_KEY" not in jobs.clean_process_env()
    assert jobs.clean_process_env(include_openai=True)["OPENAI_API_KEY"] == "private-openai"


def test_cleanup_checks_identity_and_owned_bootstrap_receipt_before_termination(tmp_path, monkeypatch):
    import psutil
    store = jobs.JobStore(tmp_path, blender=tmp_path / "blender.exe")
    session = tmp_path / "dedicated"; session.mkdir()
    jobs.atomic_json(session / "session.json", {"pid": 222})
    calls = []
    created = session.stat().st_ctime
    class Process:
        def create_time(self): return created
        def exe(self): return str(store.blender)
        def cmdline(self): return [str(store.blender), "--receipt", str(session / "initialization.json")]
        def terminate(self): calls.append("terminate")
        def wait(self, timeout): calls.append("wait")
    monkeypatch.setattr(psutil, "Process", lambda _: Process())
    assert store._cleanup_blender(session, created+1)["closed"] is False
    assert calls == []
    assert store._cleanup_blender(session, created)["closed"] is True
    assert calls == ["terminate", "wait"]


def test_failed_current_run_cannot_resume_prior_checkpoint(tmp_path):
    store = jobs.JobStore(tmp_path)
    job = store.create({})
    metadata = store.metadata(job["id"])
    metadata["resume_checkpoint"] = {"path": "old.blend", "sha256": "old"}
    store.save(metadata)
    store.finish(job["id"], {"status": "failed", "error": "credit_balance_exhausted", "stop_checkpoint": {"saved": False}})
    with pytest.raises(jobs.JobError, match="No verified"):
        store._checkpoint(job["id"])
    assert "credit_balance_exhausted" in store.get(job["id"])["error"]


def test_import_copies_completed_delivery_references_and_never_resumes(tmp_path):
    trial = tmp_path / "trial"; agent = trial / "agent"
    run = agent / "runs/one"; run.mkdir(parents=True)
    model = agent / "ar/final/model.glb"; model.parent.mkdir(parents=True); model.write_bytes(b"unchanged fixture")
    (agent / "scene.blend").write_bytes(b"source fixture")
    jobs.atomic_json(run / "result.json", {"status": "completed", "final_output": "Lens coating remains approximate"})
    jobs.atomic_json(agent / "trial-budget.json", {"maximum_micro_usd": 17000000})
    jobs.atomic_json(model.parent / "preview-result.json", {"sha256": jobs.sha(model), "ar": {"validation": {"ok": True}}})
    (trial / "references").mkdir()
    (trial / "references/front.png").write_bytes(base64.b64decode(photo()["data_url"].split(",")[1]))
    store = jobs.JobStore(tmp_path / "studio")
    job = store.import_completed(trial)
    assert job["status"] == "imported" and job["read_only"] is True
    assert job["budget_usd"] == 17
    assert job["final_output"] == "Lens coating remains approximate"
    assert job["result"]["validation"]["ok"] is True and len(job["images"]) == 1
    assert job["result"]["scene_matches_revision"] is None
    assert job["result"]["source_match_status"] == "unverified"
    assert job["result"]["glb_materials_edited"] is False
    assert store.artifact(job["id"], "model.glb").read_bytes() == model.read_bytes()
    with pytest.raises(jobs.JobError):
        store.start(job["id"], resume=True)


def test_orphan_finished_cleanup_waits_for_exit_and_verified_checkpoint(tmp_path, monkeypatch):
    store = jobs.JobStore(tmp_path)
    public = store.create({}); job = store.metadata(public["id"])
    directory = store.directory(job["id"])
    checkpoint = directory / "checkpoint.blend"; checkpoint.write_bytes(b"saved")
    job.update(status="completed", started_once=True,
               resume_checkpoint={"path": checkpoint.name, "sha256": jobs.sha(checkpoint)})
    store.save(job)
    jobs.atomic_json(directory / "execution.json", {"pid": 22, "process_created": 44,
                                                    "blender_session": "blender-aabb", "blender_process_created": 33})
    calls = []
    monkeypatch.setattr(store, "_cleanup_blender", lambda session, identity: calls.append((session, identity)) or {"closed": True})
    monkeypatch.setattr(jobs, "process_identity", lambda pid: 44)
    store.get(job["id"])
    assert not calls
    monkeypatch.setattr(jobs, "process_identity", lambda pid: False)
    assert store.get(job["id"])["blender_cleanup"]["closed"] is True
    assert calls == [(directory / "blender-aabb", 33)]
    store.get(job["id"])
    assert len(calls) == 1


def test_legacy_unbound_source_match_is_not_promoted_after_restart(tmp_path):
    store = jobs.JobStore(tmp_path)
    public = store.create({}); job = store.metadata(public["id"])
    job["result"] = {"revision": "original", "scene_matches_revision": True, "scene_url": "/scene.blend"}
    store.save(job)
    assert store.get(job["id"])["result"]["scene_matches_revision"] is None
    job["result"].update(revision="abcdef1234567890", scene_matches_revision=False)
    store.save(job)
    assert store.get(job["id"])["result"]["glb_materials_edited"] is True


def test_import_rejects_failed_resume_after_older_completion(tmp_path):
    trial = tmp_path / "trial"
    (trial / "agent/runs/001").mkdir(parents=True)
    (trial / "agent/runs/002").mkdir(parents=True)
    jobs.atomic_json(trial / "agent/runs/001/result.json", {"status": "completed", "final_output": "Old delivery"})
    jobs.atomic_json(trial / "agent/runs/002/result.json", {"status": "failed"})
    with pytest.raises(jobs.JobError, match="latest trial invocation"):
        jobs.JobStore(tmp_path / "studio").import_completed(trial)
