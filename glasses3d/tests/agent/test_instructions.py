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
    assert len(text) <= I.MAX_INSTRUCTION_CHARS == 24_000


def test_sandbox_font_and_export_hints_name_real_tools(tools):
    from glasses3d.g3d.safe_mode import HINTS
    names = {tool.name for tool in tools}
    assert {"load_font", "export_glb", "finish", "execute_blender_code"} <= names
    assert "load_font tool" in HINTS["font"] and "execute_blender_code" in HINTS["font"]
    assert "export_glb tool" in HINTS["export"] and "finish tool" in HINTS["export"]
    assert "deliver()" not in " ".join(HINTS.values())


def test_intake_and_history_documentation_matches_owner_rules():
    root = Path(__file__).parents[2]
    readme = (root / "agent/README.md").read_text(encoding="utf-8")
    plan = (root / "plan/ASSEMBLY.md").read_text(encoding="utf-8")
    assert "`reflective_floor` and `matte_mode` are owner-only fields" in readme
    assert "Coding agents may label only view kind and side" in readme
    assert "automatic matte selection" in readme
    assert "append-only: earlier messages and images are never replaced" in readme
    assert "superseded generated images become labelled notes" not in readme
    assert 'former "Set `glossy_floor` mattes at intake" instruction is superseded' in plan


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
                          "temple_stage", "photo_print", "labelled_model_map"}
    assert I.named_tools(core) & I.SPECIALIST_GUIDANCE.keys() == registered
    pending = I.SPECIALIST_GUIDANCE.keys() - registered
    for name in pending:
        text = I.build_instructions([*tools, SimpleNamespace(name=name)])
        assert I.named_tools(text) == frozenset(TOOL_NAMES) | {name}
    all_tools = [*tools, *(SimpleNamespace(name=name) for name in pending)]
    assert len(I.build_instructions(all_tools)) <= I.MAX_INSTRUCTION_CHARS


def test_owner_edit_teaching_keeps_all_tools_and_owner_priority_within_cap(tools):
    registered = [*tools, *(SimpleNamespace(name=name) for name in I.EDIT_GUIDANCE)]
    text = I.build_instructions(registered, owner_edit={"phase": "edit"})
    assert len(text) <= I.MAX_INSTRUCTION_CHARS
    assert I.named_tools(text) == {t.name for t in registered}
    assert "outranks photo readings" in text and "Every physical hard check remains" in text
    assert "Do not mutate the selected source geometry" in text and "before and after" in text


@pytest.mark.parametrize("phase", ["understand", "edit"])
def test_owner_phase_contract_matches_its_actual_registry_and_brief(phase):
    from glasses3d.agent import edits
    owner = {"phase": phase, "request_text": "Move the shown face outward.",
             "confirmation_text": "Yes, that face and amount.", "understanding": {}}
    tools = build_tools(SimpleNamespace(root=None, owner_edit=owner))
    text = I.build_instructions(tools, owner_edit=owner)
    assert contract_problems(I.instruction_contract(tools, owner_edit=owner), instructions=text,
                             brief=edits.brief_suffix(owner), tools=tools) == []
    assert text.count(MODEL_REQUIREMENTS) == 1
    assert len(text) <= I.MAX_INSTRUCTION_CHARS


@pytest.mark.parametrize("phase", ["understand", "edit"])
def test_capture_guidance_keeps_roles_coordinates_and_authenticated_scope(phase):
    owner = {"phase": phase}
    tools = build_tools(SimpleNamespace(root=None, owner_edit=owner))
    text = " ".join(I.build_instructions(tools, owner_edit=owner).split())
    for phrase in ("not a product photo", "annotated and clean native-pixel images", "source GLB hash",
                   "Labels identify points, lines or closed shapes", "unique across the edit session",
                   "owner's words verbatim per item", "keep its item ID", "bridge-relative millimetres",
                   "bridge underside", "GLB triangles are not Blender polygon IDs",
                   "background click has no surface anchor", "unresolved correspondence remains unresolved",
                   "Product photos still decide the look", "Capture UNDERSTAND items arrive unresolved",
                   "owner_mark_resolutions", "owner_labels", "candidate_target_ids", "supports_json",
                   "delivered scope_refs", "before any change", "anchor alone never authorizes a mutation"):
        assert phrase in text
    assert "All supplied images remain authoritative photos" not in text
    assert "The owner's product photos remain authoritative photos" in text
    for phrase in I.RETIRED_PHRASES:
        assert phrase not in text.lower()
    for pattern, _ in I.REMOVED_PATTERNS:
        assert not re.search(pattern, text, re.I)
    tool = next(tool for tool in tools if tool.name == "labelled_model_map")
    assert "owner_labels" in tool.params_json_schema["properties"]


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
    text = " ".join(I.build_instructions(tools).split())
    for phrase in ("closed solid", "Every supplied product photo from the owner is authoritative",
                   "Photos decide appearance", "Report every conflict", "A colourway name does not determine",
                   "Small branding stays", "Room reflections on lenses are not defects", "one fixed reflection",
                   "crystal and translucent", "complex pattern such as tortoiseshell", "photo-derived print",
                   "Plain frames need material, colour", "earlier owner complaints"):
        assert phrase in text
    assert text.index("Develop the complete frame and lenses") < text.index("Resolve lens") < text.index("Apply photo print")
    assert text.index("Use `export_glb`") < text.index("Use `arview_capture`") < text.index("Call `finish`")


def test_faint_facet_guidance_keeps_named_evidence_through_finish(tools):
    text = I.build_instructions(tools)
    assert "facet edges crisp in photos but faint in the build" in text
    assert "angle comparison/choice" in text
    assert "named mismatches and crops from the stage summary into the finish review" in text


def test_construction_guidance_compares_lens_materials_and_temple_sections(tools):
    lens = (
        "Choose body tint, transmission, surface reflection and any thin-film coating by their combined agreement "
        "with the photographs in the browser comparison. A visible colour shift does not establish matching "
        "brightness, saturation or colour distribution. Keep a comparison checkpoint before a material "
        "experiment, and compare the candidate with it in the same views before retaining the change."
    )
    temple = (
        "Judge temple section width, relief and transitions in top, rear and oblique evidence as well as the side "
        "silhouette. A stronger longitudinal highlight does not by itself prove the section matches. When a "
        "fitted camera is ambiguous, compare its alternatives and use the other photographs to identify "
        "differences visible under either camera."
    )
    main = " ".join(I.build_instructions(tools).split())
    assert main.count(lens) == main.count(temple) == 1
    assert main.index("2. Build a measurable model") < main.index(temple) < main.index("3. Complete construction")
    assert main.index("3. Complete construction") < main.index(lens) < main.index("4. Review the best state")
    for phase in ("understand", "edit"):
        owner = {"phase": phase}
        registered = build_tools(SimpleNamespace(root=None, owner_edit=owner))
        text = " ".join(I.build_instructions(registered, owner_edit=owner).split())
        assert lens not in text and temple not in text
        assert "Preserve unrequested geometry, materials, UVs and topology" in text
        assert "do not refit" in text


def test_shape_edits_receive_full_visual_review_and_owner_money_stays_scoped(tools):
    main = I.build_instructions(tools)
    assert "After each shape, colour or material edit, use `compare_look_to_photos`" in main
    assert "full-size photo/render pairs and close-ups for edges, rounding, rims, facets" in main
    assert "Do not finish while a visible difference you can still fix remains" in main
    assert "finish runway" not in main
    owner = {"phase": "edit"}
    text = I.build_instructions(build_tools(SimpleNamespace(root=None, owner_edit=owner)), owner_edit=owner)
    assert "After each authorized edit in EDIT, use `compare_look_to_photos`" in text
    assert "photo pairs, details, side-light checks and the worn front" in text
    assert "fixed by an authorized operation" in text and "finish runway" not in text
    assert "never permits changing sealed targets, directions, amounts or support" in text
    assert "`finish` is refused in UNDERSTAND" in text
    assert "STOP for the owner's confirmation" in text


def test_amount_choice_teaching_uses_delivered_preview_and_explicit_owner_selection(tools):
    main = I.build_instructions(tools)
    assert "preview_owner_edit" not in I.named_tools(main)
    owner = {"phase": "understand"}
    registered = build_tools(SimpleNamespace(root=None, owner_edit=owner))
    text = I.build_instructions(registered, owner_edit=owner)
    assert "preview_owner_edit" in I.named_tools(text)
    assert text.index("Use `labelled_model_map`") < text.index("use `preview_owner_edit`") < text.index("with `understand_finish`")
    for phrase in ("two or three options", "distinct positive finite scales", "same selected checkpoint",
                   "native photo/before/option comparisons", "target close-ups", "following_markings",
                   "selected source stays unchanged", "successfully delivered preview_receipt",
                   "same base operation_json", "owner must explicitly select an option",
                   "Do not choose an option or silently use the base amount", "STOP for the owner's confirmation",
                   "complete ordered operation group and keep corrections null",
                       "another amount or direction needs another shown preview",
                       "Every executable group requires a delivered preview and owner selection",
                   "requires the complete ordered group, including through manual edits",
                   "Child UNDERSTAND previews are advisory", "own delivered preview receipt"):
        assert phrase in text
    assert "does not provide a general" not in text
    assert "deformation-preview tool" not in text
    # The live schema must support the values the prompt asks Astra to pass, rather than advertising a future tool.
    names = {tool.name: tool for tool in registered}
    assert set(names["preview_owner_edit"].params_json_schema["properties"]) == {
        "map_id", "map_sha256", "capture_receipts", "operations_json", "options", "refs", "item_id"}
    assert "preview_receipt" in names["understand_finish"].params_json_schema["properties"]


def test_owner_diagnostic_contract_requires_disposition_and_teaches_no_authority():
    owner = {"phase": "understand"}
    registered = build_tools(SimpleNamespace(root=None, owner_edit=owner))
    text = I.build_instructions(registered, owner_edit=owner)
    for phrase in ("Inspect the mesh and relevant photos", "native photo/before/option comparisons", "unsupported or needs_clarification",
                   "explanatory reason and operation_json=[]", "Never invent no-op placeholders",
                   "All-blocked requests end as diagnostic_only", "Mixed plans are diagnostic only",
                   "Any blocked item prevents paid EDIT"):
        assert phrase in text
    tool = next(tool for tool in registered if tool.name == "understand_finish")
    schema = tool.params_json_schema["$defs"]["Interpretation"]
    assert {"disposition", "reason"}.issubset(schema["required"])
    assert schema["properties"]["disposition"]["enum"] == [
        "executable", "previewed", "cannot_preview", "unsupported", "needs_clarification"]
    assert "diagnostic_only" in tool.description and "never invent a no-op" in tool.description


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
                "budget_status": ("settled_usd", "settled_stop_usd", "remaining_to_stop_usd"),
                "finish": ("status accepted", "summary")}
    for name, fields in expected.items():
        assert name in {tool.name for tool in tools}
        for field in fields:
            assert field in I.CORE_GUIDANCE[name]
    assert "before the settled stop" not in I.CORE_GUIDANCE["finish"]
    assert "revisit every difference" in I.CORE_GUIDANCE["finish"]
    assert "money" not in I.CORE_GUIDANCE["finish"]
    assert "Do not finish while a visible difference you can still fix remains" in I.CORE_GUIDANCE["finish"]
    assert "finish runway" not in I.CORE_GUIDANCE["finish"]
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
