"""The candidates lab variant adds capability without changing frozen trial scope."""
from types import SimpleNamespace
import json
from pathlib import Path
import pytest

from glasses3d.agent.tools import build_tools
from glasses3d.lens_lab import harness, trial, variants


def test_candidates_variant_alone_exposes_tool_and_baseline_stays_scoped(tmp_path):
    scene = SimpleNamespace(instruction_profile="decision-20261021", job={})
    assert "compare_lens_candidates" in {tool.name for tool in variants.build_variant_tools(scene, "candidates")}
    for variant in ("baseline", "regional", "pre14", "historical-label"):
        assert "compare_lens_candidates" not in harness.allowed_tools(variant)
    assert "compare_lens_candidates" in harness.allowed_tools("candidates")
    paragraph = trial.lab_paragraph(tmp_path, ["Lens"], tmp_path / "python.exe", variant="candidates")
    assert "compare_lens_candidates" in paragraph
    assert "compare_lens_candidates" not in trial.lab_paragraph(tmp_path, ["Lens"], tmp_path / "python.exe")
    assert "compare_lens_candidates" in trial.RENDER_TOOLS


def test_candidate_capture_runner_is_isolated_from_production():
    from glasses3d.agent.tools import lens_candidates, arview
    def runner(*args, **kwargs):
        raise AssertionError("test never renders")
    scene = SimpleNamespace(instruction_profile="decision-20261021", job={})
    registry = variants.build_variant_tools(scene, "candidates", capture_runner=runner)
    assert any(tool.name == "compare_lens_candidates" for tool in registry)
    assert lens_candidates.arview is arview and arview.run_capture is not runner


def test_candidates_score_keeps_measurements_without_white_background_sheet(tmp_path, monkeypatch):
    from glasses3d.lens_lab import score
    root = tmp_path / "trial"
    job = root / "job"
    job.mkdir(parents=True)
    model = job / "model.glb"
    model.write_bytes(b"synthetic")
    (root / "trial.json").write_text(json.dumps({"variant": "candidates", "job_dir": str(job)}))
    (root / "finish.json").write_text(json.dumps({"status": "lab_finished", "export": {
        "status": "exported", "path": str(model), "sha256": score.sha256(model)}}))
    monkeypatch.setattr(score, "output_path", lambda path: Path(path).resolve())
    measured = []
    monkeypatch.setattr(score, "score_glb", lambda *args, **kwargs: measured.append(kwargs) or {"error": 12.3})
    assert score.score_trial(root) == {"error": 12.3}
    assert measured[0]["make_sheet"] is False
    receipt = root / "codex"
    receipt.mkdir()
    output = root / "score"
    output.mkdir()
    result = {"six_region_error": 12.3}
    (output / "score.json").write_text(json.dumps(result))
    monkeypatch.setattr(trial, "_score_trial", lambda _: result)
    checked = trial.score_after_codex(root, receipt)
    assert checked["status"] == "completed" and checked["comparison_sheet_omitted"]
    assert not list(root.rglob("*.png"))


def test_candidates_scoring_refuses_unrequested_sheet(tmp_path, monkeypatch):
    (tmp_path / "trial.json").write_text(json.dumps({"variant": "candidates"}))
    monkeypatch.setattr(trial, "_score_trial", lambda _: {"comparison_image": "white.png"})
    result = trial.score_after_codex(tmp_path, tmp_path)
    assert result["status"] == "failed" and "must not produce" in result["error"]


@pytest.mark.parametrize("sheet", [False, True])
def test_score_cli_reports_measurements_with_or_without_a_sheet(tmp_path, monkeypatch, capsys, sheet):
    from glasses3d.lens_lab import score
    result = {"mean_regional_delta_E76": 12.3}
    if sheet:
        result["comparison_image"] = str(tmp_path / "comparison.png")
    monkeypatch.setattr(score, "score_trial", lambda *args, **kwargs: result)
    assert score.main(["score-trial", str(tmp_path)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown == {"score_json": str((tmp_path / "score/score.json").resolve()), **result}
    assert not list(tmp_path.rglob("*.png"))
