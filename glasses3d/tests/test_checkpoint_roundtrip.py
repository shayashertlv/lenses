"""Save/reload preserves authored content across active, orphan and fake-user Blender IDs."""
import json
from pathlib import Path

import pytest

from glasses3d.g3d_host import blender

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")


def test_mixed_scene_roundtrip_and_authored_changes(tmp_path):
    script = Path(__file__).parent / "blender" / "checkpoint_roundtrip.py"
    result = blender.run(None, script, [tmp_path], timeout_s=180)
    report_path = tmp_path / "report.json"
    assert report_path.is_file(), result.stdout + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("ok") is True, result.stdout + result.stderr
    assert report["restore"]["matches"] is True
    assert report["retained"]["Converted text"]["users"] == 0
    assert all(report["survived"].values())
    assert len(report["mutations_detected"]) >= 55
    assert all(report["mutations_detected"].values())
