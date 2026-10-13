"""The brief preserves owner evidence and the owner's model requirement table."""
from pathlib import Path

from glasses3d.agent.brief import MODEL_REQUIREMENTS, build_brief, host_text


def job():
    return {"name": "Owner model", "description": "  Actual owner words.\r\nKeep spacing.  ",
            "specs_json": '{\r\n "width_mm":145, "custom":"自由"\r\n}',
            "images": [{"file": "images/front.png", "view_kind": "front", "side": "both",
                        "size_px": [640, 480], "sha256": "a" * 64},
                       {"file": "images/side.png", "view_kind": "side", "side": "left",
                        "size_px": [480, 640], "sha256": "b" * 64}]}


def test_description_specs_and_every_photo_reach_brief_verbatim():
    data = job()
    text = build_brief(data)
    assert data["description"] in text and data["specs_json"] in text
    assert "Every reference image below is an authoritative photo" in text
    for row in data["images"]:
        assert row["file"] in text and row["sha256"] in text
        assert f"view '{row['view_kind']}', side '{row['side']}'" in text
    assert "Photos determine shape and colour" in text


def test_owner_model_requirements_are_word_for_word_from_plan():
    plan = (Path(__file__).resolve().parents[2] / "plan/PLAN.md").read_text(encoding="utf-8")
    table = "\n".join(line.strip() for line in plan.splitlines() if line.startswith("  |"))
    assert MODEL_REQUIREMENTS == table
    assert MODEL_REQUIREMENTS in build_brief(job())


def test_contract_checks_host_prose_without_censoring_verbatim_owner_evidence():
    from glasses3d.agent.loop import REMOVED_PHRASES, ToolContract, contract_problems

    data = job()
    data.update(name="Hypothesis", description="Keep supplementary images; these words are unverified.",
                specs_json='{"label":"hypothesis"}')
    data["images"][0]["file"] = "images/supplementary.png"
    text, host = build_brief(data), host_text(data)
    for field in ("name", "description", "specs_json"):
        assert data[field] in text and data[field] not in host
    assert data["images"][0]["file"] in text and data["images"][0]["file"] not in host
    assert MODEL_REQUIREMENTS in host
    contract = ToolContract(frozenset(), removed_phrases=REMOVED_PHRASES)
    assert contract_problems(contract, instructions="", brief=host, tools=[]) == []
    assert contract_problems(contract, instructions="", brief=text, tools=[])
    assert contract_problems(contract, instructions="supplementary", brief=host, tools=[])
