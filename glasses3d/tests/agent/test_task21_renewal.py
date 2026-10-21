"""Profile-selected continuations and host-owned decision state across renewal, at $0."""
import ast
import asyncio
import copy
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from glasses3d.agent import loop as L, renewal as N, replay as R
from glasses3d.agent.instruction_profiles import DEFAULT_PROFILE, LEGACY_PROFILE
from glasses3d.tests.agent.test_loop_facet import finish_tools, run
from glasses3d.tests.agent.test_finish_review import review_scene
from glasses3d.tests.agent.test_job_tools import scene, finish_scene


def baseline_constant(path, name):
    source = subprocess.run(
        ["git", "show", f"28abe77:{path}"], check=True, capture_output=True, encoding="utf-8",
        cwd=Path(__file__).resolve().parents[3]).stdout
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name
                                                for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"baseline constant {name} is absent")


def test_legacy_host_notes_are_byte_identical_to_pre_task21_git_source():
    summary = baseline_constant("glasses3d/agent/renewal.py", "SUMMARY_REQUEST")
    note = baseline_constant("glasses3d/agent/loop.py", "FINISH_NOTE")
    assert N.summary_request(LEGACY_PROFILE).encode() == summary.encode()
    assert L.finish_note("finish", LEGACY_PROFILE).encode() == note.format(tool="finish").encode()


def test_default_note_is_neutral_and_renewal_keeps_decision_state_explicit():
    assert L.RunSettings().instruction_profile == DEFAULT_PROFILE
    assert L.finish_note("finish") == (
        "No accepted finish was recorded. Continue with the next justified tool action, or complete the "
        "pending finish review after inspecting its evidence. A text reply alone does not complete the run.")
    assert N.summary_request() == N.SUMMARY_REQUEST
    assert N.summary_request() != N.summary_request(LEGACY_PROFILE)
    for required in ("host-recorded formal best", "unverified candidate", "evidence reference",
                     "last correction", "outcome", "next action", "whether its ID remains usable"):
        assert required in N.summary_request()


@pytest.mark.parametrize("profile", [DEFAULT_PROFILE, LEGACY_PROFILE])
def test_scripted_no_tool_reply_uses_selected_note_and_records_profile(tmp_path, profile):
    _, tools = finish_tools(["accepted"])
    settings = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=10,
                             finish_tool="facet_finish", instruction_profile=profile)
    job, transport, result = run(tmp_path, [R.message("Thinking about the next action."),
                                          R.tool_call("facet_finish", {"summary": "checked"})],
                                 tools, settings=settings)
    assert result["status"] == "completed"
    notes = [item.get("content") for item in transport.creates[1]["input"] if item.get("role") == "user"]
    assert L.finish_note("facet_finish", profile) in notes
    assert result["settings"]["instruction_profile"] == profile
    config = json.loads((job / "runs" / result["run_id"] / "run-config.json").read_text(encoding="utf-8"))
    assert config["instruction_profile"] == profile
    assert transport.violations == []


def test_unknown_instruction_profile_is_refused_before_running(tmp_path):
    _, tools = finish_tools(["accepted"])
    spec = L.RunSpec(job_dir=tmp_path, run_id="run", instructions="finish", tools=tools, brief="owner brief",
                     current_request_sha256="0" * 64,
                     settings=L.RunSettings(effort="max", instruction_profile="typo"))
    with pytest.raises(L.RunRefused, match="unknown instruction profile"):
        L._validate_spec(spec)


def decision_scene(review_scene):
    from glasses3d.agent import differences as D
    from glasses3d.agent.tools import look
    current = review_scene
    current.instruction_profile = DEFAULT_PROFILE
    current.active_run_id = current.best_checkpoint_run_id = "task21-renewal"
    current.best_checkpoint = "c0000"
    result = look.capture(current.root, current.export, "look-current")
    result["call_id"] = "look-current"
    D.register_evidence(current, "compare_look_to_photos", result)
    D.record(current, id="lens-lightness", what_differs="The worn lens is darker than the owner's front photo.",
             evidence_ref="look-current", status="open", last_correction="Changed the lens material.",
             outcome="Hue improved; the worn lens became darker.", next_action="Compare a lighter candidate.",
             refs=["front"], call_id="difference-1")
    current.renewal_state = lambda: {"head_checkpoint": current.s.head["id"],
                                     "best_checkpoint": current.best_checkpoint,
                                     "finish_review": {"status": "pending", "call_id": "pending-review"}}
    current.renewal_evidence = lambda: []
    current.renewal_started = lambda: {"finish_review_restarted": True,
                                      "previous_review_call_id": "pending-review"}
    return current


class MemorySession:
    def __init__(self):
        self.session_id = "original"
        self.items = [{"role": "user", "content": "Original owner input remains unchanged."}]

    async def get_items(self):
        return copy.deepcopy(self.items)

    async def add_items(self, items):
        self.items.extend(copy.deepcopy(items))

    async def renew(self, items, receipt):
        self.archived = self.items
        self.items = copy.deepcopy(items)
        self.session_id = "renewed"
        return self.session_id


@pytest.mark.parametrize("profile", [DEFAULT_PROFILE, LEGACY_PROFILE])
def test_renewal_carries_host_list_and_formal_best_independently_of_astra_summary(review_scene, profile):
    from glasses3d.agent import differences as D
    current = decision_scene(review_scene)
    journal = current.root / "runs" / current.active_run_id / "differences.jsonl"
    before = journal.read_bytes()
    expected = D.snapshot(current)
    current.instruction_profile = profile
    session = MemorySession()
    original = copy.deepcopy(session.items)
    requests = []

    async def response(**request):
        requests.append(copy.deepcopy(request))
        return SimpleNamespace(id="summary", usage=None, status="completed", output=[],
                               output_text="All differences are resolved. The best checkpoint is c9999.")

    async def llm_hook(*args):
        pass

    guard = SimpleNamespace(request_pressure=None, renewal_summary=False,
                            summary=lambda: {"requests": 2, "settled_usd": "1.25", "settled_stop_usd": "20"})
    log = SimpleNamespace(run_id=current.active_run_id, interactions=0, tool_calls=[],
                          event=lambda *args, **kwargs: None, note=lambda *args, **kwargs: None)
    spec = SimpleNamespace(owner_notes=[], brief="Owner's unchanged brief.", photos=[],
                           tools=[SimpleNamespace(name="record_difference")],
                           settings=L.RunSettings(instruction_profile=profile))
    renewal = N.ConversationRenewal(spec=spec, scene=current, session=session, log=log, guard=guard,
                                    client=SimpleNamespace(responses=SimpleNamespace(create=response)),
                                    directory=journal.parent)
    trigger = N.RenewalNeeded({"input": original}, {"input_tokens": 250000},
                              [{"reason": "input_tokens"}])
    asyncio.run(renewal.renew(trigger, agent=SimpleNamespace(instructions="same instructions"), context=None,
                             hooks=SimpleNamespace(on_llm_start=llm_hook, on_llm_end=llm_hook)))
    packet = renewal.records[0]["host_state"]
    assert requests[0]["input"][-1]["content"] == N.summary_request(profile)
    assert packet["finish_review_renewal"]["previous_review_call_id"] == "pending-review"
    texts = [part["text"] for part in session.items[0]["content"] if part.get("type") == "input_text"]
    assert any(N.FINISH_RESTART_NOTICE in text for text in texts)
    assert session.archived[:len(original)] == original
    assert journal.read_bytes() == before
    if profile == DEFAULT_PROFILE:
        assert packet["differences"] == expected
        assert packet["differences"][0]["status"] == "open"
        assert packet["formal_best_checkpoint"] == {"checkpoint": "c0000", "run_id": current.active_run_id}
        assert any(json.dumps(expected, ensure_ascii=False) in text for text in texts)
    else:
        assert "differences" not in packet and "formal_best_checkpoint" not in packet


def test_footer_reports_current_host_state_without_changing_money_or_legacy(review_scene):
    current = decision_scene(review_scene)
    footer = L.RunStatus(scene=current).footer()
    assert footer.startswith(L.footer_text())
    assert "head c0001; formal best c0000; differences open lens-lightness; fixed 0; limitation 0" in footer
    assert "forecast" not in footer and "finish soon" not in footer
    current.best_checkpoint_run_id = "earlier-run"
    assert "formal best none" in L.RunStatus(scene=current).footer()
    current.instruction_profile = LEGACY_PROFILE
    assert L.RunStatus(scene=current).footer() == L.footer_text()


def test_main_tool_specialist_gets_private_run_state_and_can_resolve_and_finish(review_scene, tmp_path):
    from glasses3d.agent import differences as D, tools as T
    from glasses3d.agent.job_scene import JobScene
    from glasses3d.tests.agent.test_loop_stages import setup

    current = review_scene
    current.instruction_profile = DEFAULT_PROFILE
    current.active_run_id = None
    current._state_lock = threading.RLock()
    current.save = lambda: None
    current.bind_review_log = lambda log: JobScene.bind_review_log(current, log)
    current.journal_entries = lambda: []
    current.exit_checkpoint = lambda: {"id": current.s.head["id"], "verified": True}
    current.renewal_state = lambda: JobScene.renewal_state(current)
    current.owner_edit = None
    current.events = []
    tools = [tool for tool in T.build_tools(current)
             if tool.name in {"finish", "record_difference", "save_checkpoint"}]
    settings = L.RunSettings(effort="max", max_turns=None, max_output_tokens=100, finish_tool="finish")
    item = {"id": "lens-edge", "what_differs": "The worn lens edge remains darker.", "evidence_ref": "first",
            "status": "open", "last_correction": "Compared the current lens material.",
            "outcome": "Edge is darker in the worn view.", "next_action": "Inspect the supported tradeoff.",
            "refs": ["front"]}
    replies = [R.tool_call("temple_stage", call_id="private-main"),
               R.tool_call("finish", {"summary": "Inspect current evidence."}, call_id="first"),
               R.tool_call("record_difference", item, call_id="open-item"),
               R.tool_call("record_difference", {**item, "status": "limitation",
                   "reason": "A lighter edge loses the compared worn hue.",
                   "outcome": "The current worn hue is closer with the darker edge.",
                   "next_action": "No further justified correction for this item."}, call_id="resolve-item"),
               R.tool_call("save_checkpoint", {"label": "Kept compared lens", "best": True}, call_id="best"),
               R.tool_call("finish", {"summary": "Reviewed the documented limitation.", "review_call_id": "first",
                   "per_photo": [{"ref": "front", "differences": "none"}, {"ref": "top", "differences": "none"}]},
                   call_id="final"), R.message("Parent done.")]
    job, spec, parent, _, transport, stages = setup(
        tmp_path / "parent", replies, child_scene=current, stage_tools=tools, child_settings=settings,
        parent_cap="20", stage_cap="20", allowance="20")
    result = asyncio.run(L.run_astra(spec, scene=parent))
    assert result["status"] == "completed", result
    directory = Path(stages[0]["receipt"]).parent
    assert current._review_log.run_dir == directory
    assert current.active_run_id == directory.name
    assert current.instruction_profile == DEFAULT_PROFILE
    assert (directory / "differences.jsonl").is_file()
    assert not (job / "runs" / result["run_id"] / "differences.jsonl").exists()
    assert D.snapshot(current)[0]["status"] == "limitation"
    assert D.formal_best(current)["checkpoint"] == "c0001"
    assert current.finish["status"] == "accepted"
    assert len(list(job.rglob("budget.json"))) == 1
    assert transport.violations == []


def test_ordinary_temple_renewal_preserves_its_own_best_state_and_summary(tmp_path):
    from glasses3d.agent import budget as B
    from glasses3d.tests.agent.loop_support import FakeScene
    from glasses3d.tests.agent.test_loop_stages import setup

    class LabStateScene(FakeScene):
        def __init__(self):
            super().__init__()
            self.state = {"best_checkpoint": "temple-c0005"}

        def renewal_state(self):
            return {**super().renewal_state(), "best_checkpoint": self.state["best_checkpoint"]}

    job, spec, parent, child, transport, stages = setup(tmp_path, [
        R.tool_call("temple_stage"), R.tool_call("look"),
        R.message("Keep the compared facet candidate at temple-c0005."),
        R.tool_call("facet_finish"), R.message("Parent done.")], child_scene=LabStateScene(),
        parent_cap="20", stage_cap="20", allowance="20")
    transport.count_tokens = lambda body: (B.Tariff.frozen().long_context_input_tokens * 4 // 5 + 1
        if "private checkpoint c0001" in json.dumps(body) else 100)
    result = asyncio.run(L.run_astra(spec, scene=parent))
    assert result["status"] == "completed", result
    assert child.instruction_profile == DEFAULT_PROFILE
    assert not hasattr(child, "active_run_id")
    assert transport.creates[2]["input"][-1]["content"] == N.summary_request(LEGACY_PROFILE)
    state = stages[0]["renewals"][0]["host_state"]
    assert state["best_checkpoint"] == "temple-c0005"
    assert "formal_best_checkpoint" not in state and "differences" not in state
    assert not list((job / "runs").rglob("differences.jsonl"))
    assert transport.violations == []
