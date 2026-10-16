"""Independent rendering-material scope regressions in actual Blender."""
from pathlib import Path

import pytest

from glasses3d.g3d_host import blender


def test_multiple_material_slots_and_shading_group_and_object_override_refusal():
    if blender.blender_exe() is None:
        pytest.skip("Blender is not installed")
    result = blender.run(None, Path(__file__).parent / "blender/owner_material_review.py", [], timeout_s=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNER_MATERIAL_REVIEW_PASSED" in result.stdout, result.stdout + result.stderr
