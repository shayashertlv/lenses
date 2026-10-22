"""Task 21b changes two decision index sentences while preserving legacy transport."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from glasses3d.agent import instructions as I
from glasses3d.agent.instruction_profiles import DEFAULT_PROFILE, LEGACY_PROFILE
from glasses3d.agent.loop import contract_problems
from glasses3d.agent.tools import build_tools


ROOT = Path(__file__).resolve().parents[3]
CHANGED_SENTENCES = (
    ("`finish`: review then confirm.",
     "`finish`: review then confirm with difference_updates; omitted evidence_ref uses that review."),
    ("`record_difference`: persist/update important differences and their evidence, status, correction and next action.",
     "`record_difference`: persist/update one or several important differences atomically via items, with their evidence, status, correction and next action."),
)


@lru_cache(maxsize=1)
def baseline_source():
    return subprocess.check_output(
        ["git", "show", "22f3980:glasses3d/agent/instructions.py"], cwd=ROOT).decode("utf-8")


def test_task21b_historical_diff_and_current_batch_guidance():
    expected = baseline_source()
    # Task 23 authorizes additional tools/teaching. Keep the exact task-21b
    # assertion on its immutable commit, and require both sentences today too.
    current = subprocess.check_output(
        ["git", "show", "74142c2:glasses3d/agent/instructions.py"], cwd=ROOT).decode("utf-8")
    for old, new in CHANGED_SENTENCES:
        assert expected.count(old) == current.count(new) == 1
        expected = expected.replace(old, new)
    assert current == expected
    # The task-21b sentences belong to the selectable decision-20261021 text.
    tools = build_tools(SimpleNamespace(instruction_profile="decision-20261021"))
    text = I.build_instructions(tools, profile="decision-20261021")
    assert all(new in text for _old, new in CHANGED_SENTENCES)
    assert len(text) <= I.MAX_INSTRUCTION_CHARS == 24_000
    assert contract_problems(I.instruction_contract(tools, profile="decision-20261021"), instructions=text,
                             brief="Owner facts.", tools=tools) == []


@pytest.mark.parametrize("phase", [None, "understand", "edit"])
def test_batch_and_inline_schemas_do_not_change_legacy_teaching_or_schema(phase):
    owner = {"phase": phase} if phase else None
    current = build_tools(SimpleNamespace(root=None, instruction_profile=DEFAULT_PROFILE, owner_edit=owner))
    by_name = {tool.name: tool for tool in current}
    assert "items" in by_name["record_difference"].params_json_schema["properties"]
    assert "difference_updates" in by_name["finish"].params_json_schema["properties"]

    legacy = build_tools(SimpleNamespace(root=None, instruction_profile=LEGACY_PROFILE, owner_edit=owner))
    old = {tool.name: tool for tool in legacy}
    assert "record_difference" not in old
    assert "difference_updates" not in old["finish"].params_json_schema["properties"]
    namespace = {"__name__": "glasses3d.agent._task21b_baseline", "__package__": "glasses3d.agent"}
    exec(compile(baseline_source(), "22f3980:instructions.py", "exec"), namespace)
    text = I.build_instructions(legacy, owner_edit=owner, profile=LEGACY_PROFILE)
    assert text.encode("utf-8") == namespace["build_instructions"](
        legacy, owner_edit=owner, profile=LEGACY_PROFILE).encode("utf-8")
    assert len(text) <= I.MAX_INSTRUCTION_CHARS
    assert contract_problems(I.instruction_contract(legacy, owner_edit=owner, profile=LEGACY_PROFILE),
                             instructions=text, brief="Owner facts.", tools=legacy) == []
