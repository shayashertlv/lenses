"""Blender's intended normal vectors are verified by exact encoded shorts."""
from pathlib import Path

import pytest

from glasses3d.g3d_host import blender


def test_exact_encoding_witness_preserves_source_and_detects_one_changed_short():
    if blender.blender_exe() is None:
        pytest.skip("Blender is not installed")
    result = blender.run(None, Path(__file__).parent / "blender/owner_normals.py", [], timeout_s=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OWNER_NORMAL_ENCODING_PASSED" in result.stdout, result.stdout + result.stderr
