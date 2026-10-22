"""Deliberate decision-only tool/index expansion; legacy identity lives in task21 tests."""
from types import SimpleNamespace

import pytest

from glasses3d.agent import instructions as I, tool_timeouts as T
from glasses3d.agent.tools import build_tools
from glasses3d.agent.instruction_profiles import legacy_tool_text


@pytest.mark.parametrize("phase", [None, "understand", "edit"])
def test_candidates_camera_and_best_are_decision_only_and_named(phase):
    owner = {"phase": phase} if phase else None
    scene = SimpleNamespace(root=None, owner_edit=owner, instruction_profile="decision-20261021")
    tools = build_tools(scene)
    names = {tool.name: tool for tool in tools}
    for name in ("compare_lens_candidates", "choose_camera_orientation"):
        assert name in names
        assert T.sdk_timeout_s(name) > T.inner_steps_s(name)
    assert "best" in names["record_difference"].params_json_schema["properties"]
    text = I.build_instructions(tools, owner_edit=owner, profile="decision-20261021")
    assert I.named_tools(text) == set(names)
    assert len(text) < I.MAX_INSTRUCTION_CHARS
    assert "affected refs persist" in text and "resolution needs all refs" in text
    legacy = legacy_tool_text(tools, owner_edit=owner)
    old_names = {tool.name for tool in legacy}
    assert not {"compare_lens_candidates", "choose_camera_orientation", "record_difference"} & old_names
    I.build_instructions(legacy, owner_edit=owner, profile="legacy-20261021")


def test_new_teaching_describes_choices_without_parameters_or_promotion_default():
    tools = build_tools(SimpleNamespace(instruction_profile="decision-20261021"))
    text = I.build_instructions(tools, profile="decision-20261021")
    assert "Do not rule out\nuntested options by prediction alone" in text
    assert "Astra chooses every value" in text
    assert "Photographic arm opening is separate from camera orientation" in text
    assert "best=True while recording its current-head comparison decision and remaining differences" in text
    assert "prose does not set the draft source" in text
    assert T.inner_steps_s("compare_lens_candidates") == (
        T.export_steps_s() + 12 * T.capture_steps_s(T.LOOK_CAPTURE_DEADLINE_S))


def test_all_six_labelled_candidate_gaps_remain_immediate_when_report_is_paged(tmp_path):
    from agents.tool import ToolOutputText
    from glasses3d.agent import tool_reports as R
    from glasses3d.agent.tools.look import LENS_COLOUR_JUDGEMENT
    gaps = [f"Candidate {i}: reference-01 lens: AR L* {50+i} vs photo 63 (diagnostic only)" for i in range(6)]
    body = {"schema": "g3d.lens_candidates/1", "status": "ok", "lens_colour_gap": gaps,
            "lens_colour_judgement": LENS_COLOUR_JUDGEMENT, "candidates": [{"capture": "x" * 30000}] * 6,
            "apply_materials": [{"material": "lens", "source_double_sided": False}],
            **{f"diagnostic_{i}": "x" * 10000 for i in range(90)}}
    result = R.limit_output(tmp_path, "candidate-call", "compare_lens_candidates", [ToolOutputText(text=R.dumps(body))])
    shown, _ = R._body(result[0].text)
    assert shown["paged"]
    assert shown["lens_colour_gap"] == gaps
    assert shown["lens_colour_judgement"] == LENS_COLOUR_JUDGEMENT
    assert shown["apply_materials"] == body["apply_materials"]
