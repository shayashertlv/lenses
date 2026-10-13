"""Temporary render scenes preserve all authored units on success and failed construction."""
import json
from pathlib import Path

import pytest

from glasses3d.g3d_host import blender

pytestmark = pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")


def test_temporary_render_units_never_mutate_user_scenes(tmp_path):
    script = Path(__file__).parent / "blender/render_unit_isolation.py"
    result = blender.run(None, script, [tmp_path], timeout_s=60)
    report_path = tmp_path / "report.json"
    assert report_path.is_file(), result.stdout + result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True and len(report["cases"]) == 8
    for case in report["cases"]:
        assert case["ok"] is True and case["before"] == case["after"]
        assert case["fingerprint_before"] == case["fingerprint_after"]
        assert all(len(values) >= 8 for values in case["before"].values())
