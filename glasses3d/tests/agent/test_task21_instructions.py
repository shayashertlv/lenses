"""Decision teaching, authoritative colour priority and frozen legacy transport text."""
import ast
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from glasses3d.agent import instructions as I
from glasses3d.agent.instruction_profiles import (
    DEFAULT_PROFILE, LEGACY_PROFILE, legacy_text, legacy_tool_text, resolve_profile,
)
from glasses3d.agent.loop import contract_problems
from glasses3d.agent.tools import build_tools, look

ROOT = Path(__file__).resolve().parents[3]


def normalized(value):
    return " ".join(value.split())


def baseline_source(path):
    return subprocess.check_output(["git", "show", f"28abe77:{path}"], cwd=ROOT).decode("utf-8")


def assignment(source, name):
    tree = ast.parse(source)
    return next(ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name
                                                       for target in node.targets))


def test_new_default_is_explicit_and_unknown_profiles_refuse():
    assert resolve_profile() == DEFAULT_PROFILE == "decision-20261021"
    assert resolve_profile(LEGACY_PROFILE) == "legacy-20261021"
    with pytest.raises(ValueError, match="unknown instruction profile"):
        resolve_profile("decision-typo")


def test_new_prompt_contract_and_decision_loop_keep_owner_authority():
    tools = build_tools(SimpleNamespace())
    text = I.build_instructions(tools)
    assert text != legacy_text("main")
    assert I.named_tools(text) == {tool.name for tool in tools}
    assert "record_difference" in I.named_tools(text)
    assert len(text) <= I.MAX_INSTRUCTION_CHARS == 24_000
    assert contract_problems(I.instruction_contract(tools), instructions=text, brief="Owner facts.", tools=tools) == []
    assert text == I.build_instructions(list(reversed(tools)))
    words = normalized(text)
    for phrase in ("likely cause and expected comparison evidence", "Name the improvement and any regression",
                   "retain or restore for that reason", "hue, lightness, colour distribution and see-through",
                   "last correction and outcome", "best=True", "host-recorded formal best",
                   "candidate not yet verified", "prose does not set the draft source", "host-recorded list",
                   "finish refuses open items", "unknown or stale evidence references", "append-only",
                   "Every supplied product photo from the owner is authoritative", "Only the owner accepts likeness",
                   "facet edges crisp in photos but faint in the build", "certain contradictory measurements still refuse"):
        assert phrase in words
    assert "Before building, name the product's main parts" not in text  # proposal #1 is outside this task


def test_legacy_main_and_notes_are_exact_28abe77_bytes():
    source = baseline_source("glasses3d/agent/instructions.py")
    namespace = {"__name__": "glasses3d.agent._legacy_test", "__package__": "glasses3d.agent"}
    exec(compile(source, "28abe77:instructions.py", "exec"), namespace)
    tools = legacy_tool_text(build_tools(SimpleNamespace()))
    text = I.build_instructions(tools, profile=LEGACY_PROFILE)
    assert text.encode() == namespace["build_instructions"](tools).encode() == legacy_text("main").encode()
    assert hashlib.sha256(text.encode()).hexdigest() == "e309318c416a24a67b22cd6174ba4b9be705227ddc93de1a11f56acb162dc30f"
    for path, variable, key in (
        ("tools/look.py", "LENS_COLOUR_JUDGEMENT", "look_note"),
        ("renewal.py", "SUMMARY_REQUEST", "summary_request"),
        ("loop.py", "FINISH_NOTE", "no_tool_finish_note_template"),
    ):
        assert legacy_text(key).encode() == assignment(baseline_source("glasses3d/agent/" + path), variable).encode()


def test_legacy_registry_matches_task20_snapshot_without_mutating_default():
    tools = build_tools(SimpleNamespace())
    before = [(tool.description, json.dumps(tool.params_json_schema, sort_keys=True)) for tool in tools]
    restored = legacy_tool_text(tools)
    assert "record_difference" not in {tool.name for tool in restored}
    registry = [{"name": tool.name, "description": tool.description, "parameters": tool.params_json_schema}
                for tool in restored]
    canonical = json.dumps(registry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    # Task20's independently recorded canonical registry digest includes every description and schema byte.
    assert hashlib.sha256(canonical.encode()).hexdigest() == "5bdd90c48f0a8fae5084c5b7a33e71d54b642d80070b39b00ecb0fa28dacb0f1"
    snapshot = ROOT / "glasses3d/data/review_20261018/codex/task20_text_snapshot.json"
    if snapshot.is_file():
        assert registry == json.loads(snapshot.read_text(encoding="utf-8"))["registry"]
    assert before == [(tool.description, json.dumps(tool.params_json_schema, sort_keys=True)) for tool in tools]
    restored[0].params_json_schema["properties"]["code"]["description"] = "local mutation"
    assert before == [(tool.description, json.dumps(tool.params_json_schema, sort_keys=True)) for tool in tools]


def test_worn_priority_agrees_in_prompt_description_and_immediate_note():
    tools = build_tools(SimpleNamespace())
    description = next(tool.description for tool in tools if tool.name == "compare_look_to_photos")
    note = look.lens_colour_judgement()
    for value in (I.build_instructions(tools), description, note):
        assert normalized(note) in normalized(value)
        assert "Judge photo-background colour match and worn-face likeness together" not in value
        assert "then the worn" not in value
    assert look.lens_colour_judgement(LEGACY_PROFILE) == legacy_text("look_note")
    assert note != legacy_text("look_note")


@pytest.mark.parametrize("profile", [DEFAULT_PROFILE, LEGACY_PROFILE])
def test_look_wrapper_passes_selected_profile_to_capture(monkeypatch, profile):
    seen = []

    def capture(root, export, call_id, **kwargs):
        seen.append(kwargs["profile"])
        return {"lens_colour_judgement": look.lens_colour_judgement(kwargs["profile"])}

    async def call(ctx, name, operation):
        return operation("look-test")

    monkeypatch.setattr(look, "capture", capture)
    scene = SimpleNamespace(root="unused", instruction_profile=profile,
                            seeing=SimpleNamespace(export_head=lambda **kwargs: {}))
    callback = look.tool_for(scene, lambda name: lambda fn: fn, call)
    result = asyncio.run(callback(SimpleNamespace()))
    assert seen == [profile]
    assert result["lens_colour_judgement"] == look.lens_colour_judgement(profile)


def test_paging_requires_only_facts_needed_for_the_decision():
    tools = build_tools(SimpleNamespace())
    description = next(tool.description for tool in tools if tool.name == "read_report")
    for value in (description, I.build_instructions(tools)):
        words = normalized(value)
        assert "read_pointer" in words
        assert "next decision" in words
        assert "unread" in words
        assert "unrelated" in words


@pytest.mark.parametrize("phase", ["understand", "edit"])
def test_owner_edit_keeps_scope_and_has_no_competing_colour_order(phase):
    owner = {"phase": phase}
    tools = build_tools(SimpleNamespace(root=None, owner_edit=owner))
    text = I.build_instructions(tools, owner_edit=owner)
    assert I.named_tools(text) == {tool.name for tool in tools}
    assert "Do not mutate the selected source geometry" in text
    assert "This never expands confirmed scope" in text
    assert "does not overrule the worn result" in text
    assert "Judge photo-background colour match and worn-face likeness together" not in text
    assert "then the worn" not in text
    assert len(text) <= I.MAX_INSTRUCTION_CHARS
