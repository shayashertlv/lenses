"""Runner wiring with real SDK tools/SQLite/budget, but no network or live Blender."""
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("agents")
from PIL import Image
from blender_agent import agent as module


class OfflineMCP:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def call_tool(self, name, arguments):
        assert name == "execute_blender_code"
        save = next(node for node in ast.walk(ast.parse(arguments["code"]))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "save_as_mainfile")
        values = {keyword.arg: ast.literal_eval(keyword.value) for keyword in save.keywords}
        Path(values["filepath"]).write_bytes(b"offline checkpoint")
        return SimpleNamespace(isError=False)


def test_run_and_resume_wire_history_dedup_new_tools_and_unchanged_effort(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-never-dispatched")
    monkeypatch.setattr(module, "mcp_server", lambda *_: OfflineMCP())
    seen = []

    async def runner(agent, new_input, *, session, **kwargs):
        tools = {tool.name for tool in agent.tools}
        assert {"read_evidence_json", "record_review", "inspect_export", "preview_ar",
                "preview_portrait", "budget_status"} <= tools
        assert agent.model == "gpt-6-astra"
        assert agent.model_settings.reasoning.effort == "max"
        assert agent.model_settings.max_tokens == 25000
        assert kwargs["run_config"].tool_execution.max_function_tool_concurrency == 1
        seen.append({"history": await session.get_items(), "new": new_input})
        # Mirrors SDK input persistence. No provider request is dispatched.
        await session.add_items(new_input)
        return SimpleNamespace(final_output="Offline integration completed")

    monkeypatch.setattr(module.Runner, "run", runner)
    photo = tmp_path / "reference.png"
    Image.new("RGB", (8, 6), "red").save(photo)
    prompt = tmp_path / "brief.txt"
    prompt.write_text("Build from these reference images.")
    args = SimpleNamespace(output=tmp_path / "trial", prompt_file=prompt, photo=[photo],
                           env=None, reasoning_effort="max", max_turns=40, max_output_tokens=25000,
                           max_usd="20", resume=False, run_paid=True, meters_per_unit=.001,
                           bridge_origin=None)
    first = asyncio.run(module.run(args))
    args.resume = True
    second = asyncio.run(module.run(args))
    assert first["status"] == second["status"] == "completed"
    assert first["trial_budget"]["committed_usd"] == second["trial_budget"]["committed_usd"] == "0.000000"
    assert first["stop_checkpoint"]["saved"] and second["stop_checkpoint"]["saved"]
    assert len(seen[0]["history"]) == 0 and len(seen[1]["history"]) == 1
    parts = lambda item: item["new"][0]["content"]
    assert sum(part["type"] == "input_image" for part in parts(seen[0])) == 1
    assert sum(part["type"] == "input_image" for part in parts(seen[1])) == 0
    receipt = json.loads((Path(second["review_directory"]) / "input-receipt.json").read_text())
    assert receipt["brief"]["action"] == "reuse_history"
    assert receipt["images_reused_from_history"] == 1
    assert second["trial_budget"]["prior_invocations"] == 1
