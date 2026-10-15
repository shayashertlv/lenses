"""M2 instruction contract: current SDK registry, owner requirements, report facts and retired language."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from glasses3d.agent import instructions as I
from glasses3d.agent.brief import MODEL_REQUIREMENTS
from glasses3d.agent.loop import REMOVED_PHRASES, contract_problems, schema_descriptions
from glasses3d.agent.tools import TOOL_NAMES, build_tools


@pytest.fixture
def tools():
    return build_tools(SimpleNamespace())


def test_current_registry_is_complete_and_named_independently(tools):
    text = I.build_instructions(tools)
    assert I.named_tools(text) == frozenset(TOOL_NAMES) == I.instruction_contract(tools).named
    assert contract_problems(I.instruction_contract(tools), instructions=text, brief="Job facts.", tools=tools) == []
    assert text == I.build_instructions(list(reversed(tools)))
    assert len(text) <= I.MAX_INSTRUCTION_CHARS == 8000


def test_owner_table_is_exact_and_included_once(tools):
    text = I.build_instructions(tools)
    plan = (Path(__file__).parents[2] / "plan/PLAN.md").read_text(encoding="utf-8")
    table = "\n".join(line.strip() for line in plan.splitlines() if line.startswith("  |"))
    assert MODEL_REQUIREMENTS == table
    assert text.count(MODEL_REQUIREMENTS) == 1


def test_all_optional_specialists_are_absent_until_registered_and_fit_cap(tools):
    core = I.build_instructions(tools)
    registered = set(TOOL_NAMES) & I.SPECIALIST_GUIDANCE.keys()
    assert registered == {"seat_lens", "hide_eyewire", "check_lens_seating", "unseat_lens", "unhide_eyewire",
                          "temple_stage", "photo_print"}
    assert I.named_tools(core) & I.SPECIALIST_GUIDANCE.keys() == registered
    pending = I.SPECIALIST_GUIDANCE.keys() - registered
    for name in pending:
        text = I.build_instructions([*tools, SimpleNamespace(name=name)])
        assert I.named_tools(text) == frozenset(TOOL_NAMES) | {name}
    all_tools = [*tools, *(SimpleNamespace(name=name) for name in pending)]
    assert len(I.build_instructions(all_tools)) <= I.MAX_INSTRUCTION_CHARS


@pytest.mark.parametrize("bad_registry", ["missing", "unknown", "duplicate"])
def test_registry_changes_cannot_silently_lose_guidance(tools, bad_registry):
    if bad_registry == "missing":
        tools.pop(0)  # a required core tool, independent of which optional specialists are now registered
    elif bad_registry == "unknown":
        tools.append(SimpleNamespace(name="invented_tool"))
    else:
        tools.append(tools[0])
    with pytest.raises(ValueError, match="instruction registry mismatch"):
        I.build_instructions(tools)


def test_retired_language_is_absent_from_prompt_and_current_tool_descriptions(tools):
    text = I.build_instructions(tools)
    inputs = [text, *I.SPECIALIST_GUIDANCE.values()]
    for tool in tools:
        inputs.append(tool.description)
        inputs.extend(value for _path, value in schema_descriptions(tool.params_json_schema))
    assert set(REMOVED_PHRASES) <= set(I.RETIRED_PHRASES)
    for value in inputs:
        collapsed = " ".join(value.split())
        assert not [phrase for phrase in I.RETIRED_PHRASES if phrase in collapsed.lower()]
        assert not [pattern for pattern, _ in I.REMOVED_PATTERNS if re.search(pattern, collapsed, re.I)]


@pytest.mark.parametrize("pattern,witness", I.REMOVED_PATTERNS)
def test_every_removed_pattern_catches_its_retired_witness(pattern, witness):
    assert re.search(pattern, witness, re.I)


@pytest.mark.parametrize("phrase", I.RETIRED_PHRASES)
def test_runtime_contract_refuses_each_retired_literal(tools, phrase):
    problems = contract_problems(I.instruction_contract(tools), instructions=I.build_instructions(tools) + "\n" + phrase,
                                 brief="Job facts.", tools=tools)
    assert any("removed phrase" in problem for problem in problems)


def test_guidance_teaches_recorded_decisions_and_stage_order(tools):
    text = I.build_instructions(tools)
    for phrase in ("closed solid", "Every supplied reference image is an authoritative photo",
                   "Photos decide appearance", "Report every conflict", "A colourway name does not determine",
                   "Small branding stays", "Room reflections on lenses are not defects", "one fixed reflection",
                   "crystal and translucent", "complex pattern such as tortoiseshell", "photo-derived print",
                   "plain frames need material, colour", "earlier owner complaints"):
        assert phrase in text
    assert text.index("Develop the complete frame and lenses") < text.index("Resolve lens") < text.index("Apply photo print")
    assert text.index("Use `export_glb`") < text.index("Use `arview_capture`") < text.index("Call `finish`")


def test_report_guidance_names_fields_the_current_tools_return(tools):
    # Keep this focused on the fields the prose asks Astra to act on. These correspond to the actual wrappers and
    # Seeing return records, not to the superseded WP7 checks.json proposal.
    expected = {"scene_summary": ("result.objects", "closed", "loose_parts"),
                "fit_cameras": ("basis", "refs", "status", "iou", "contour_mean_mm", "flags"),
                "render_views": ("sheets", "views", "ref", "basis", "manifest"),
                "compare_to_photos": ("refs", "sheet", "contour_mean_mm", "model_only_px", "photo_only_px", "delta"),
                "pick_surface_point": ("hits", "hit", "object", "point_mm"),
                "read_image": ("images",), "export_glb": ("status", "sha256", "validate_spec", "seat_check"),
                "arview_capture": ("sheets", "status", "errors", "warnings", "export_sha256"),
                "budget_status": ("settled_usd", "remaining_usd", "planning", "settled_stop_usd", "remaining_to_stop_usd"),
                "finish": ("status accepted", "summary")}
    for name, fields in expected.items():
        assert name in {tool.name for tool in tools}
        for field in fields:
            assert field in I.CORE_GUIDANCE[name]
    assert "before the settled stop" in I.CORE_GUIDANCE["finish"]
    assert "revisit every difference" in I.CORE_GUIDANCE["finish"]
    assert "fix it or report it" in I.CORE_GUIDANCE["finish"]
    assert "per_photo differences" in I.CORE_GUIDANCE["finish"]

    # Check the implementation's actual dictionary keys, rather than accepting a new field invented in the text.
    root = Path(__file__).parents[2]
    sources = {"scene_summary": ("g3d/worker.py", "m_scene_summary"),
               "fit_cameras": ("agent/seeing.py", "fit_cameras"),
               "render_views": ("agent/seeing.py", "render_views"),
               "compare_to_photos": ("agent/seeing.py", "_compare_v1"),
               "pick_surface_point": ("g3d/render.py", "pick"),
               "read_image": ("agent/tools/__init__.py", "_output"),
               "export_glb": ("agent/seeing.py", None),
               "arview_capture": ("agent/tools/arview.py", "capture"),
               "budget_status": ("agent/budget.py", "summary"),
               "finish": ("agent/tools/__init__.py", "finish_current")}
    for name, (file, function) in sources.items():
        tree = ast.parse((root / file).read_text(encoding="utf-8"))
        nodes = [tree] if function is None else [n for n in ast.walk(tree)
                                               if isinstance(n, ast.FunctionDef) and (n.name == function or
                                                   function == "fit_cameras" and n.name == "_fit_ref_summary")]
        assert nodes, (file, function)
        literals = {n.value for node in nodes for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        # result is the journal envelope; refs/hits entries are nested tool records. Compound prose phrases name
        # the leaf field and, for accepted, the value that the wrapper really emits.
        for field in expected[name]:
            for leaf in field.removeprefix("result.").split():
                assert leaf in literals, (name, leaf)
