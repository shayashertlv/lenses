"""Missing hinge intersections are explicit unavailable diagnostics, never nonfinite RPC values."""
import json
from pathlib import Path

import pytest
from glasses3d.g3d_host import blender

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender is not installed")


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    receipt = tmp_path_factory.mktemp("hinge-report") / "report.json"
    proc = blender.run(None, Path(__file__).parent / "blender/temple_hinge_report.py", [receipt], timeout_s=120)
    assert receipt.is_file() and proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-6000:]
    return json.loads(receipt.read_text(encoding="utf-8"))


@pytest.mark.parametrize("side", ["1", "-1"])
def test_measurable_gap_preserves_signed_distance(report, side):
    assert report["sides"][side]["measured"] == pytest.approx(.5)
    leaf = report["sides"][side]["report"]["leaves"][0]
    assert leaf["gap_mm"] == pytest.approx(.5)
    assert "gap_unavailable" not in leaf


@pytest.mark.parametrize("side", ["1", "-1"])
@pytest.mark.parametrize("kind", ["miss", "outward"])
def test_no_inner_face_hit_is_null_in_strict_rpc(report, side, kind):
    assert report["sides"][side][kind] is None
    leaf = report["sides"][side]["report"]["leaves"][0]
    assert leaf["old_gap_mm"] is None
    assert leaf["old_gap_unavailable"] == "no inward-facing x-ray intersection"


def test_missing_current_gap_is_reported_without_changing_scene(report):
    planned = report["planned"]
    assert planned["status"] == "planned" and planned["unchanged"] is True
    leaf = planned["side"]["hinge"]["leaves"][0]
    assert leaf["gap_mm"] is None and leaf["old_gap_mm"] is None
    assert leaf["gap_unavailable"] == leaf["old_gap_unavailable"] == "no inward-facing x-ray intersection"
    warning = next(w for w in planned["side"]["warnings"] if w["code"] == "W_HINGE_LEAF")
    assert warning["gap_mm"] is None and "not measurable" in warning["message"]
