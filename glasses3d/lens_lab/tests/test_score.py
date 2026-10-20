import numpy as np
import pytest

from glasses3d.lens_lab import score


def test_regions_partition_photo_support_without_candidate_mask():
    mask = np.zeros((8, 12), bool)
    mask[1:7, 1:11] = True
    mask[4:, 5:7] = False
    encoded, metadata = score.photo_regions(mask)
    assert np.array_equal(encoded > 0, mask)
    assert set(np.unique(encoded)) == {0, 1, 2, 3}
    assert metadata["boundaries_x"] == pytest.approx([1 + 10 / 3, 1 + 20 / 3])
    assert encoded[2, 2] == 1 and encoded[2, 6] == 2 and encoded[2, 9] == 3


def test_lstar_and_lab_known_black_white_and_grey():
    image = np.array([[[0, 0, 0], [255, 255, 255], [128, 128, 128]]], np.uint8)
    lightness = score.lab(image)[0, :, 0]
    assert lightness == pytest.approx([0, 100, 53.585], abs=.001)
    assert score.lab(image)[0, :, 1:] == pytest.approx(np.zeros((3, 2)), abs=.001)
    mask = np.array([[False, False, True]])
    assert score.colour_stats(image, mask)["median_lightness_Lstar"] == pytest.approx(53.585, abs=.001)


def test_score_detects_spatial_change_hidden_by_whole_lens_median():
    encoded = np.array([[1, 1, 2, 2, 3, 3]], np.uint8)
    purple, green = [175, 90, 195], [85, 170, 110]
    reference = np.array([[purple, purple, green, green, purple, purple]], np.uint8)
    candidate = np.array([[green, green, purple, purple, purple, purple]], np.uint8)
    target, actual = score.measurements(reference, encoded), score.measurements(candidate, encoded)
    assert target["whole_lens"] == actual["whole_lens"]
    errors = score.regional_errors(target, actual)
    assert errors["left"]["delta_E76_of_median_Lab"] > 50
    assert errors["centre"]["delta_E76_of_median_Lab"] > 50
    assert errors["right"]["delta_E76_of_median_Lab"] == 0
    assert score.front_pattern(target)["purple_sides_green_centre_signs"]
    assert not score.front_pattern(actual)["purple_sides_green_centre_signs"]


def test_empty_regions_and_dimension_drift_refuse():
    with pytest.raises(ValueError, match="empty"):
        score.photo_regions(np.zeros((10, 10), bool))
    with pytest.raises(ValueError, match="no pixels"):
        score.colour_stats(np.ones((2, 2, 3), np.uint8), np.zeros((2, 2), bool))
    with pytest.raises(ValueError, match="identical"):
        score.colour_stats(np.ones((3, 2, 3), np.uint8), np.ones((2, 2), bool))


def test_frozen_basis_detects_changed_assets(tmp_path):
    asset = tmp_path / "regions.png"
    asset.write_bytes(b"original")
    score.write_json(tmp_path / "basis.json", {"assets": {"regions.png": score.sha256(asset)}})
    assert score.load_basis(tmp_path)["directory"] == str(tmp_path)
    asset.write_bytes(b"changed")
    with pytest.raises(ValueError, match="asset changed"):
        score.load_basis(tmp_path)


def test_finished_trial_scores_the_bound_export_without_reexport(tmp_path, monkeypatch):
    monkeypatch.setattr(score, "DATA", tmp_path)
    trial = tmp_path / "trial"
    job = trial / "job"
    job.mkdir(parents=True)
    model = job / "finished.glb"
    model.write_bytes(b"immutable finished export")
    score.write_json(trial / "trial.json", {"job_dir": str(job)})
    score.write_json(trial / "finish.json", {"status": "lab_finished", "export": {
        "status": "exported", "path": str(model), "sha256": score.sha256(model)}})
    seen = []
    monkeypatch.setattr(score, "score_glb", lambda glb, output, **kwargs: seen.append((glb, output, kwargs)) or {"ok": True})
    assert score.score_trial(trial) == {"ok": True}
    assert seen[0][0] == model and seen[0][1] == trial / "score"
    assert seen[0][2]["trial_label"].startswith("LAB Codex-as-Astra")
    model.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed bytes"):
        score.score_trial(trial)


@pytest.mark.parametrize("writer", ["prepare_basis", "capture_glb", "score_capture", "score_glb", "score_trial"])
@pytest.mark.parametrize("destination", ["outside", "traversal", "root"])
def test_public_writers_refuse_nonprivate_outputs_before_reads_or_mutation(tmp_path, monkeypatch, writer, destination):
    from glasses3d.lens_lab import harness
    lab = tmp_path / "lab"
    lab.mkdir()
    original = tmp_path / "original"
    original.mkdir()
    sentinel = original / "keep.txt"
    sentinel.write_text("unchanged")
    monkeypatch.setattr(score, "DATA", lab)
    def unexpected(*args, **kwargs):
        pytest.fail("outside output reached input reads or tool execution")
    monkeypatch.setattr(score, "read_json", unexpected)
    monkeypatch.setattr(harness, "ensure_heavy_allowed", unexpected)
    output = {"outside": original / "score", "traversal": lab / ".." / "original" / "score", "root": lab}[destination]
    before = set(tmp_path.rglob("*"))
    with pytest.raises(ValueError, match="private subdirectory"):
        if writer == "prepare_basis":
            score.prepare_basis(output)
        else:
            getattr(score, writer)(lab / "unread-input", output)
    assert set(tmp_path.rglob("*")) == before
    assert sentinel.read_text() == "unchanged"


@pytest.mark.parametrize("finished", [False, True])
@pytest.mark.parametrize("job_location", ["outside", "other-child"])
def test_trial_job_must_be_its_own_copy_before_finish_or_export(tmp_path, monkeypatch, finished, job_location):
    from glasses3d.lens_lab import harness
    lab = tmp_path / "lab"
    trial = lab / "trial"
    trial.mkdir(parents=True)
    monkeypatch.setattr(score, "DATA", lab)
    job = tmp_path / "original-job" if job_location == "outside" else trial / "different-job"
    score.write_json(trial / "trial.json", {"job_dir": str(job)})
    if finished:
        score.write_json(trial / "finish.json", {"status": "lab_finished"})
    monkeypatch.setattr(harness, "call_tool", lambda *args: pytest.fail("invalid job reached exporter"))
    with pytest.raises(ValueError, match="private job directory"):
        score.score_trial(trial)
    assert not (trial / "score").exists()


def test_finished_trial_itself_must_be_below_lab_root(tmp_path, monkeypatch):
    lab = tmp_path / "lab"
    lab.mkdir()
    monkeypatch.setattr(score, "DATA", lab)
    with pytest.raises(ValueError, match="private subdirectory"):
        score.score_trial(tmp_path / "original", lab / "score")
    assert not (lab / "score").exists()


def test_output_and_trial_job_resolve_directory_links_before_writing(tmp_path, monkeypatch):
    import os
    import subprocess
    from glasses3d.lens_lab import harness
    lab = tmp_path / "lab"
    trial = lab / "trial"
    trial.mkdir(parents=True)
    original = tmp_path / "original"
    original.mkdir()
    link = trial / "job"
    if os.name == "nt":
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(original)],
                       capture_output=True, check=True)
    else:
        link.symlink_to(original, target_is_directory=True)
    monkeypatch.setattr(score, "DATA", lab)
    score.write_json(trial / "trial.json", {"job_dir": str(link)})
    score.write_json(trial / "finish.json", {"status": "lab_finished"})
    monkeypatch.setattr(harness, "call_tool", lambda *args: pytest.fail("linked job reached exporter"))
    with pytest.raises(ValueError, match="private subdirectory"):
        score.output_path(link / "new-score")
    with pytest.raises(ValueError, match="private job directory"):
        score.score_trial(trial)
    assert list(original.iterdir()) == []


def test_cli_trial_and_glb_dispatch_without_starting_tools(tmp_path, monkeypatch, capsys):
    import json
    calls = []
    result = {"comparison_image": "comparison.png", "mean_regional_delta_E76": 12.5}
    monkeypatch.setattr(score, "score_trial", lambda *args, **kwargs: calls.append(("trial", args, kwargs)) or result)
    monkeypatch.setattr(score, "score_glb", lambda *args, **kwargs: calls.append(("glb", args, kwargs)) or result)
    assert score.main(["score-trial", str(tmp_path)]) == 0
    assert calls[0][0] == "trial"
    assert json.loads(capsys.readouterr().out)["score_json"] == str(tmp_path / "score/score.json")
    assert score.main(["score-glb", str(tmp_path / "model.glb"), str(tmp_path / "out")]) == 0
    assert calls[1][0] == "glb"
    assert json.loads(capsys.readouterr().out)["mean_regional_delta_E76"] == 12.5
