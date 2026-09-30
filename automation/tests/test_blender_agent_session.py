"""Scratch startup checks use an isolated background Blender, without any addon."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from blender_agent.session import build_parser, initialized_checkpoint


@pytest.mark.parametrize('source', [[], ['--empty', '--seed', 'donor.blend']])
def test_source_mode_is_explicit_and_exclusive(source):
    with pytest.raises(SystemExit):
        build_parser().parse_args(['--output', 'new-session', *source])


@pytest.mark.parametrize('source,empty,seed', [
    (['--empty'], True, None),
    (['--seed', 'donor.blend'], False, Path('donor.blend')),
])
def test_source_mode_selection(source, empty, seed):
    args = build_parser().parse_args(['--output', 'new-session', *source])
    assert args.empty is empty
    assert args.seed == seed


def test_readiness_requires_matching_saved_checkpoint(tmp_path):
    working, receipt_path = tmp_path / 'working.blend', tmp_path / 'initialization.json'
    assert initialized_checkpoint(receipt_path, working, 'empty') is None
    data = {'mode': 'empty', 'working_copy': str(working), 'checkpoint_saved': True,
            'initial_counts': {'objects': 0, 'meshes': 0, 'materials': 0}}
    receipt_path.write_text(json.dumps(data), encoding='utf-8')
    assert initialized_checkpoint(receipt_path, working, 'empty') is None
    working.write_bytes(b'fixture checkpoint')
    assert initialized_checkpoint(receipt_path, working, 'empty') == data
    with pytest.raises(RuntimeError, match='does not match'):
        initialized_checkpoint(receipt_path, working, 'seed')
    data['initial_counts']['objects'] = 1
    receipt_path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(RuntimeError, match='retained'):
        initialized_checkpoint(receipt_path, working, 'empty')


@pytest.mark.slow
@pytest.mark.parametrize('mode', ['empty', 'seed'])
def test_real_initial_checkpoint_has_requested_scene_without_addon(tmp_path, mode):
    blender = shutil.which('blender') or r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
    if not Path(blender).is_file():
        pytest.skip('Host Blender is unavailable')
    working, receipt_path = tmp_path / 'working.blend', tmp_path / 'initialization.json'
    verification = tmp_path / 'verification.json'
    bootstrap = Path(__file__).resolve().parents[1] / 'blender_agent' / 'bootstrap.py'
    script = tmp_path / 'initialize_fixture.py'
    script.write_text(f'''
import bpy, json, runpy
from pathlib import Path
initialize = runpy.run_path({str(bootstrap)!r})['initialize_scene']
# Deliberately include orphan donor datablocks: empty must clear these too.
bpy.data.meshes.new('Unlinked donor mesh').use_fake_user = True
bpy.data.materials.new('Unlinked donor material').use_fake_user = True
bpy.data.node_groups.new('Unlinked donor shader', 'ShaderNodeTree').use_fake_user = True
for material in bpy.data.materials:
    material.use_fake_user = True
bpy.context.scene['mdl_bridge_underside'] = [3.0, 4.0, 5.0]
before = {{name: len(getattr(bpy.data, name)) for name in ('objects', 'meshes', 'materials', 'node_groups')}}
assert before['objects'] > 0 and before['meshes'] > 0 and before['materials'] > 0
receipt = initialize({mode!r}, {str(working)!r}, {str(receipt_path)!r}, port=19879)
assert not hasattr(bpy.types, 'blendermcp_server')
assert not hasattr(bpy.types.Scene, 'blendermcp_port')
assert bpy.context.scene['blendermcp_port'] == 19879
assert bpy.data.filepath == {str(working)!r}
bpy.ops.wm.open_mainfile(filepath={str(working)!r})
counts = {{name: len(getattr(bpy.data, name)) for name in receipt['initial_counts']}}
assert counts == receipt['initial_counts']
scene = bpy.context.scene
assert scene['blendermcp_port'] == 19879
if {mode!r} == 'empty':
    assert not any(counts.values()), counts
    assert scene.unit_settings.system == 'METRIC'
    assert abs(scene.unit_settings.scale_length - 0.001) < 1e-9
    assert scene.unit_settings.length_unit == 'MILLIMETERS'
    assert list(scene['mdl_bridge_underside']) == [0, 0, 0]
    assert scene['blender_agent_axes'] == '+Y up; +Z front'
else:
    assert {{name: counts[name] for name in before}} == before
    assert list(scene['mdl_bridge_underside']) == [3, 4, 5]
    assert scene.unit_settings.scale_length == 1.0
Path({str(verification)!r}).write_text(json.dumps({{'mode': {mode!r}, 'counts_after_reopen': counts, 'addon_loaded': False}}))
''', encoding='utf-8')
    env = {k: v for k, v in os.environ.items()
           if not any(x in k.upper() for x in ('API_KEY', 'APIKEY', 'SECRET', 'TOKEN', 'PASSWORD', 'CREDENTIAL', 'ACCESS_KEY', 'PRIVATE_KEY'))}
    result = subprocess.run([str(blender), '--background', '--factory-startup', '--disable-autoexec',
                             '--python-exit-code', '1', '--python', str(script)],
                            capture_output=True, text=True, timeout=90, env=env)
    (tmp_path / 'blender.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    check = json.loads(verification.read_text())
    assert check['addon_loaded'] is False
    receipt = initialized_checkpoint(receipt_path, working, mode)
    assert receipt['initial_counts'] == check['counts_after_reopen']
    if mode == 'empty':
        assert not any(receipt['initial_counts'].values())
