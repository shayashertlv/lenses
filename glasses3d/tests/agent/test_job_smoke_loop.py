"""One real SDK request through scripted transport, the full canonical contract, and no modeling side effects."""
from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
import json

import pytest

from glasses3d.agent import approvals as A, instructions as I, loop as L, replay as R, tools as T
from glasses3d.agent.receipts import BlobStore
from glasses3d.tests.agent.loop_support import BRIEF, FakeScene, ledger_rows, photos
from glasses3d.tests.agent.support import OFFLINE_KEY
from glasses3d.tests.agent.test_job_tools import scene  # noqa: F401 - lightweight canonical-tool fixture


SMOKE = L.RunSettings(effort="max", max_turns=1, max_continuations=0, max_output_tokens=4096,
                      settled_stop_usd="1", tool_choice="required", reset_tool_choice=False, smoke=True,
                      context_guard_tokens=150000, context_guard_every_requests=3)


@pytest.fixture(autouse=True)
def no_paid_clients(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("smoke-loop tests must never construct production clients")
    monkeypatch.setattr(L, "ProductionClients", forbidden)


def prepared(root, scene, replies, *, settings=SMOKE, run_id="smoke", tools=None):
    tools = tools or T.build_tools(scene, settings)
    instructions = I.build_instructions(tools)
    job = root / "job"
    sources = job / "inputs/images"
    sources.mkdir(parents=True, exist_ok=True)
    (job / "job.json").write_text(json.dumps({"schema": "g3d.job/1", "name": "Smoke fixture"}))
    references = photos(6)
    for photo in references:
        (job / "inputs" / photo.path).write_bytes(base64.b64decode(photo.image_url.split(",", 1)[1]))
    request = A.build_request(job, instructions_sha256=L.instructions_sha256(instructions),
                              tool_registry_sha256=L.tool_registry_sha256(tools, settings),
                              settings=L.approval_settings(settings),
                              run_inputs=A.run_input_binding(job, BRIEF, references))
    A.write_approval_request(job / "runs" / run_id, job_id=job.name, request=request,
                             estimate={"expected_usd": "0.50"}, suggested_cap_usd="1", requested_cap_usd="1")
    A.write_approval(job / "runs" / run_id, max_usd="1", owner_words="Offline smoke proof only.")
    transport = R.ScriptedOpenAI(replies)
    spec = L.RunSpec(job_dir=job, run_id=run_id, instructions=instructions, tools=tools, brief=BRIEF,
                     photos=references, settings=settings, requested_cap_usd="1",
                     current_request_sha256=A.request_sha256(request), contract=I.instruction_contract(tools),
                     offline_handler=transport.handle, offline_api_key=OFFLINE_KEY, heartbeat_interval_s=0.05)
    return job, spec, transport


@pytest.mark.parametrize("tool,args", [("scene_summary", {"objects": None}), ("budget_status", {})])
def test_smoke_keeps_real_instructions_brief_all_photos_and_stops_after_one_read(tmp_path, scene, tool, args):
    job, spec, transport = prepared(tmp_path, scene, [R.tool_call(tool, args)])
    before = scene.s.head.copy()
    hooks = FakeScene()
    result = asyncio.run(L.run_astra(spec, scene=hooks))
    assert result["status"] == "completed" and result["smoke"]["ok"], result
    assert result["requests"] == result["turns_used"] == result["tool_calls"] == 1
    assert len(transport.creates) == len(transport.counts) == 1 and not transport.unexpected
    assert not transport.violations and ledger_rows(job, "smoke")[0]["outcome"] == "settled"
    payload = transport.creates[0]
    assert payload["instructions"] == spec.instructions == I.build_instructions(spec.tools)
    assert payload["tool_choice"] == "required" and payload["parallel_tool_calls"] is False
    assert {t["name"] for t in payload["tools"]} == set(L.SMOKE_TOOLS)
    assert len(spec.tools) > 2 and "execute_blender_code" in {t.name for t in spec.tools}
    assert L.contract_problems(spec.contract, instructions=spec.instructions, brief=spec.brief, tools=spec.tools) == []
    parts = [p for item in payload["input"] if item.get("role") == "user" for p in item.get("content", [])]
    assert len([p for p in parts if p["type"] == "input_image"]) == 6
    texts = "\n".join(p["text"] for p in parts if p["type"] == "input_text")
    assert BRIEF in texts and L.SMOKE_POLICY["notice"] in texts
    assert "owner cutoff: $1.000000 settled spend" in texts
    assert scene.s.head == before and scene.finish is None and hooks.restored == []
    assert not (job / "session/session.sqlite").exists()
    assert (job / "runs/smoke/smoke/session.sqlite").exists()
    config = json.loads((job / "runs/smoke/run-config.json").read_text())
    assert config["session"] == "runs/smoke/smoke/session.sqlite"
    assert config["tools"] == [t.name for t in spec.tools]
    assert config["executable_tools"] == list(L.SMOKE_TOOLS) and config["smoke_policy"] == L.SMOKE_POLICY


@pytest.mark.parametrize("kind", ["text", "incomplete", "mutating", "two_reads", "schema_error", "refused"])
def test_bad_smoke_response_cannot_mutate_or_send_a_second_inference(tmp_path, scene, kind):
    if kind == "text":
        reply = R.message("Done without a tool.")
    elif kind == "incomplete":
        reply = R.incomplete()
    elif kind == "mutating":
        reply = R.tool_call("execute_blender_code", {"code": "raise RuntimeError('must not execute')",
                                                    "soft_timeout_s": 1, "label": "bad"})
    elif kind == "two_reads":
        reply = R.tool_call("scene_summary", {"objects": None}, call_id="first")
        reply.output += R.tool_call("budget_status", call_id="second").output
    elif kind == "schema_error":
        reply = R.tool_call("scene_summary", {"objects": 42})
    else:
        scene.s.call_read = lambda *a, **k: {"status": "refused", "code": "test-refusal"}
        reply = R.tool_call("scene_summary", {"objects": None})
    job, spec, transport = prepared(tmp_path, scene, [reply, R.message("A second request must never occur.")])
    before = scene.s.head.copy()
    result = asyncio.run(L.run_astra(spec, scene=FakeScene()))
    assert result["status"] != "completed" and result["smoke"]["ok"] is False, result
    assert len(transport.creates) == len(transport.counts) == result["requests"] == 1
    assert result["continuations"] == 0 and scene.s.head == before and scene.finish is None
    assert all(call[0] != "execute" for call in scene.s.calls)
    if kind == "two_reads":
        assert [r["status"] for r in result["smoke"]["calls"]] == ["ok", "blocked"]
        assert len(scene.calls) == 1  # the second readonly action was stopped before its generic wrapper
    assert len(ledger_rows(job, "smoke")) == 1


def test_smoke_does_not_touch_existing_normal_history(tmp_path, scene):
    job, spec, transport = prepared(tmp_path, scene, [R.tool_call("budget_status")])
    db = job / "session/session.sqlite"
    db.parent.mkdir()
    session = L.BlobSession(L.SESSION_ID, db, blobs=BlobStore(job / "blobs"))
    asyncio.run(session.add_items([{"role": "user", "content": "Normal modeling history, keep exactly."}]))
    session.close()
    before = db.read_bytes()
    result = asyncio.run(L.run_astra(spec, scene=FakeScene()))
    assert result["smoke"]["ok"] and db.read_bytes() == before
    assert "Normal modeling history" not in json.dumps(transport.creates[0]["input"])


def test_normal_run_after_smoke_is_fresh_and_receives_all_photos_again(tmp_path, scene):
    job, spec, _ = prepared(tmp_path, scene, [R.tool_call("budget_status")])
    assert asyncio.run(L.run_astra(spec, scene=FakeScene()))["smoke"]["ok"]
    normal = replace(SMOKE, smoke=False, tool_choice="auto", reset_tool_choice=True)
    _, next_spec, transport = prepared(tmp_path, scene, [R.message("Normal run begins here.")],
                                        settings=normal, run_id="normal")
    result = asyncio.run(L.run_astra(next_spec, scene=FakeScene()))
    assert result["status"] == "completed" and "smoke" not in result
    assert result["input"]["images_sent"] == 6 and result["input"]["images_reused_from_history"] == 0
    assert len(transport.creates[0]["tools"]) == len(next_spec.tools)
    assert L.SMOKE_POLICY["notice"] not in json.dumps(transport.creates[0]["input"])
    assert (job / "session/session.sqlite").exists()


@pytest.mark.parametrize("change", [{"smoke": 1}, {"max_turns": 2}, {"max_output_tokens": 8192},
                                     {"max_continuations": 1}, {"tool_choice": "auto"},
                                     {"finish_tool": "finish"}, {"settled_stop_usd": "0.5"}])
def test_invalid_smoke_mode_refuses_before_budget_or_transport(tmp_path, scene, change):
    job, spec, transport = prepared(tmp_path, scene, [R.tool_call("budget_status")])
    spec.settings = replace(SMOKE, **change)
    with pytest.raises(L.RunRefused, match="smoke"):
        asyncio.run(L.run_astra(spec, scene=FakeScene()))
    assert transport.creates == transport.counts == [] and not (job / "runs/smoke/budget.json").exists()


def test_smoke_resume_is_refused_before_paid_state(tmp_path, scene):
    job, spec, transport = prepared(tmp_path, scene, [R.tool_call("budget_status")])
    spec.resume = True
    with pytest.raises(L.RunRefused, match="smoke"):
        asyncio.run(L.run_astra(spec, scene=FakeScene()))
    assert not transport.creates and not (job / "runs/smoke/budget.json").exists()


def test_smoke_policy_and_full_registry_are_bound_to_the_approval(tmp_path, scene, monkeypatch):
    job, spec, transport = prepared(tmp_path, scene, [R.tool_call("budget_status")])
    full = L.tool_registry_sha256(spec.tools, SMOKE)
    only_read = [tool for tool in spec.tools if tool.name in L.SMOKE_TOOLS]
    assert full != L.tool_registry_sha256(only_read, SMOKE)
    assert full != L.tool_registry_sha256(spec.tools, replace(SMOKE, smoke=False))
    monkeypatch.setattr(L, "SMOKE_POLICY", {**L.SMOKE_POLICY, "max_calls": 2})
    assert full != L.tool_registry_sha256(spec.tools, SMOKE)
    with pytest.raises(A.ApprovalRefused, match="changed"):
        asyncio.run(L.run_astra(spec, scene=FakeScene()))
    assert not transport.creates and not (job / "runs/smoke/budget.json").exists()
