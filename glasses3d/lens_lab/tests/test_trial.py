"""Runner contracts use synthetic files/mocked Codex, never Blender or an API."""
from __future__ import annotations

import json
import base64
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from glasses3d.lens_lab import trial


@pytest.fixture
def private_trial(tmp_path):
    repo = tmp_path / "repo"
    directory = repo / "glasses3d/data/lens_lab/trials/one"
    job_dir = directory / "job"
    (job_dir / "images").mkdir(parents=True)
    rows = []
    for index in range(6):
        path = job_dir / f"images/reference-{index + 1:02d}.png"
        path.write_bytes(f"photo bytes {index}".encode())
        rows.append({"file": path.relative_to(job_dir).as_posix(), "sha256": trial.file_sha256(path),
                     "view_kind": "front" if index == 0 else "angled", "side": "both", "size_px": [2, 2]})
    job = {"name": "Owner name", "description": "Exact owner words", "specs_json": "{}", "images": rows}
    trial.write_json(job_dir / "job.json", job)
    manifest = {"job_dir": str(job_dir), "lens_materials": ["Lens"]}
    trial.write_json(directory / "trial.json", manifest)
    return repo, directory, manifest, job


def finish_event(directory, index=0):
    return {"type": "item.completed", "item": {"type": "command_execution", "id": f"finish-{index}",
            "command": f"python -m glasses3d.lens_lab tool '{directory}' finish '{{}}'", "exit_code": 0}}


def view_event(path, *, kind="image_view", status="completed"):
    return {"type": "item.completed", "item": {"type": kind, "id": "view", "path": str(path), "status": status}}


def render_call(path):
    return {"tool": "compare_look_to_photos", "arguments": {}, "output_text": ["pipeline text"],
            "images": [{"path": str(path), "sha256": trial.file_sha256(path)}], "result": {"status": "ok"}}


THREAD_ID = "01234567-89ab-4cde-8123-456789abcdef"


def native_records(repo, image, *, view=True):
    records = [{"type": "session_meta", "payload": {
        "id": THREAD_ID, "cwd": str(repo), "timestamp": trial.utc_now(), "source": "exec"}}]
    if view:
        records.extend([
            {"type": "response_item", "payload": {"type": "function_call", "name": "view_image",
             "call_id": "view", "arguments": json.dumps({"path": str(image)})}},
            {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "ImageView", "id": "view", "path": image.as_uri()}}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "view",
             "output": [{"type": "input_image", "image_url": "data:image/png;base64,secret-pixels"}]}}])
    records.append({"type": "event_msg", "payload": {"type": "item_completed", "item": {
        **finish_event(image.parent)["item"], "type": "CommandExecution",
        "command": ["powershell", "-Command", finish_event(image.parent)["item"]["command"]]}}})
    return records


def write_native_session(directory, records):
    session = directory / "2026/10/20" / f"rollout-2026-10-20T00-00-00-{THREAD_ID}.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)
    session.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    return session


def test_head_builder_uses_same_committed_brief_not_dirty_module(tmp_path, monkeypatch):
    source_brief = b'MODEL_REQUIREMENTS = "committed requirements"\ndef build_brief(job): return job["description"]'
    source_instructions = (
        b'from .brief import MODEL_REQUIREMENTS\n'
        b'CORE_GUIDANCE = {"core": "core"}\nSPECIALIST_GUIDANCE = {"special": "special"}\n'
        b'def build_instructions(tools):\n'
        b'    assert sorted(t.name for t in tools) == ["core", "special"]\n'
        b'    return "main\\n" + MODEL_REQUIREMENTS\n')
    seen = []

    def git(repo, *args):
        seen.append(args)
        if args == ("rev-parse", "HEAD"):
            return b"abc123\n"
        return source_brief if args[1].endswith("/brief.py") else source_instructions

    monkeypatch.setattr(trial, "git_bytes", git)
    result = trial.head_prompt_sources(tmp_path, {"description": "owner words"})
    assert result["instructions"] == "main\ncommitted requirements"
    assert result["brief"] == "owner words"
    assert result["source_sha256"]["glasses3d/agent/instructions.py"] == trial.sha256(source_instructions)
    assert seen[1:] == [("show", "abc123:glasses3d/agent/brief.py"),
                        ("show", "abc123:glasses3d/agent/instructions.py")]


def test_current_head_main_matches_production_builder(tmp_path):
    # Pure registry construction is explicitly supported without worker startup.
    from glasses3d.agent.instructions import build_instructions
    from glasses3d.agent.tools import build_tools

    job = {"name": "test", "description": "exact", "specs_json": "{}", "images": []}
    head = trial.head_prompt_sources(trial.REPO, job)
    current_source = (trial.REPO / "glasses3d/agent/instructions.py").read_bytes()
    if trial.sha256(current_source) != head["source_sha256"]["glasses3d/agent/instructions.py"]:
        pytest.skip("another task has uncommitted production instruction edits")
    tools = build_tools(SimpleNamespace(root=tmp_path))
    assert set(head["main_tool_names"]) == {tool.name for tool in tools}
    assert head["instructions"] == build_instructions(tools)


def test_prompt_is_two_verbatim_parts_then_one_recipe_free_paragraph(private_trial):
    _, directory, _, _ = private_trial
    sources = {"instructions": " main text\nwith original whitespace ", "brief": "owner's\nexact brief"}
    paragraph = trial.lab_paragraph(directory, ["Lens"], Path("python.exe"))
    prompt = trial.build_prompt(sources, paragraph)
    assert prompt.startswith(sources["instructions"] + "\n\n" + sources["brief"] + "\n\n")
    assert "\n" not in paragraph
    assert "view_image" in paragraph and "--args-file" in paragraph
    assert "tool-schemas.json" in paragraph and "end with the lab finish" in paragraph
    assert "thickness_nm" not in paragraph and "roughness=" not in paragraph
    with pytest.raises(ValueError, match="one paragraph"):
        trial.build_prompt(sources, "first\n\nsecond")


def test_no_heavy_work_without_task17_exit(tmp_path):
    path = tmp_path / "glasses3d/data/review_20261018/codex/task17_status.txt"
    path.parent.mkdir(parents=True)
    for text in ("start now", "not exit yet", "exiting soon"):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(RuntimeError, match="no exit line"):
            trial.require_task17_exit(tmp_path)
    path.write_text("start now\nexit 0\n", encoding="utf-8")
    assert "exit 0" in trial.require_task17_exit(tmp_path)


def test_output_and_owner_photos_stay_in_trial(private_trial):
    repo, directory, manifest, job = private_trial
    assert trial.lab_path(directory, repo) == directory
    with pytest.raises(ValueError):
        trial.lab_path(repo / "glasses3d/data/jobs/new-job", repo)
    job_dir, loaded, photos = trial.load_trial_job(directory, manifest)
    assert len(photos) == 6 and loaded == job
    photos[0].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="integrity"):
        trial.load_trial_job(directory, manifest)
    with pytest.raises(ValueError, match="inside"):
        trial.load_trial_job(directory, {"job_dir": str(repo), "lens_materials": ["Lens"]})


def test_six_photos_and_no_model_override(private_trial):
    repo, directory, manifest, _ = private_trial
    _, _, photos = trial.load_trial_job(directory, manifest)
    command = trial.codex_command(["codex"], repo, photos, directory / "last-message.txt")
    assert command.count("-i") == 6
    assert [command[i + 1] for i, arg in enumerate(command) if arg == "-i"] == list(map(str, photos))
    assert command[command.index("-s") + 1] == "danger-full-access"
    assert command[command.index("-C") + 1] == str(repo)
    assert "--json" in command and "--ephemeral" not in command and command[-1] == "-"
    assert "-m" not in command and "--model" not in command
    assert not any("reasoning_effort" in arg for arg in command)


def test_environment_excludes_provider_keys_without_affecting_codex_home(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "never-output-me")
    monkeypatch.setenv("CODEX_API_KEY", "also-never-output-me")
    monkeypatch.setenv("CODEX_HOME", "existing-login-home")
    env = trial.child_environment()
    assert not (set(env) & trial.API_KEY_NAMES)
    assert env["CODEX_HOME"] == "existing-login-home"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["OPENBLAS_NUM_THREADS"] == "1"


def test_api_key_login_refuses_without_echoing_status(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="Logged in using an API key secret-value", stderr=""))
    with pytest.raises(RuntimeError) as error:
        trial.codex_preflight(["codex"], {})
    assert "ChatGPT" in str(error.value) and "secret-value" not in str(error.value)


@pytest.mark.parametrize("kind", ["image_view", "imageView"])
@pytest.mark.parametrize("uri", [False, True])
def test_native_image_event_proves_midtrial_render_view(private_trial, kind, uri):
    repo, directory, _, _ = private_trial
    image = directory / "comparison with spaces.png"
    image.write_bytes(b"image bytes")
    evidence = trial.image_view_evidence(
        [view_event(image.as_uri() if uri else image, kind=kind), finish_event(directory)],
        [render_call(image)], directory, repo)
    assert evidence["verified"]
    assert evidence["render_image_views"][0]["bytes_verified"]


@pytest.mark.parametrize("path", ["file://remote-server/share/image.png", "https://example.org/image.png"])
def test_nonlocal_image_uri_cannot_trigger_filesystem_access(private_trial, path):
    repo, directory, _, _ = private_trial
    result = trial.image_view_evidence([view_event(path), finish_event(directory)], [], directory, repo)
    assert not result["verified"] and result["other_image_views"][0]["unavailable"]


def test_agent_claim_or_initial_photo_is_not_image_proof(private_trial):
    repo, directory, manifest, _ = private_trial
    image = directory / "comparison.png"
    image.write_bytes(b"image bytes")
    photo = trial.load_trial_job(directory, manifest)[2][0]
    events = [{"type": "item.completed", "item": {"type": "agent_message", "text": "I used view_image"}},
              view_event(photo), finish_event(directory)]
    evidence = trial.image_view_evidence(events, [render_call(image)], directory, repo)
    assert not evidence["verified"]
    assert len(evidence["other_image_views"]) == 1


@pytest.mark.parametrize("problem", ["after_finish", "changed_bytes", "failed_view", "started_only"])
def test_uncompleted_or_unbound_image_view_does_not_pass(private_trial, problem):
    repo, directory, _, _ = private_trial
    image = directory / "comparison.png"
    image.write_bytes(b"original image")
    call = render_call(image)
    events = [view_event(image), finish_event(directory)]
    if problem == "after_finish":
        events.reverse()
    elif problem == "changed_bytes":
        image.write_bytes(b"changed image")
    elif problem == "failed_view":
        events[0]["item"]["status"] = "failed"
    else:
        events[0]["type"] = "item.started"
    assert not trial.image_view_evidence(events, [call], directory, repo)["verified"]


def test_file_change_audit_flags_outside_trial(private_trial):
    repo, directory, _, _ = private_trial
    events = [{"type": "item.completed", "item": {"type": "file_change", "changes": [
        {"path": (directory / "args.json").as_uri(), "kind": "add"},
        {"path": "glasses3d/agent/instructions.py", "kind": "update"}]}}]
    audit = trial.event_audit(events, directory, repo)
    assert len(audit["file_changes"]) == 2
    assert audit["outside_trial_file_changes"][0]["path"] == str(repo / "glasses3d/agent/instructions.py")
    assert "not an OS sandbox" in audit["limit"]


@pytest.mark.parametrize("problem", [None, "wrong_thread", "wrong_cwd", "wrong_source", "old_session",
                                     "missing_stdout_thread", "no_image_payload", "wrong_call_id",
                                     "delivery_after_finish", "wrong_view_path"])
def test_native_rollout_binds_this_exec_and_image_delivery(private_trial, problem):
    repo, directory, _, _ = private_trial
    (directory / "codex").mkdir()
    image = directory / "render.png"
    image.write_bytes(b"real render")
    started = trial.utc_now()
    records = native_records(repo, image)
    meta = records[0]["payload"]
    if problem == "wrong_thread":
        meta["id"] = "00000000-0000-4000-8000-000000000000"
    elif problem == "wrong_cwd":
        meta["cwd"] = str(repo.parent)
    elif problem == "wrong_source":
        meta["source"] = "app-server"
    elif problem == "old_session":
        meta["timestamp"] = "2000-01-01T00:00:00+00:00"
    elif problem == "no_image_payload":
        records[3]["payload"]["output"] = [{"type": "text", "text": "I viewed it"}]
    elif problem == "wrong_call_id":
        records[3]["payload"]["call_id"] = "different-call"
    elif problem == "delivery_after_finish":
        records[3], records[4] = records[4], records[3]
    elif problem == "wrong_view_path":
        records[1]["payload"]["arguments"] = json.dumps({"path": str(directory / "other.png")})
    root = repo.parent / "mock-codex-state"
    session = write_native_session(root / "sessions", records)
    # A different session is never opened, even if its contents aren't valid JSON.
    session.with_name("rollout-unrelated-00000000-0000-4000-8000-000000000000.jsonl").write_text("private", encoding="utf-8")
    events = [] if problem == "missing_stdout_thread" else [{"type": "thread.started", "thread_id": THREAD_ID}]
    result = trial.native_rollout_evidence(events, [render_call(image)], directory, repo,
                                           {"CODEX_HOME": str(root)}, started, trial.utc_now())
    assert result.get("image_viewing", {}).get("verified", False) == (problem is None)
    if problem is None:
        assert result["rollout_sha256"] == trial.file_sha256(session)
        proof = Path(result["proof_path"]).read_text(encoding="utf-8")
        assert "ImageView" in proof and "CommandExecution" in proof
        assert "secret-pixels" not in proof and "private" not in proof
        assert result["image_viewing"]["render_image_views"][0]["image_payload_delivered"]


@pytest.mark.parametrize("problem", [None, "wrong_outer_id", "unrelated_bytes", "no_payload",
                                     "view_before_call", "delivery_after_finish", "overlapping_exec",
                                     "no_view_invocation", "wrong_finish_trial", "finish_in_filename"])
def test_code_mode_native_images_require_unambiguous_call_and_exact_bytes(private_trial, problem):
    repo, directory, _, _ = private_trial
    (directory / "codex").mkdir()
    image = directory / "render.png"
    image.write_bytes(b"byte exact render image")
    started = trial.utc_now()
    records = native_records(repo, image)
    records[1]["payload"] = {"type": "custom_tool_call", "name": "exec", "call_id": "outer",
                             "input": "image((await tools.view_image({path:load('path')})).image_url);"}
    records[2]["payload"]["item"]["id"] = "exec-inner-view"
    output = records[3]["payload"]
    output.update(type="custom_tool_call_output", call_id="outer",
                  output=[{"type": "input_image", "image_url": "data:image/png;base64," +
                           base64.b64encode(image.read_bytes()).decode()}])
    if problem == "wrong_outer_id":
        output["call_id"] = "another-outer"
    elif problem == "unrelated_bytes":
        output["output"][0]["image_url"] = "data:image/png;base64," + base64.b64encode(b"other image").decode()
    elif problem == "no_payload":
        output["output"] = [{"type": "input_text", "text": "viewed it"}]
    elif problem == "view_before_call":
        records[1], records[2] = records[2], records[1]
    elif problem == "delivery_after_finish":
        records[3], records[4] = records[4], records[3]
    elif problem == "overlapping_exec":
        records.insert(2, {"type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "call_id": "other", "input": "text('overlap')"}})
    elif problem == "no_view_invocation":
        records[1]["payload"]["input"] = "text('view_image');"
    elif problem == "wrong_finish_trial":
        records[-1]["payload"]["item"]["command"] = [finish_event(directory.parent / "other")["item"]["command"]]
    elif problem == "finish_in_filename":
        records[-1]["payload"]["item"]["command"] = [
            f"python -m glasses3d.lens_lab tool '{directory}' read_report --args-file '{directory}/finish.json'"]
    root = repo.parent / "mock-codex-state"
    write_native_session(root / "sessions", records)
    events = [{"type": "thread.started", "thread_id": THREAD_ID}]
    result = trial.native_rollout_evidence(events, [render_call(image)], directory, repo,
                                           {"CODEX_HOME": str(root)}, started, trial.utc_now())
    assert result["status"] == "collected"
    assert result["image_viewing"]["verified"] == (problem is None)
    if problem is None:
        row = result["image_viewing"]["render_image_views"][0]
        assert row["payload_byte_exact"] and row["outer_call_id"] == "outer"
        assert row["image_payload_delivered"]
        old_proof = Path(result["proof_path"]).read_bytes()
        repeated = trial.native_rollout_evidence(events, [render_call(image)], directory, repo,
                                                  {"CODEX_HOME": str(root)}, started, trial.utc_now())
        assert repeated["status"] == "unavailable" and "new path" in repeated["error"]
        assert Path(result["proof_path"]).read_bytes() == old_proof


def mock_codex_run(monkeypatch, repo, directory, *, view=True):
    monkeypatch.setenv("CODEX_HOME", str(repo.parent / "mock-codex-state"))
    monkeypatch.setattr(trial, "require_task17_exit", lambda repo: "exit 0")
    monkeypatch.setattr(trial, "codex_preflight", lambda *a: {"auth_mode": "ChatGPT", "version": "test"})
    monkeypatch.setattr(trial, "head_prompt_sources", lambda *a: {
        "head": "committed-head", "source_sha256": {}, "main_tool_names": ["finish"],
        "instructions": "exact main instructions", "brief": "exact owner brief"})
    processes, scoring_calls = [], []

    class FakePopen:
        pid = 123

        def __init__(self, command, *, cwd, env, stdin, stdout, stderr):
            assert cwd == repo and "-m" not in command
            self.prompt = stdin.read()
            assert self.prompt.startswith(b"exact main instructions\n\nexact owner brief\n\n")
            assert env["PYTHONDONTWRITEBYTECODE"] == "1"
            self.stdout = stdout
            self.finished = False
            processes.append(self)

        def wait(self):
            image = directory / "render.png"
            image.write_bytes(b"image pixels")
            call = render_call(image)
            finish = {"status": "lab_finished", "lab": True, "final_materials": [{"name": "Lens"}]}
            with (directory / "calls.jsonl").open("w", encoding="utf-8") as stream:
                stream.write(json.dumps(call) + "\n")
                stream.write(json.dumps({"tool": "finish", "result": finish}) + "\n")
            trial.write_json(directory / "finish.json", finish)
            write_native_session(repo.parent / "mock-codex-state/sessions", native_records(repo, image, view=view))
            # Actual 0.163 exec stdout has no image_view item, even when opened.
            events = [{"type": "thread.started", "thread_id": THREAD_ID},
                      finish_event(directory), {"type": "turn.completed", "usage": {"input_tokens": 11}}]
            self.stdout.write(("\n".join(json.dumps(row) for row in events) + "\n").encode())
            self.stdout.flush()
            self.finished = True
            return 0

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    def score(path):
        assert path == directory and processes[0].finished and processes[0].stdout.closed
        scoring_calls.append(path)
        score_dir = directory / "score"
        score_dir.mkdir()
        comparison = score_dir / "comparison.png"
        comparison.write_bytes(b"labelled comparison pixels")
        result = {"comparison_image": str(comparison), "comparison_sha256": trial.file_sha256(comparison),
                  "label": "LAB Codex-as-Astra trial - not a product", "views": []}
        trial.write_json(score_dir / "score.json", result)
        return result
    monkeypatch.setattr(trial, "_score_trial", score)
    return scoring_calls


@pytest.mark.parametrize("view", [True, False])
def test_mocked_trial_writes_complete_receipt_and_requires_image_proof(private_trial, monkeypatch, view):
    repo, directory, _, _ = private_trial
    scoring_calls = mock_codex_run(monkeypatch, repo, directory, view=view)
    result = trial.run_trial(directory, variant="current-main", repo=repo, executable=["codex"])
    assert result["status"] == ("completed" if view else "incomplete")
    assert result["provider_api_spend_usd"] == 0
    assert result["codex_usage"] == [{"input_tokens": 11}]
    assert result["final_materials"] == [{"name": "Lens"}]
    assert result["seconds"] >= 0 and result["pid"] == 123
    assert result["prompt_sha256"] == trial.file_sha256(directory / "codex/prompt.txt")
    assert len(result["tool_calls"]) == 2 and result["finish_receipted"]
    assert result["codex_status"] == ("completed" if view else "incomplete")
    assert result["scoring"]["status"] == ("completed" if view else "not_started")
    assert scoring_calls == ([directory] if view else [])
    assert result["scoring"]["not_sent_to_codex"]
    assert result["seconds"] >= result["codex_seconds"]
    assert result == json.loads((directory / "codex/receipt.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="already has Codex"):
        trial.run_trial(directory, repo=repo, executable=["codex"])


def test_previously_used_initialized_trial_refuses(private_trial, monkeypatch):
    repo, directory, _, _ = private_trial
    mock_codex_run(monkeypatch, repo, directory)
    (directory / "calls.jsonl").write_text('{"tool":"execute_blender_code"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="neutral fixture"):
        trial.run_trial(directory, repo=repo, executable=["codex"])


def test_finish_file_without_matching_tool_receipt_does_not_complete(private_trial, monkeypatch):
    repo, directory, _, _ = private_trial
    mock_codex_run(monkeypatch, repo, directory)
    original = subprocess.Popen

    class ChangedFinish(original):
        def wait(self):
            code = super().wait()
            trial.write_json(directory / "finish.json", {"status": "lab_finished", "lab": True,
                                                        "final_materials": [{"name": "Different"}]})
            return code

    monkeypatch.setattr(subprocess, "Popen", ChangedFinish)
    result = trial.run_trial(directory, repo=repo, executable=["codex"])
    assert result["image_viewing"]["verified"]
    assert result["status"] == "incomplete" and not result["finish_receipted"]


def test_concurrent_implementation_edit_invalidates_stable_trial(private_trial, monkeypatch):
    repo, directory, _, _ = private_trial
    mock_codex_run(monkeypatch, repo, directory)
    versions = iter([{"glasses3d/agent/a.py": "before"}, {"glasses3d/agent/a.py": "after"}])
    monkeypatch.setattr(trial, "implementation_hashes", lambda repo: next(versions))
    result = trial.run_trial(directory, repo=repo, executable=["codex"])
    assert result["status"] == "incomplete"
    assert result["implementation_changed_paths"] == ["glasses3d/agent/a.py"]
    assert "does not attribute" in result["implementation_change_note"]


def test_scoring_failure_keeps_codex_proof_but_does_not_complete_trial(private_trial, monkeypatch):
    repo, directory, _, _ = private_trial
    mock_codex_run(monkeypatch, repo, directory)
    def failed_score(path):
        raise RuntimeError("capture failed")
    monkeypatch.setattr(trial, "_score_trial", failed_score)
    result = trial.run_trial(directory, repo=repo, executable=["codex"])
    assert result["status"] == "incomplete" and result["codex_status"] == "completed"
    assert result["image_viewing"]["verified"] and result["finish_receipted"]
    assert result["scoring"]["status"] == "failed"
    assert result["scoring"]["error"] == "RuntimeError: capture failed"
    saved = json.loads((directory / "codex/scoring-status.json").read_text(encoding="utf-8"))
    assert saved == result["scoring"]


@pytest.mark.parametrize("problem", ["missing_json", "changed_image", "outside_trial"])
def test_scoring_requires_bound_artifacts_inside_trial(private_trial, monkeypatch, problem):
    _, directory, _, _ = private_trial
    receipt_dir = directory / "codex"
    receipt_dir.mkdir()
    def incomplete_score(path):
        output = directory / "score"
        output.mkdir()
        image = output / "comparison.png" if problem != "outside_trial" else directory / "outside.png"
        image.write_bytes(b"image bytes")
        result = {"comparison_image": str(image), "comparison_sha256": trial.file_sha256(image)}
        if problem != "missing_json":
            trial.write_json(output / "score.json", result)
        if problem == "changed_image":
            image.write_bytes(b"changed bytes")
        return result
    monkeypatch.setattr(trial, "_score_trial", incomplete_score)
    result = trial.score_after_codex(directory, receipt_dir)
    assert result["status"] == "failed" and result["not_sent_to_codex"]


def test_batch_is_serial_stops_infrastructure_failure_and_keeps_variant_outside_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(trial, "require_task17_exit", lambda repo: "exit 0")
    seen = []

    def run(path, **kwargs):
        seen.append((path, kwargs))
        return {"status": "incomplete" if len(seen) == 2 else "completed", "trial_dir": str(path),
                "prompt_sha256": "x", "seconds": 1}

    monkeypatch.setattr(trial, "run_trial", run)
    result = trial.run_trials(variant="baseline", count=3, repo=tmp_path)
    assert len(seen) == 2 and result["completed_trials"] == 1
    assert seen[0][0].name == "trial-001" and seen[1][0].name == "trial-002"
    assert result["variant"] == "baseline"
    with pytest.raises(ValueError):
        trial.run_trials(variant="../escape", count=1, repo=tmp_path)
    with pytest.raises(ValueError):
        trial.run_trials(variant="good", count=0, repo=tmp_path)
