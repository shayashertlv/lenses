"""Comparison provenance and native output controls. Real core geometry; renderer is isolated here.

The copy proof exercises the actual worker/AR pipeline separately. These tests do not infer geometry from images.
"""
import copy
import json
from pathlib import Path

import pytest
from PIL import Image
from agents.tool import ToolOutputText

from glasses3d.agent.temple_lab import crease_angles as CA, crease_ar as AR, state as S
from glasses3d.agent.temple_lab import geometry as G
from glasses3d.agent.tools import arview
from glasses3d.agent import tool_reports
from glasses3d.tests.agent.test_temple_lab import make_lab
from glasses3d.tests.test_temple_visible_fit import design


@pytest.fixture
def prepared(tmp_path, monkeypatch, request):
    source = design()
    if getattr(request, "param", None) == "two":
        source["nodes"].update({n: [d, y, {"read": {"id": n, "X": 68., "sd": 1.}}]
                                for n, d, y in (("P2", 80., 0.), ("Q2", 110., 0.),
                                                ("D2", 95., -8.), ("U2", 95., 8.))})
        source["facets"].update(lower2=["P2", "D2", "Q2"], upper2=["P2", "Q2", "U2"])
    lab = make_lab(tmp_path, spec=source)
    S.record_object(lab.state, "Two measured flat faces sharing a horizontal edge", "object", spec=lab.design(0), parent=0)

    def binding(obj, parent):
        return {"parent_revision": parent, "design_sha256": G.design_sha256(obj.design(parent)),
                "head_revision": obj.head_record()["revision"], "head_fingerprint": obj.s.head["fingerprint"],
                "readings_sha256": CA.digest(obj.readings()), "assets": "fixed"}

    monkeypatch.setattr(CA, "binding", binding)
    monkeypatch.setattr(lab, "_variant_box", lambda *_: (1, (0, 0, 80, 60)))

    def rows(_lab, photos, directory):
        photo = directory / "photo.png"
        Image.new("RGB", (80, 60), "white").save(photo)
        return [{"photo": "05", "reference": "reference-05", "camera": {"id": "photo-000", "width": 80,
                "height": 60, "meta": {"pixel_scale": 1}}, "photo_path": str(photo),
                "photo_sha256": arview.sha256(photo), "native_pixel_scale": 1}]

    def capture(_lab, export, rows, directory, deadline):
        directory.mkdir(parents=True)
        image = directory / "beauty.png"
        Image.new("RGB", (80, 60), "beige").save(image)
        report = directory / "report.json"
        report.write_text(json.dumps({"spec": {"ok": True}}), encoding="utf8")
        return {"report": str(report), "report_sha256": arview.sha256(report), "spec": {"ok": True},
                "images": [{"photo": "05", "path": str(image), "sha256": arview.sha256(image)}]}

    def exports(tool, method, params, **kw):
        assert method == "temple_variants_export"
        assert lab.s.head["fingerprint"] == lab.state["start_fingerprint"]
        directory = Path(params["out_dir"])
        directory.mkdir(parents=True)
        variants = {}
        for row in params["variants"]:
            path = directory / (row["label"] + ".glb")
            path.write_bytes(json.dumps(row["spec"], sort_keys=True).encode())
            variants[row["label"]] = {"status": "exported", "path": str(path), "sha256": arview.sha256(path)}
        return {"status": "ok", "result": {"status": "ok", "unchanged": True, "variants": variants}}

    monkeypatch.setattr(AR, "photo_rows", rows)
    monkeypatch.setattr(AR, "capture", capture)
    monkeypatch.setattr(AR, "visible_target", lambda *_: 30)
    monkeypatch.setattr(lab.s, "call_read", exports)
    monkeypatch.setattr(CA.look, "verified_report", lambda *a: (None, {}))
    return lab


def comparison(lab):
    return lab.crease_compare("compare", 0, ["lower", "upper"], "lower", "ridge", [6., 12., 16.], ["05"])


def test_real_builder_alternatives_restore_head_and_require_delivered_output(prepared):
    lab = prepared
    fp = lab.s.head["fingerprint"]
    out = comparison(lab)
    assert out["status"] == "ok"
    assert lab.s.head["fingerprint"] == fp
    assert [c["status"] for c in out["alternatives"]] == ["eligible"]*3
    assert [c["built_degrees"] for c in out["alternatives"]] == pytest.approx([6., 12., 16.], abs=1e-5)
    assert out["photos"][0]["visible_pixels_by_variant"] == {"baseline": 30, "angle-00": 30, "angle-01": 30, "angle-02": 30}
    assert out["photos"][0]["sheets"][0]["labels"][0] == "Reference photo"
    assert out["photos"][0]["context_facets"] == ["lower", "upper"]
    effects = out["alternatives"][1]["affected_geometry"]
    assert any(r["region"] == "lower" and not r["unchanged"] for r in effects["surfaces"])
    assert any(r["region"] == "upper" and r["unchanged"] for r in effects["surfaces"])
    with pytest.raises(S.LabRefusal, match="completed comparison"):
        lab.visible_angle("choose-before-output", out["comparison_id"], 12., "The rendered edge is clearer", apply=False)
    lab.mark_delivered("compare", "facet_crease_compare")
    chosen = lab.visible_angle("choose", out["comparison_id"], 12., "The rendered edge is clearer", apply=False)
    assert chosen["status"] == "built"
    selected = lab.design(chosen["revision"])
    assert selected["creases"][0]["visible_angle"]["comparison_sha256"] == out["comparison_sha256"]
    CA.validate_design(lab, selected)  # reapply unchanged choice is allowed
    selected["nodes"]["D"][0] += .1
    with pytest.raises(S.LabRefusal, match="design changed"):
        CA.validate_design(lab, selected)
    lab.state["readings"]["new"] = {"fact": "changed"}
    with pytest.raises(S.LabRefusal, match="changed"):
        lab.visible_angle("stale", out["comparison_id"], 12., "stale", apply=False)


@pytest.mark.parametrize("unseen_first", [False, True])
def test_late_output_cannot_revive_unseen_comparison_and_state_is_durable(prepared, unseen_first):
    lab = prepared
    if unseen_first:
        lab.mark_unseen("compare")
    out = comparison(lab)
    lab.mark_delivered("compare", "facet_crease_compare")
    if not unseen_first:
        lab.mark_unseen("compare")
        persisted = json.loads(lab.state_path.read_text(encoding="utf8"))
        assert persisted["crease_comparisons"][out["comparison_id"]]["unseen"]
        assert not persisted["crease_comparisons"][out["comparison_id"]]["delivered"]
    lab.mark_delivered("compare", "facet_crease_compare")
    state = json.loads(lab.state_path.read_text(encoding="utf8"))
    assert state["crease_comparisons"][out["comparison_id"]]["unseen"]
    assert not state["crease_comparisons"][out["comparison_id"]]["delivered"]
    with pytest.raises(S.LabRefusal, match="completed comparison"):
        CA.evidence(lab, out["comparison_id"])


def test_comparison_means_package_is_no_longer_unused(prepared):
    lab = prepared
    lab.state["objects"] = []
    assert lab.is_clean()
    comparison(lab)
    assert not lab.is_clean()


@pytest.mark.parametrize("tamper", ["manifest", "glb", "sheet", "photo"])
def test_compared_evidence_is_immutable(prepared, tamper):
    lab = prepared
    out = comparison(lab)
    lab.mark_delivered("compare", "facet_crease_compare")
    paths = {"manifest": lab.root / lab.state["crease_comparisons"][out["comparison_id"]]["file"],
             "glb": out["alternatives"][1]["export"]["path"],
             "sheet": out["photos"][0]["sheets"][0]["path"], "photo": out["photos"][0]["photo_path"]}
    with Path(paths[tamper]).open("ab") as file:
        file.write(b"changed")
    with pytest.raises(S.LabRefusal, match="changed"):
        CA.evidence(lab, out["comparison_id"])


def test_candidate_visibility_not_baseline_controls_choice(prepared, monkeypatch):
    lab = prepared
    monkeypatch.setattr(AR, "visible_target", lambda path, *_: 0 if Path(path).stem in {"baseline", "angle-01"} else 17)
    out = comparison(lab)
    assert out["photos"][0]["sheets"]  # requested view stays even when baseline is hidden
    assert out["photos"][0]["visible_pixels_by_variant"]["baseline"] == 0
    lab.mark_delivered("compare", "facet_crease_compare")
    with pytest.raises(S.LabRefusal, match="named face was shown"):
        lab.visible_angle("hidden", out["comparison_id"], 12., "invisible", apply=False)
    assert lab.visible_angle("visible", out["comparison_id"], 6., "visible", apply=False)["status"] == "built"


def test_strict_ar_spec_failure_stays_informational_but_cannot_be_selected(prepared, monkeypatch):
    lab = prepared
    original = AR.capture
    def failed(*args):
        out = original(*args)
        if Path(args[1]["path"]).stem == "angle-01":
            out["spec"] = {"ok": False}
        return out
    monkeypatch.setattr(AR, "capture", failed)
    out = comparison(lab)
    assert out["alternatives"][1]["status"] == "hard_checks_failed"
    assert out["photos"][0]["sheets"]
    lab.mark_delivered("compare", "facet_crease_compare")
    with pytest.raises(S.LabRefusal, match="hard checks passed"):
        lab.visible_angle("invalid", out["comparison_id"], 12., "bad", apply=False)


@pytest.mark.parametrize("prepared", ["two"], indirect=True)
def test_two_real_distinct_angle_choices_share_a_whole_design_anchor(prepared):
    lab = prepared
    first = comparison(lab)
    lab.mark_delivered("compare", "facet_crease_compare")
    chosen1 = lab.visible_angle("choose1", first["comparison_id"], 12., "First visible edge", apply=False)
    assert chosen1["status"] == "built"
    second = lab.crease_compare("compare2", chosen1["revision"], ["lower2", "upper2"], "lower2", "ridge",
                                [6., 12., 16.], ["05"])
    assert second["status"] == "ok"
    lab.mark_delivered("compare2", "facet_crease_compare")
    chosen2 = lab.visible_angle("choose2", second["comparison_id"], 6., "Second visible edge", apply=False)
    assert chosen2["status"] == "built"
    selected = lab.design(chosen2["revision"])
    assert len([c for c in selected["creases"] if c.get("visible_angle")]) == 2
    CA.validate_design(lab, selected)
    changed = copy.deepcopy(selected)
    changed["nodes"]["U2"][0] += .1
    with pytest.raises(S.LabRefusal, match="design changed"):
        CA.validate_design(lab, changed)
    lab.mark_unseen("compare")
    with pytest.raises(S.LabRefusal, match="completed comparison"):
        CA.validate_design(lab, selected)


def test_full_report_limiter_keeps_metrics_for_all_five_actual_candidates(prepared):
    lab = prepared
    result = lab.crease_compare("large-compare", 0, ["lower", "upper"], "lower", "ridge",
                                [3., 6., 9., 12., 16.], ["05"])
    text = tool_reports.dumps(result)
    assert len(text) > tool_reports.TEXT_LIMIT  # actual fitted specs/residuals/exports, not a shortened report proxy
    output = tool_reports.limit_output(lab.root, "large-compare", "facet_crease_compare", [ToolOutputText(text=text)])
    bounded = json.loads(output[0].text)
    assert bounded["paged"]
    assert bounded["summary"] == result["summary"]
    assert len(bounded["summary"]) <= 12
    assert "measurement" in bounded["summary"]["baseline"] and "unresolved" in bounded["summary"]["baseline"]
    for index, candidate in enumerate(result["alternatives"]):
        summary = bounded["summary"][candidate["label"]]
        assert isinstance(summary, str) and len(summary) <= 120
        for term in ("built/requested", candidate["status"], "wall", "localΔχ²", "max|r|", "moved D", "surfaces",
                     f"/alternatives/{index}"):
            assert term in summary
        assert f"{candidate['label']}={candidate['degrees']!r}" in bounded["summary"][f"exact_requested_degrees_{index//3+1}"]
    # The unabridged fit still exists in the saved report with all original values.
    saved = tool_reports.read_report(lab.root, bounded["full_report"]["id"], "/result/alternatives/4/degrees")
    assert saved["entries"][0]["value"] == 16.
