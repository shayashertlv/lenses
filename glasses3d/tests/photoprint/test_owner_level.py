"""An owner-selected level must survive the fitted recommendation and refuse stale selection records."""
import copy
import json

import pytest

from glasses3d.tests.photoprint import ar_calibrate as AC, bake_ft1119 as BA


def document():
    level = {"gain": 0.83, "gamma": 1.17, "sat": 0.996, "chroma_power": 0.455,
             "chroma_hold": True, "chroma_hue": [20.0, 100.0]}
    return {"level": {"gain": 0.93, "gamma": 1.08}, "candidates": {"near_18deg": {"level": level}},
            "production_selection": {"candidate": "near_18deg", "level": copy.deepcopy(level),
                                     "reason": "owner chose near_18deg 2026-10-07",
                                     "owner_words": "which option is best - b"}}


def test_bake_uses_owner_b_while_retaining_the_original_fit(tmp_path):
    path = tmp_path / "calibration.json"
    data = document()
    path.write_text(json.dumps(data))
    before = path.read_bytes()
    level, source = BA._level(path)
    assert level == data["candidates"]["near_18deg"]["level"]
    assert level != data["level"]
    assert source.startswith("owner chose near_18deg 2026-10-07;") and "sha256" in source
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "changed", "unbound", "reason", "owner_words", "malformed"])
def test_stale_or_incomplete_owner_choices_never_fall_back_to_the_fit(tmp_path, damage):
    path = tmp_path / "calibration.json"
    data = document()
    if damage == "missing":
        data["candidates"].clear()
    elif damage == "changed":
        data["candidates"]["near_18deg"]["level"]["gain"] = 0.99
    elif damage == "unbound":
        del data["production_selection"]["level"]
    elif damage == "malformed":
        data["production_selection"] = None
    else:
        data["production_selection"][damage] = ""
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        BA._level(path)


def test_calibration_without_an_owner_choice_still_provides_its_fit(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps({"level": {"gain": 0.93, "gamma": 1.08}}))
    assert BA._level(path)[0] == {"gain": 0.93, "gamma": 1.08}
    assert BA._level(None) == (None, None)


def test_recalibration_cannot_overwrite_an_owner_choice(tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(document()))
    before = path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("capture or fitting must not start")
    monkeypatch.setattr(AC, "calibrate", forbidden)
    with pytest.raises(ValueError, match="new --out"):
        AC.main(["--bake", str(tmp_path), "--glb", "unused.glb", "--out", str(tmp_path)])
    assert path.read_bytes() == before
