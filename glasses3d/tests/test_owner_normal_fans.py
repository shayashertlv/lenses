"""Native planned normal-fan and topology-remap regression."""
from pathlib import Path
import pytest
from glasses3d.g3d_host import blender


def test_native_cleanup_remaps_required_sharp_fans_only_once():
    if blender.blender_exe() is None:pytest.skip('Blender is not installed')
    result=blender.run(None,Path(__file__).parent/'blender/owner_normal_fans.py',[],timeout_s=120)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'OWNER_NORMAL_FANS_PASSED' in result.stdout,result.stdout+result.stderr
