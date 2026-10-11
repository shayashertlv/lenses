"""The facet-run settings of the loop (plan/WP6_FACETS.md §4.3) on the real SDK over MockTransport: required tool
choice kept after the first call, the finish gate (a refused finish keeps the run going, an accepted one ends it), the
text-reply fallback (ended_without_finish), the one-epoch context guard, the approval / registry binding, the
parameter-description contract scan and the timeout table. The default settings leave every request unchanged
(test_loop.py and the other loop tests run on the defaults).
"""
import asyncio
import hashlib
import json

import pytest

pytest.importorskip("agents")
from agents import function_tool  # noqa: E402
from agents.tool import ToolOutputImage, ToolOutputText  # noqa: E402

from glasses3d.agent import approvals as A  # noqa: E402
from glasses3d.agent import loop as L  # noqa: E402
from glasses3d.agent import replay as R  # noqa: E402
from glasses3d.tests.agent.loop_support import (BRIEF, FakeScene, data_url, photos, png_bytes,  # noqa: E402
                                                result_of, tool_instructions)
from glasses3d.tests.agent.support import OFFLINE_KEY  # noqa: E402

RUN = "20261011T120000-facet01"
FACET = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=10, tool_choice="required",
                      reset_tool_choice=False, finish_tool="facet_finish", finish_k=4)


@pytest.fixture(autouse=True)
def no_production_clients(monkeypatch):
    class Forbidden:
        def __init__(self, *args, **kwargs):
            raise AssertionError("ProductionClients constructed in an offline test")
    monkeypatch.setattr(L, "ProductionClients", Forbidden)


def finish_tools(answers: list[str]):
    """facet_finish answers the given statuses in order; look returns one image per call (seeded by the call)."""
    calls = {"finish": 0, "look": 0}

    @function_tool
    async def facet_finish(summary: str) -> list:
        """End the run when the preconditions hold.

        Args:
            summary: What was matched.
        """
        status = answers[min(calls["finish"], len(answers) - 1)]
        calls["finish"] += 1
        return [ToolOutputText(text=json.dumps({"status": status, "n": calls["finish"]}) + "\nsettled $0.00")]

    @function_tool
    async def look(photo: str) -> list:
        """Compare a photo.

        Args:
            photo: The photo id.
        """
        calls["look"] += 1
        return [ToolOutputText(text=json.dumps({"status": "ok", "compare_id": f"cmp{calls['look']}"})),
                ToolOutputImage(image_url=data_url(png_bytes(4, 4, seed=40 + calls["look"])), detail="high")]
    return calls, [facet_finish, look]


def job_for(root, tools, settings, *, cap="20"):
    job = root / "jobs" / "facet-test-0000abcd"
    (job / "inputs" / "images").mkdir(parents=True)
    (job / "job.json").write_text(json.dumps({"schema": "g3d.job/1", "name": "Facet test"}), encoding="utf-8")
    for k, photo in enumerate(photos(2), 1):
        import base64
        (job / "inputs" / "images" / f"reference-0{k}.png").write_bytes(base64.b64decode(
            photo.image_url.split(",", 1)[1]))
    request = A.build_request(job, instructions_sha256=L.instructions_sha256(tool_instructions(tools)),
                              tool_registry_sha256=L.tool_registry_sha256(tools, settings),
                              settings=settings.approval_settings(),
                              run_inputs=A.run_input_binding(job, BRIEF, photos(2)))
    A.write_approval_request(job / "runs" / RUN, job_id=job.name, request=request, estimate={"expected_usd": "1"},
                             suggested_cap_usd=cap)
    A.write_approval(job / "runs" / RUN, max_usd=cap, owner_words=f"Yes, run it with ${cap}.")
    return job, A.request_sha256(request)


def run(tmp_path, replies, tools, settings=FACET, scene=None, cap="20"):
    job, sha = job_for(tmp_path, tools, settings, cap=cap)
    transport = R.ScriptedOpenAI(replies)
    spec = L.RunSpec(job_dir=job, run_id=RUN, instructions=tool_instructions(tools), tools=tools, brief=BRIEF,
                     current_request_sha256=sha, photos=photos(2), settings=settings,
                     offline_handler=transport.handle, offline_api_key=OFFLINE_KEY, heartbeat_interval_s=0.05,
                     contract=L.ToolContract(frozenset(t.name for t in tools)))
    result = asyncio.run(L.run_astra(spec, scene=scene or FakeScene()))
    return job, transport, result


def outputs(body):
    return [item for item in body["input"] if isinstance(item, dict) and item.get("type") == "function_call_output"]


# --------------------------------------------------------------------------------------------- binding
def test_default_settings_change_nothing():
    _calls, tools = finish_tools(["accepted"])
    default = L.RunSettings(effort="max")
    assert default.behavior() == {}
    assert default.approval_settings() == {"effort": "max", "max_output_tokens": 16384, "max_turns": 60}
    assert L.tool_registry_sha256(tools, default) == L.tool_registry_sha256(tools)
    assert L.build_model_settings(default).tool_choice is None
    agent = L.build_agent("x", tools, default)
    assert agent.tool_use_behavior == "run_llm_again" and agent.reset_tool_choice is True
    assert L.build_run_config(object.__new__(__import__("openai").AsyncOpenAI)).call_model_input_filter is None


def test_facet_settings_are_bound_into_the_approval_and_the_registry_sha():
    _calls, tools = finish_tools(["accepted"])
    guard = L.RunSettings(**{**FACET.__dict__, "context_guard_tokens": 240_000})
    assert FACET.approval_settings() == {
        "effort": "max", "max_output_tokens": 4096, "max_turns": 10, "tool_choice": "required",
        "reset_tool_choice": False, "finish": {"tool": "facet_finish", "behavior": L.FINISH_BEHAVIOR, "k": 4}}
    assert guard.approval_settings()["context_guard"] == {"input_tokens": 240_000, "filter": L.CONTEXT_GUARD}
    shas = {L.tool_registry_sha256(tools), L.tool_registry_sha256(tools, FACET), L.tool_registry_sha256(tools, guard),
            L.tool_registry_sha256(tools, L.RunSettings(**{**FACET.__dict__, "finish_k": 5}))}
    assert len(shas) == 4
    agent = L.build_agent("x", tools, FACET)
    assert isinstance(agent.tool_use_behavior, L.FinishOnAccepted) and agent.reset_tool_choice is False
    assert L.build_model_settings(FACET).tool_choice == "required"


def test_invalid_facet_settings_are_refused(tmp_path):
    _calls, tools = finish_tools(["accepted"])
    for bad in ({"tool_choice": "none"}, {"finish_tool": "nope"}, {"finish_k": 0}, {"context_guard_tokens": 0},
                {"reset_tool_choice": "no"}):
        settings = L.RunSettings(**{**FACET.__dict__, **bad})
        spec = L.RunSpec(job_dir=tmp_path, run_id=RUN, instructions="x facet_finish look", tools=tools, brief="b",
                         current_request_sha256="0" * 64, settings=settings)
        with pytest.raises(L.RunRefused):
            L._validate_spec(spec)


# --------------------------------------------------------------------------------------------- the finish gate
def test_refused_finish_keeps_the_run_going_and_accepted_finish_ends_it(tmp_path):
    calls, tools = finish_tools(["refused", "accepted"])
    replies = [R.tool_call("facet_finish", {"summary": "early"}), R.tool_call("look", {"photo": "01"}),
               R.tool_call("facet_finish", {"summary": "done"}), R.message("never requested")]
    job, transport, result = run(tmp_path, replies, tools)
    assert result["status"] == "completed" and calls["finish"] == 2 and len(transport.creates) == 3
    assert json.loads(result["final_output"].split("\n")[0]) == {"status": "accepted", "n": 2}
    assert json.loads(outputs(transport.creates[1])[0]["output"][0]["text"].split("\n")[0])["status"] == "refused"
    assert all(body["tool_choice"] == "required" for body in transport.creates)      # kept after the first call
    assert result["finish_notes"] == 0 and result["settings"]["finish"]["tool"] == "facet_finish"
    assert transport.violations == []


def test_required_tool_choice_without_the_reset_flag_is_reset_by_the_sdk(tmp_path):
    """The pin behind reset_tool_choice=False: with the SDK default the second request drops tool_choice."""
    _calls, tools = finish_tools(["accepted"])
    settings = L.RunSettings(effort="max", max_output_tokens=4096, max_turns=10, tool_choice="required",
                             finish_tool="facet_finish")
    replies = [R.tool_call("look", {"photo": "01"}), R.tool_call("facet_finish", {"summary": "s"})]
    _job, transport, result = run(tmp_path, replies, tools, settings=settings)
    assert result["status"] == "completed"
    assert transport.creates[0]["tool_choice"] == "required" and "tool_choice" not in transport.creates[1]


def test_text_reply_without_finish_continues_then_ends_without_finish(tmp_path):
    _calls, tools = finish_tools(["refused"])
    settings = L.RunSettings(**{**FACET.__dict__, "tool_choice": "auto"})
    replies = [R.message("I am done."), R.message("Really done."), R.message("Done.")]
    job, transport, result = run(tmp_path, replies, tools, settings=settings)
    assert result["status"] == "ended_without_finish" and result["finish_notes"] == 2
    assert result["turns_used"] == 3 and len(transport.creates) == 3
    note = L.FINISH_NOTE.format(tool="facet_finish")
    texts = [item.get("content") for item in transport.creates[1]["input"] if item.get("role") == "user"]
    assert note in texts and result["final_output"] == "Done."
    events = (job / "runs" / RUN / "transcript.md").read_text(encoding="utf-8")
    assert "host note 1 of 2" in events and "2 host notes were used" in events


def test_text_reply_then_accepted_finish_completes(tmp_path):
    _calls, tools = finish_tools(["accepted"])
    settings = L.RunSettings(**{**FACET.__dict__, "tool_choice": "auto"})
    _job, _transport, result = run(tmp_path, [R.message("Thinking aloud."),
                                              R.tool_call("facet_finish", {"summary": "s"})], tools, settings=settings)
    assert result["status"] == "completed" and result["finish_notes"] == 1 and result["turns_used"] == 2


def test_finish_status_parsing():
    assert L.tool_result_status([ToolOutputText(text='{"status":"accepted"}\nsettled $1.00')]) == "accepted"
    assert L.tool_result_status('{"status": "refused", "missing": []}') == "refused"
    assert L.tool_result_status("accepted") is None and L.tool_result_status([]) is None


# --------------------------------------------------------------------------------------------- context guard
class GuardScene(FakeScene):
    def __init__(self):
        super().__init__()
        self.asked = []

    def context_stubs(self, image_sha256s):
        self.asked.append(list(image_sha256s))
        return {image_sha256s[0]: "[image removed: superseded]"}


def test_context_guard_stubs_once_and_holds(tmp_path):
    _calls, tools = finish_tools(["accepted"])
    settings = L.RunSettings(**{**FACET.__dict__, "context_guard_tokens": 50_000})
    scene = GuardScene()
    replies = [R.tool_call("look", {"photo": "01"}, counted=1000), R.tool_call("look", {"photo": "02"}, counted=60_000),
               R.tool_call("look", {"photo": "03"}), R.tool_call("facet_finish", {"summary": "s"})]
    _job, transport, result = run(tmp_path, replies, tools, settings=settings, scene=scene)
    assert result["status"] == "completed" and len(scene.asked) == 1 and len(scene.asked[0]) == 2
    first = hashlib.sha256(png_bytes(4, 4, seed=41)).hexdigest()
    assert scene.asked[0][0] == first
    images = [len(R.input_images(body)) for body in transport.creates]
    assert images[:2] == [2, 3]                                  # the two brief photos, then one tool image
    assert images[2] == 3 and images[3] == 4                     # first tool image stubbed, the rest kept
    stub = [p for o in outputs(transport.creates[2]) for p in o["output"] if p.get("type") == "input_text"]
    assert any(p["text"] == "[image removed: superseded]" for p in stub)
    assert result["context_guard"]["epoch"]["stubbed"] == 1


def test_context_guard_unit_keeps_brief_images():
    from agents.run_config import CallModelData, ModelInputData
    url = data_url(png_bytes(seed=7))
    items = [{"role": "user", "content": [{"type": "input_image", "image_url": url}]},
             {"type": "function_call_output", "call_id": "c1", "output": [{"type": "input_image", "image_url": url}]}]
    guard = L.ContextGuard(10, counted=lambda: 11, choose=lambda shas: {shas[0]: "stub"})
    out = guard(CallModelData(model_data=ModelInputData(input=items, instructions="i"), agent=None, context=None))
    assert out.input[0] == items[0] and out.input[1]["output"] == [{"type": "input_text", "text": "stub"}]
    assert guard.epoch["tool_images"] == 1 and items[1]["output"][0]["type"] == "input_image"      # copy, not edit
    below = L.ContextGuard(10, counted=lambda: 9, choose=lambda shas: {shas[0]: "stub"})
    data = ModelInputData(input=items, instructions="i")
    assert below(CallModelData(model_data=data, agent=None, context=None)) is data


# --------------------------------------------------------------------------------------------- contract, timeouts
def test_contract_scans_parameter_descriptions():
    @function_tool
    def guess(idea: str) -> str:
        """Record an idea.

        Args:
            idea: A hypothesis about the facet.
        """
        return idea
    problems = L.contract_problems(L.ToolContract(frozenset({"guess"})), instructions="Use guess.", brief="b",
                                   tools=[guess])
    assert problems and "parameter description of `guess`" in problems[0] and "hypothes" in problems[0]


def test_timeout_table_names_every_facet_tool():
    from glasses3d.agent.temple_lab.tools import TOOL_NAMES
    for name in TOOL_NAMES:
        assert L.hard_timeout_s(name) == L.HARD_TIMEOUT_S[name] and L.sdk_timeout_s(name) == L.HARD_TIMEOUT_S[name] + 10
    small = L.hard_timeout_s("facet_variants", variants=2, photos=1)
    assert small == L.FACET_VARIANTS_BASE_S + 3 * L.FACET_VARIANTS_PER_RENDER_S < L.HARD_TIMEOUT_S["facet_variants"]
    assert all(L.HARD_TIMEOUT_S[name] >= 30 for name in TOOL_NAMES)


def test_facet_variants_worker_deadline_leaves_the_host_its_margin():
    """facet_variants' worker render deadline scales with its renders but stops FACET_VARIANTS_HOST_MARGIN_S under
    the table value at every photo and variant count the lab takes (up to 4 x 7 renders), so the host's builds,
    crease lines and sheets keep that margin inside the hard timeout plus the SDK's 10 s (the 2026-10-11 review: at
    4 photos the old cap left 10 s of the 100 s SDK timeout)."""
    from glasses3d.agent.temple_lab.lab import VARIANT_PHOTOS_MAX, VARIANTS_MAX, VARIANTS_MIN
    table = L.HARD_TIMEOUT_S["facet_variants"]
    assert L.FACET_VARIANTS_HOST_MARGIN_S >= 15.0
    for photos in range(1, VARIANT_PHOTOS_MAX + 1):
        for variants in range(VARIANTS_MIN, VARIANTS_MAX + 1):
            deadline = L.hard_timeout_s("facet_variants", variants=variants, photos=photos)
            scaled = L.FACET_VARIANTS_BASE_S + L.FACET_VARIANTS_PER_RENDER_S * photos * (variants + 1)
            assert deadline == min(scaled, table - L.FACET_VARIANTS_HOST_MARGIN_S), (photos, variants)
            assert L.sdk_timeout_s("facet_variants") - deadline >= \
                L.FACET_VARIANTS_HOST_MARGIN_S + L.SDK_TIMEOUT_MARGIN_S
    worst = L.hard_timeout_s("facet_variants", variants=VARIANTS_MAX, photos=VARIANT_PHOTOS_MAX)
    assert worst == table - L.FACET_VARIANTS_HOST_MARGIN_S == 75.0
    assert worst >= 2.5 * (0.82 * VARIANT_PHOTOS_MAX * (VARIANTS_MAX + 1) + 2.9)   # the table comment's measured rate
    assert L.hard_timeout_s("facet_variants", variants=3, photos=4) == 70.0 < worst     # under the cap: scaled


def test_restore_and_finish_timeouts_exceed_their_inner_deadlines():
    """facet_restore runs one checkpoint restore and facet_finish a restore and an export: their hard timeouts are
    those declared worker deadlines plus the host margin, so the SDK never answers E_TIMEOUT while a restore or an
    export may still succeed (a timed-out finish thread would otherwise still export and record)."""
    from glasses3d.agent.journal import CHECKPOINT_DEADLINE_S
    from glasses3d.agent.seeing import EXPORT_DEADLINE_S
    from glasses3d.agent.temple_lab.lab import BUILD_DEADLINE_S
    restore = CHECKPOINT_DEADLINE_S + L.FACET_DEADLINE_MARGIN_S
    finish = CHECKPOINT_DEADLINE_S + EXPORT_DEADLINE_S + L.FACET_DEADLINE_MARGIN_S
    assert L.hard_timeout_s("facet_restore") == restore == 90.0
    assert L.hard_timeout_s("facet_finish") == finish == 210.0
    assert L.sdk_timeout_s("facet_restore") > CHECKPOINT_DEADLINE_S
    assert L.sdk_timeout_s("facet_finish") > CHECKPOINT_DEADLINE_S + EXPORT_DEADLINE_S
    assert BUILD_DEADLINE_S == L.hard_timeout_s("facet_edit")           # a build never outlives its tool's table value
