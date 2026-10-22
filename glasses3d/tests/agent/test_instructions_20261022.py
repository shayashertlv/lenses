"""decision-20261022, the owner-approved audit rewrite (plan/AUDIT_20261022_ASTRA_DELIVERY.md): its text contract,
the coating search offered only with it, difference updates carried by an edit, and the trimmed descriptions."""
import asyncio
import re
from types import SimpleNamespace

import pytest

from glasses3d.agent import coating_match, differences as D, instructions as I, money
from glasses3d.agent.brief import MODEL_REQUIREMENTS
from glasses3d.agent.instruction_profiles import DEFAULT_PROFILE, PROFILES, legacy_tool_text
from glasses3d.agent.loop import contract_problems
from glasses3d.agent.tools import TOOL_NAMES, build_tools, look
from glasses3d.tests.agent.test_finish_review import first, review_scene  # noqa: F401 - fixture
from glasses3d.tests.agent.test_job_tools import finish_scene, invoke, record, scene  # noqa: F401 - fixtures
from glasses3d.tests.agent.test_task21_differences import decision_scene, entry  # noqa: F401 - fixture

PROFILE = "decision-20261022"


def words(text):
    return " ".join(text.split())


@pytest.fixture
def tools():
    return build_tools(SimpleNamespace(instruction_profile=PROFILE))


def test_default_profile_and_contract_cover_every_registered_tool_once(tools):
    assert DEFAULT_PROFILE == PROFILE and PROFILES == (PROFILE, "decision-20261021", "legacy-20261021")
    text = I.build_instructions(tools)
    assert text == I.build_instructions(tools, profile=PROFILE) == I.build_instructions(list(reversed(tools)))
    assert I.named_tools(text) == {tool.name for tool in tools} == set(TOOL_NAMES)
    assert contract_problems(I.instruction_contract(tools), instructions=text, brief="Owner facts.", tools=tools) == []
    assert len(text) <= I.MAX_INSTRUCTION_CHARS
    rows = [line for line in text.splitlines() if line.startswith("| ") and "`" in line]
    assert len(rows) == len(tools) and all(len(I.named_tools(row)) == 1 for row in rows)
    for problem in ([*tools, SimpleNamespace(name="invented_tool")], [*tools, tools[0]]):
        with pytest.raises(ValueError, match="instruction registry mismatch"):
            I.build_instructions(problem)


def test_the_order_and_the_check_after_every_edit_are_restored(tools):
    text = words(I.build_instructions(tools))
    for phrase in ("Work in this order", "Note every place where parts meet in the photos",
                   "Wherever parts meet in the photos they meet in the model: no gap, slit or floating part there",
                   "Read scene_summary's contacts after building",
                   "fit_cameras once the geometry can explain the photos",
                   "A structural break (parts that touch in the photos but not in the model, a floating or missing part) "
                   "comes before appearance work, even when the brief names appearance as the priority",
                   "After every shape or material edit, run compare_look_to_photos",
                   "Render sheets, AR sheets, fit residuals and other numbers do not replace this comparison",
                   "All capabilities remain available",
                   "Keep correcting while a visible difference you can still fix remains",
                   "Photographic arm opening is separate from camera orientation",
                   "Do not rule out untested options by prediction alone",
                   "The owner fine-tunes lens colour in the editor afterwards; get the lens family, see-through and "
                   "coating presence close with one coating search and one side-by-side test, then return to "
                   "structure and shape",
                   "earlier owner complaints", "Every supplied product photo from the owner is authoritative"):
        assert phrase in text, phrase
    steps = [text.index(step) for step in ("a. Structure", "b. Cameras", "c. Shape detail", "d. Lens seating",
                                           "e. Appearance", "4. The revision loop", "8. Which tool when")]
    assert steps == sorted(steps)


def test_shared_sentences_are_word_for_word_and_host_jargon_is_gone(tools):
    text = words(I.build_instructions(tools))
    assert text.count(money.MONEY_INSTRUCTION) == 1
    assert text.count(words(look.LENS_COLOUR_JUDGEMENT)) == 1
    assert "metallic multiplies the colour seen through the lens by (1 - metallic)" in text
    for gone in ("host-recorded", "forced spending", "Status never authorizes another paid run",
                 "Preserve the owner's brief verbatim", "Nonblank per-photo text", "append-only", "7. Tool index"):
        assert gone not in text
    assert MODEL_REQUIREMENTS not in I.build_instructions(tools)  # the brief carries the owner's table
    for phrase in I.RETIRED_PHRASES:
        assert phrase not in text.lower()
    for pattern, _ in I.REMOVED_PATTERNS:
        assert not re.search(pattern, text, re.I)


def test_paging_rule_lives_in_read_report_and_the_table_points_to_it(tools):
    text = I.build_instructions(tools)
    row = next(line for line in text.splitlines() if "`read_report`" in line)
    assert "read_pointer" in row
    description = words(next(tool.description for tool in tools if tool.name == "read_report"))
    for phrase in ("read_pointer", "next decision", "still unread", "unrelated diagnostic fields need not be paged"):
        assert phrase in description


def test_coating_search_is_offered_only_where_its_teaching_exists():
    def names(**scene):
        return {tool.name for tool in build_tools(SimpleNamespace(**scene))}
    assert "match_coating_colours" in names(instruction_profile=PROFILE)
    assert "match_coating_colours" not in names(instruction_profile="decision-20261021")
    assert "match_coating_colours" not in names(instruction_profile="legacy-20261021")
    assert "match_coating_colours" not in names(root=None, owner_edit={"phase": "edit"})
    restored = legacy_tool_text(build_tools(SimpleNamespace(instruction_profile=PROFILE)))
    assert "match_coating_colours" not in {tool.name for tool in restored}


def test_coating_search_tool_returns_the_edit_page_choices(tools):
    tool = next(tool for tool in tools if tool.name == "match_coating_colours")
    scene = SimpleNamespace()

    async def call(ctx, name, action):
        return action("coating-call")
    callback = __import__("glasses3d.agent.tools.coating_tool", fromlist=["tool_for"]).tool_for(
        scene, lambda name: lambda fn: fn, call)
    result = asyncio.run(callback(SimpleNamespace(), straight_on_srgb=[90, 190, 140], sides_srgb=[170, 110, 200],
                                  lens_ior=6))
    assert result["status"] == "ok" and result["choices"][0]["thickness_nm"] == 444
    assert result["choices"][0]["film_ior"] == 1.8 and result["choices"][0]["quality"] == "close match"
    assert result["kept"] == {"ior": 6.0, "metallic": 0.0, "tint": [1.0, 1.0, 1.0], "iridescence": 1.0}
    grey = asyncio.run(callback(SimpleNamespace(), straight_on_srgb=[128, 128, 128], sides_srgb=[20, 200, 30],
                                lens_ior=3))
    assert grey["choices"] == [] and "clear colours" in grey["reason"]
    for bad in ({"straight_on_srgb": [90, 190]}, {"lens_ior": 0.5}, {"strength": 2}, {"tint": [1, 1]}):
        arguments = {"straight_on_srgb": [90, 190, 140], "sides_srgb": [170, 110, 200], "lens_ior": 6, **bad}
        with pytest.raises(ValueError):
            asyncio.run(callback(SimpleNamespace(), **arguments))
    assert "g3d.lens_coating(material, factor=strength, ior=film_ior" in words(tool.description)
    assert coating_match.FILM_IORS[0] == 1.2 and coating_match.FILM_IORS[-1] == 2.4


def test_an_edit_records_its_differences_first_and_a_refusal_runs_no_code(decision_scene):
    s = decision_scene
    first(s)
    refused = record(invoke(s, "execute_blender_code", {"code": "pass", "difference_updates": [
        entry(evidence_ref="never-delivered")]}, cid="edit-refused"))
    assert refused["status"] == "refused" and refused["message"].startswith("No code ran")
    assert not [call for call in s.s.calls if call[0] == "execute"] and D.snapshot(s) == []
    done = record(invoke(s, "execute_blender_code", {"code": "pass", "label": "coating", "difference_updates": [
        entry(next_action="Raise the coating thickness in this edit, then compare.")]}, cid="edit-recorded"))
    assert done["status"] == "ok" and done["recorded_differences"] == ["lens-hue"]
    assert [call[0] for call in s.s.calls].count("execute") == 1
    rows = [row for row in D._records(s) if row["kind"] == "difference"]
    assert rows[-1]["call_id"] == "edit-recorded" and rows[-1]["item"]["evidence_ref"] == "first"


def test_descriptions_drop_product_irrelevant_text_and_keep_owner_scope(tools):
    docs = {tool.name: words(tool.description) for tool in tools}
    assert "In owner edit mode" not in docs["execute_blender_code"]
    assert "difference_updates" in docs["execute_blender_code"]
    assert "3.2 mm" not in docs["temple_stage"] and "20,000" not in docs["temple_stage"]
    assert "Its status must be accepted" not in docs["finish"] and "contacts lists separate parts" in docs["finish"]
    assert "decision_table" in docs["fit_cameras"] and "result.contacts lists near misses" in docs["scene_summary"]
    owner = build_tools(SimpleNamespace(root=None, owner_edit={"phase": "edit"}))
    assert "In owner edit mode use the labelled, previewed and confirmed owner operations" in next(
        tool.description for tool in owner if tool.name == "execute_blender_code")


def test_a_comparison_on_an_older_camera_set_counts_until_the_model_changes(decision_scene, monkeypatch):
    # 10-22 run: Astra kept basis-01 after a refit made basis-02, and every citation was refused as "unknown".
    import copy
    from glasses3d.agent.tools import finish_review as F
    s = decision_scene
    newest = {**copy.deepcopy(s.binding), "basis": "basis-002", "basis_sha256": "newer", "cameras_sha256": "newer"}

    def evidence(root, head, export, basis=None):
        chosen = s.binding if basis == "basis-001" else newest
        return {"checkpoint": head["id"], "fingerprint": head["fingerprint"], "export_sha256": export["sha256"],
                **copy.deepcopy(chosen)}
    monkeypatch.setattr(F, "evidence", evidence)
    assert record(invoke(s, "compare_look_to_photos", {}, cid="old-basis-look"))["status"] == "ok"
    out = record(invoke(s, "record_difference", entry(evidence_ref="old-basis-look"), cid="item"))
    assert out["status"] == "ok" and D.snapshot(s)[0]["evidence_ref"] == "old-basis-look"
    assert record(invoke(s, "execute_blender_code", {"code": "pass"}, cid="edit"))["status"] == "ok"
    stale = record(invoke(s, "record_difference", entry(evidence_ref="old-basis-look", status="fixed"), cid="late"))
    assert stale["status"] == "refused" and "stale" in stale["message"]


def test_a_comparison_that_cannot_count_says_why(decision_scene, monkeypatch):
    from glasses3d.agent.tools import look
    s = decision_scene
    capture = look.capture

    def without_worn(*args, **kwargs):
        result = capture(*args, **kwargs)
        result.pop("worn_front_path")
        return result
    monkeypatch.setattr(look, "capture", without_worn)
    assert record(invoke(s, "compare_look_to_photos", {}, cid="partial-look"))["status"] == "ok"
    out = record(invoke(s, "record_difference", entry(evidence_ref="partial-look"), cid="item"))
    assert out["status"] == "refused"
    assert "partial-look cannot support differences: The same-export worn front view is missing." in out["message"]
    assert "unknown evidence_ref" not in out["message"]
