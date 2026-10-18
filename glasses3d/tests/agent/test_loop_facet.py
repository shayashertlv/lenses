"""The facet-run settings of the loop (plan/WP6_FACETS.md §4.3) on the real SDK over MockTransport: required tool
choice kept after the first call, the finish gate (a refused finish keeps the run going, an accepted one ends it), the
text-reply fallback (ended_without_finish), the context guard (one token epoch, byte rungs, each epoch's choice scoped
to the tool outputs present at its firing), the payload_limit status, the machine-load preflight and watch, the
approval / registry binding, the parameter-description contract scan and the timeout table. The default settings leave
every request unchanged (test_loop.py and the other loop tests run on the defaults).
"""
import asyncio
import hashlib
import json

import pytest

pytest.importorskip("agents")
from agents import function_tool  # noqa: E402
from agents.tool import ToolOutputImage, ToolOutputText  # noqa: E402

from glasses3d.agent import approvals as A  # noqa: E402
from glasses3d.agent import budget as B  # noqa: E402
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


def run(tmp_path, replies, tools, settings=FACET, scene=None, cap="20", **extra):
    job, sha = job_for(tmp_path, tools, settings, cap=cap)
    transport = R.ScriptedOpenAI(replies)
    spec = L.RunSpec(job_dir=job, run_id=RUN, instructions=tool_instructions(tools), tools=tools, brief=BRIEF,
                     current_request_sha256=sha, photos=photos(2), settings=settings,
                     offline_handler=transport.handle, offline_api_key=OFFLINE_KEY, heartbeat_interval_s=0.05,
                     contract=L.ToolContract(frozenset(t.name for t in tools)), **extra)
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
    # the byte rung: the filter's own (CONTEXT_GUARD /2) unless set, and then bound into the approval and the sha
    assert L.CONTEXT_GUARD == "g3d.context_guard/2" and L.CONTEXT_GUARD_BYTES == B.PAYLOAD_LIMIT_BYTES // 2
    assert guard.guard_bytes() == L.CONTEXT_GUARD_BYTES and FACET.guard_bytes() is None
    explicit = L.RunSettings(**{**guard.__dict__, "context_guard_bytes": 100_000_000})
    assert explicit.approval_settings()["context_guard"] == {"input_tokens": 240_000, "filter": L.CONTEXT_GUARD,
                                                            "request_bytes": 100_000_000}
    assert explicit.guard_bytes() == 100_000_000
    assert L.tool_registry_sha256(tools, explicit) not in shas
    agent = L.build_agent("x", tools, FACET)
    assert isinstance(agent.tool_use_behavior, L.FinishOnAccepted) and agent.reset_tool_choice is False
    assert L.build_model_settings(FACET).tool_choice == "required"


def test_invalid_facet_settings_are_refused(tmp_path):
    _calls, tools = finish_tools(["accepted"])
    for bad in ({"tool_choice": "none"}, {"finish_tool": "nope"}, {"finish_k": 0}, {"context_guard_tokens": 0},
                {"reset_tool_choice": "no"}, {"context_guard_bytes": 1_000},      # bytes without the token epoch
                {"context_guard_tokens": 10, "context_guard_bytes": 0},
                {"context_guard_tokens": 10, "context_guard_bytes": 1.5}):
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


def photo_tools():
    """facet_finish (accepted) and a look whose image depends only on the photo, as seeing's compare cache returns the
    same panel file for the same build, photo and crop (seeing.py): a second look at a photo repeats the first's
    bytes (session2: the re-applied r6's panels were byte-identical to cmp015's, v3p3-verify-14)."""
    @function_tool
    async def facet_finish(summary: str) -> list:
        """End the run when the preconditions hold.

        Args:
            summary: What was matched.
        """
        return [ToolOutputText(text=json.dumps({"status": "accepted"}) + "\nsettled $0.00")]

    @function_tool
    async def look(photo: str) -> list:
        """Compare a photo.

        Args:
            photo: The photo id.
        """
        return [ToolOutputText(text=json.dumps({"status": "ok", "photo": photo})),
                ToolOutputImage(image_url=data_url(png_bytes(4, 4, seed=60 + int(photo))), detail="high")]
    return [facet_finish, look]


def parts_of(body, call_id) -> list:
    return [p.get("type") for o in outputs(body) if o["call_id"] == call_id for p in o["output"]]


def test_context_guard_never_stubs_an_output_that_arrives_after_its_epoch(tmp_path):
    """Review 2026-10-12, P1 (v3p3-verify-14): the guard's choice is scoped to the tool outputs present at its epoch
    (their call_ids), never to image bytes alone. A later output that repeats a stubbed image's bytes (a revision
    re-applied after the epoch: the compare cache returns the same panel file) reaches the model as an image; the old
    output keeps its stub (a stable prefix). With /1 the new output arrived as the old compare's 'superseded' stub,
    which E_JUDGEMENT still asked Astra to judge."""
    tools = photo_tools()
    settings = L.RunSettings(**{**FACET.__dict__, "context_guard_tokens": 50_000})
    scene = GuardScene()
    replies = [R.tool_call("look", {"photo": "1"}, counted=1000), R.tool_call("look", {"photo": "2"}, counted=60_000),
               R.tool_call("look", {"photo": "1"}), R.tool_call("look", {"photo": "3"}),
               R.tool_call("facet_finish", {"summary": "s"})]
    _job, transport, result = run(tmp_path, replies, tools, settings=settings, scene=scene)
    assert result["status"] == "completed" and len(scene.asked) == 1
    first = hashlib.sha256(png_bytes(4, 4, seed=61)).hexdigest()
    assert scene.asked[0][0] == first                               # look 1's image is the one stubbed
    calls = [o["call_id"] for o in outputs(transport.creates[-1])]
    assert len(calls) == 4
    epoch = transport.creates[2]                                    # the guard's request
    assert parts_of(epoch, calls[0]) == ["input_text", "input_text"] and parts_of(epoch, calls[1])[-1] == "input_image"
    later = transport.creates[3]                                    # look 1 again, after the epoch
    assert parts_of(later, calls[2]) == ["input_text", "input_image"]
    again = [p for o in outputs(later) if o["call_id"] == calls[2] for p in o["output"] if p["type"] == "input_image"]
    assert L._image_sha256(again[0]["image_url"]) == first          # the stubbed bytes, now delivered
    for body in transport.creates[3:]:                              # the epoch's outputs keep their choice
        assert parts_of(body, calls[0])[1] == "input_text" and parts_of(body, calls[2])[1] == "input_image"
    assert [len(R.input_images(b)) for b in transport.creates] == [2, 3, 3, 4, 5]
    assert result["context_guard"]["epochs"] == [result["context_guard"]["epoch"]]
    assert result["context_guard"]["epoch"]["tool_outputs"] == 2 and result["context_guard"]["epoch"]["trigger"] == \
        "input_tokens"
    assert result["context_guard"]["request_bytes"] == L.CONTEXT_GUARD_BYTES


def test_context_guard_token_epoch_once_then_byte_rungs():
    """The token epoch fires once (a later one rewrites a long-context prefix for little: CONTEXT_GUARD_BYTES'
    comment); the byte rungs fire at k x byte_threshold, the next rung the first above the request that fired (one
    reading never fires twice); each firing asks choose over every tool-output image present and scopes its choice to
    the outputs present then; an output added after the latest firing is never touched."""
    from agents.run_config import CallModelData, ModelInputData
    reading = {"tokens": None, "bytes": None}
    to_stub, asked = set(), []

    def choose(shas):
        asked.append(list(shas))
        return {sha: f"stub {sha[:8]}" for sha in shas if sha in to_stub}
    guard = L.ContextGuard(100, byte_threshold=1000, counted=lambda: reading["tokens"],
                           sent_bytes=lambda: reading["bytes"], choose=choose)

    def out(call_id, seed):
        return {"type": "function_call_output", "call_id": call_id,
                "output": [{"type": "input_image", "image_url": data_url(png_bytes(seed=seed))}]}

    def sha_of(seed):
        return hashlib.sha256(png_bytes(seed=seed)).hexdigest()

    def send(items):
        data = ModelInputData(input=items, instructions="i")
        got = guard(CallModelData(model_data=data, agent=None, context=None))
        return data, got

    def kinds(got):
        return {i["call_id"]: i["output"][0]["type"] for i in got.input}
    items = [out("c1", 1), out("c2", 2)]
    reading.update(tokens=99, bytes=999)
    data, got = send(items)
    assert got is data and not asked                                             # below both
    to_stub.add(sha_of(1))
    reading.update(tokens=100, bytes=10)
    _data, got = send(items)
    assert kinds(got) == {"c1": "input_text", "c2": "input_image"} and len(asked) == 1
    items.append(out("c3", 1))                                                   # c1's bytes, after the epoch
    reading.update(tokens=10_000, bytes=999)                                     # tokens fired once already
    _data, got = send(items)
    assert len(asked) == 1 and kinds(got) == {"c1": "input_text", "c2": "input_image", "c3": "input_image"}
    to_stub.add(sha_of(2))
    reading.update(bytes=2_500)                                                  # byte rung 1 (and 2): fires once
    _data, got = send(items)
    assert len(asked) == 2 and asked[1] == [sha_of(1), sha_of(2), sha_of(1)]
    assert kinds(got) == {"c1": "input_text", "c2": "input_text", "c3": "input_text"}   # the new choice, c1-c3
    items.append(out("c4", 2))
    _data, got = send(items)                                                     # the same reading: no firing
    reading.update(bytes=2_999)
    _data, got = send(items)                                                     # under rung 3
    assert len(asked) == 2 and kinds(got)["c4"] == "input_image"
    reading.update(bytes=3_000)
    _data, got = send(items)
    assert len(asked) == 3 and kinds(got)["c4"] == "input_text"
    assert [(e["epoch"], e["trigger"], e["at_request_bytes"], e["tool_outputs"]) for e in guard.epochs] == [
        (1, "input_tokens", 10, 2), (2, "request_bytes", 2_500, 3), (3, "request_bytes", 3_000, 4)]
    assert guard.epoch == guard.epochs[-1] and guard.byte_rung == 4 and guard.token_due is False
    assert L.epoch_text(guard.epoch) == ("Context guard epoch 3: 4 of 4 tool images replaced by text stubs in the 4 "
                                         "tool outputs present, at 10000 counted input tokens and 0.0 MB (fired on "
                                         "request bytes).")
    no_bytes = L.ContextGuard(100, counted=lambda: 50, sent_bytes=lambda: 10**12, choose=choose)  # no byte rung
    data = ModelInputData(input=items, instructions="i")
    assert no_bytes(CallModelData(model_data=data, agent=None, context=None)) is data


def test_a_body_over_the_providers_limit_ends_the_run_payload_limit(tmp_path, monkeypatch):
    """A request over the provider's documented per-request limit is refused before counting (budget
    PayloadTooLarge): the run ends payload_limit, $0 for it, nothing after it (review 2026-10-12, P2)."""
    calls, tools = finish_tools(["accepted"])
    monkeypatch.setattr(B, "PAYLOAD_LIMIT_IMAGES", 3)               # 2 brief photos, then one image a look
    replies = [R.tool_call("look", {"photo": "01"}), R.tool_call("look", {"photo": "02"}),
               R.tool_call("facet_finish", {"summary": "s"})]
    job, transport, result = run(tmp_path, replies, tools)
    assert result["status"] == "payload_limit" and calls["look"] == 2
    assert len(transport.creates) == 2 and len(transport.counts) == 2 and result["requests"] == 2
    assert [r["outcome"] for r in json.loads((job / "runs" / RUN / "budget.json").read_text(encoding="utf-8"))[
        "rows"]] == ["settled", "settled"]
    assert result["budget"]["blocked_kind"] == "PayloadTooLarge" and "payload_limit" in L.STATUSES
    assert result["budget"]["planning"]["last_request_images"] == 3


# --------------------------------------------------------------------------------------------- machine load
LOAD_ROW = {"pid": 4242, "kind": "background Blender", "name": "blender.exe"}


def test_load_rows_name_background_blender_and_pytest_outside_this_tree():
    """Other work that slows the scene's worker (v3p3-verify-16): background Blender (-b / --background) and pytest
    (-m pytest, pytest.exe, py.test), never an open Blender window and never this process's own tree."""
    rows = [{"pid": 1, "name": "blender.exe", "cmdline": [r"C:\B\blender.exe", "-b", "--python", "w.py"]},
            {"pid": 2, "name": "blender.exe", "cmdline": [r"C:\B\blender.exe"]},                       # a window
            {"pid": 3, "name": "python.exe", "cmdline": ["python.exe", "-m", "pytest", "-n", "4"]},
            {"pid": 4, "name": "pytest.exe", "cmdline": [r"C:\v\Scripts\pytest.exe", "tests"]},
            {"pid": 5, "name": "python.exe",                                    # an xdist worker: its pytest named
             "cmdline": ["python.exe", "-c", "import sys;exec(eval(sys.stdin.readline()))"]},
            {"pid": 6, "name": "python.exe", "cmdline": ["python.exe", "-m", "glasses3d.agent.temple_lab.cli",
                                                         "--basetemp", "data/pytest_tmp/x"]},
            {"pid": 7, "name": "blender.exe", "cmdline": ["blender.exe", "--background"]},
            {"pid": 8, "name": "System", "cmdline": None},
            {"pid": 9, "name": "python.exe", "cmdline": ["python", "-m", "py.test"]}]
    got = L.load_rows(rows, mine={7})
    assert [(r["pid"], r["kind"]) for r in got] == [(1, "background Blender"), (3, "pytest"), (4, "pytest"),
                                                    (9, "pytest")]
    assert L.load_text(got) == "1 background Blender (pid 1), 3 pytest (pids 3, 4, 9)"
    assert isinstance(L.machine_load(), list)                       # the live scan runs (whatever it finds)


def test_load_preflight_refuses_before_anything_is_consumed(tmp_path):
    _calls, tools = finish_tools(["accepted"])
    with pytest.raises(L.RunRefused, match="other work is running on this machine: 1 background Blender "
                                           r"\(pid 4242\).*Ways out: wait until it ends or stop it"):
        run(tmp_path, [R.tool_call("facet_finish", {"summary": "s"})], tools, load_check=lambda: [LOAD_ROW])
    run_dir = tmp_path / "jobs" / "facet-test-0000abcd" / "runs" / RUN
    assert not (run_dir / "budget.json").exists() and not (run_dir / "result.json").exists()
    assert not (tmp_path / "jobs" / "facet-test-0000abcd" / "session").exists()


def test_load_accepted_runs_and_load_that_changes_is_reported(tmp_path, capsys):
    """refuse_on_load off: the run goes on with the preflight's load recorded; LoadWatch reports each change of the
    other work during the run (printed for the host, in result.json; Astra is never told)."""
    _calls, tools = finish_tools(["accepted"])
    answers = iter([[LOAD_ROW]])
    replies = [R.tool_call("look", {"photo": "01"}), R.tool_call("facet_finish", {"summary": "s"})]
    _job, transport, result = run(tmp_path, replies, tools, load_check=lambda: next(answers, []),
                                  refuse_on_load=False, load_watch_interval_s=0.01)
    assert result["status"] == "completed"
    load = result["machine_load"]
    assert load["preflight"] == [LOAD_ROW] and load["refuse_on_load"] is False and load["check_failures"] == 0
    assert load["changes"] and load["changes"][0]["load"] == []
    assert "the other work on this machine ended" in capsys.readouterr().out
    assert not any("other work" in json.dumps(body) for body in transport.creates)


def test_load_watch_reports_changes_and_counts_failures():
    lines, answers = [], [[], [LOAD_ROW], [LOAD_ROW], RuntimeError("denied"), []]

    def check():
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    watch = L.LoadWatch(check, run_id="r", interval_s=10, printer=lines.append)
    for _ in range(5):
        watch.poll()
    assert [c["load"] for c in watch.changes] == [[LOAD_ROW], []] and watch.failures == 1
    assert lines == ["[astra r] other work started on this machine: 1 background Blender (pid 4242); tool calls may "
                     "pass their deadlines while it runs", "[astra r] the other work on this machine ended"]


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
    from glasses3d.agent import tool_timeouts as T
    for name in TOOL_NAMES:
        assert L.hard_timeout_s(name) == L.HARD_TIMEOUT_S[name]
        assert L.sdk_timeout_s(name) == T.inner_steps_s(name) + T.HOST_WORK_MARGIN_S + L.SDK_TIMEOUT_MARGIN_S
    small = L.hard_timeout_s("facet_variants", variants=2, photos=1)
    assert small == L.FACET_VARIANTS_BASE_S + 3 * L.FACET_VARIANTS_PER_RENDER_S < L.HARD_TIMEOUT_S["facet_variants"]
    assert all(L.HARD_TIMEOUT_S[name] >= 30 for name in TOOL_NAMES)


def test_facet_variants_worker_deadline_leaves_the_host_its_margin():
    """facet_variants' worker render deadline scales with its renders but stops FACET_VARIANTS_HOST_MARGIN_S under
    the table value at every photo and variant count the lab takes (up to 4 x 5 renders), so the host's builds,
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


def test_finish_timeout_exceeds_its_inner_deadlines():
    """facet_finish runs a checkpoint restore and an export: its hard timeout is those declared worker deadlines plus
    the host margin, so the SDK never answers E_TIMEOUT while an export may still succeed (a timed-out finish thread
    would otherwise still export and record). facet_restore (v2) has no timeout row since 2026-10-13."""
    from glasses3d.agent.journal import CHECKPOINT_DEADLINE_S
    from glasses3d.agent.seeing import EXPORT_DEADLINE_S
    from glasses3d.agent.temple_lab.lab import BUILD_DEADLINE_S
    finish = CHECKPOINT_DEADLINE_S + EXPORT_DEADLINE_S + L.FACET_DEADLINE_MARGIN_S
    assert L.hard_timeout_s("facet_finish") == finish == 210.0
    assert "facet_restore" not in L.HARD_TIMEOUT_S
    assert L.sdk_timeout_s("facet_finish") > CHECKPOINT_DEADLINE_S + EXPORT_DEADLINE_S
    assert BUILD_DEADLINE_S == L.hard_timeout_s("facet_edit")           # a build never outlives its tool's table value
