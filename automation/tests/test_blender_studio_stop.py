import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("agents")
from blender_agent.agent import ReviewLog, checkpoint_on_exit


def test_stop_prevents_new_request_before_budget_reservation(tmp_path):
    flag = tmp_path / "stop"
    flag.touch()
    log = ReviewLog(tmp_path / "run", stop_file=flag)
    calls = []
    async def reserve(_): calls.append("reserved")
    log.budget = SimpleNamespace(before_request=reserve)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(log.before_request(SimpleNamespace(content=b"{}")))
    assert calls == []


def test_stop_prevents_next_tool_but_exit_checkpoint_still_saves(tmp_path):
    flag = tmp_path / "stop"; flag.touch()
    log = ReviewLog(tmp_path / "run", stop_file=flag)
    events = []
    class Server:
        async def call_tool(self, name, args):
            events.append(name)
            (tmp_path / "stop-checkpoint.blend").write_bytes(b"checkpoint")
            return SimpleNamespace(isError=False)
    summary = {}
    async def scenario():
        async with checkpoint_on_exit(Server(), tmp_path, summary, log):
            await log.on_tool_start(SimpleNamespace(), None, SimpleNamespace(name="execute_blender_code"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())
    assert events == ["execute_blender_code"]
    assert summary["stop_checkpoint"]["saved"] is True


def test_regular_logs_without_stop_flag_are_unchanged(tmp_path):
    log = ReviewLog(tmp_path)
    asyncio.run(log.on_llm_start(None, None, "", []))
    assert log.interactions == 1


def test_stop_while_starting_survives_launch_and_stops_first_model_step(tmp_path):
    from blender_agent.studio_jobs import JobStore
    store = JobStore(tmp_path / "studio")
    job = store.create({"description": "Shield"})
    metadata = store.metadata(job["id"])
    metadata.update(status="starting", started_once=True)
    store.save(metadata)
    stopped = store.stop(job["id"])
    assert stopped["status"] == "stopping"
    flag = store.directory(job["id"]) / "stop.requested"
    log = ReviewLog(tmp_path / "run", stop_file=flag)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(log.on_llm_start(None, None, "", []))
    assert log.interactions == 0
