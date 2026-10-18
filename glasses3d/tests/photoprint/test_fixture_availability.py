"""Exercise the actual fixtures with unavailable local inputs in a private pytest session."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize('required', [False, True])
def test_absent_local_print_data_has_an_explicit_reason(tmp_path, required):
    source = Path(__file__).with_name('conftest.py').read_text(encoding='utf-8')
    source += '''
def pytest_configure(config):
    from glasses3d.tests.fixtures import legacy_ft1119 as FT
    from pathlib import Path
    FT.CAMERAS = Path(__file__).parent / 'absent-cameras.json'
    FT.STEP0 = Path(__file__).parent / 'absent-step0.json'
'''
    (tmp_path / 'conftest.py').write_text(source, encoding='utf-8')
    (tmp_path / 'test_absent.py').write_text('def test_data(ft1119_job):\n    assert ft1119_job\n', encoding='utf-8')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3]),
               G3D_REQUIRE_DATA='1' if required else '0')
    p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-rs', '-p', 'no:cacheprovider',
                        '--confcutdir', str(tmp_path), str(tmp_path / 'test_absent.py')],
                       env=env, capture_output=True, text=True, encoding='utf-8')
    assert p.returncode == (1 if required else 0), p.stdout + p.stderr
    assert 'local-only FT1119 data absent' in p.stdout
    assert ('1 error' if required else '1 skipped') in p.stdout
