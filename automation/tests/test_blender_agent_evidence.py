"""Recovery is independent of the model remembering to save; no paid requests."""
import ast
import asyncio
import json
from pathlib import Path
import shutil
import subprocess

import pytest

pytest.importorskip('agents')
from mcp.types import CallToolResult, TextContent
from blender_agent.evidence import EvidenceJournal, EvidenceMCPServer, STATE_MARKER, snapshot_code, state_delta


def response(text, error=False):
    return CallToolResult(content=[TextContent(type='text', text=text)], isError=error)


class Upstream:
    def __init__(self, failure=None):
        self.calls, self.failure, self.probes, self.value = [], failure, 0, 0
        self.result = response('Code executed successfully: original result')

    async def __call__(self, name, arguments, meta=None):
        code = arguments.get('code', '')
        if 'def evidence_snapshot()' not in code:
            self.calls.append(('edit', arguments, meta))
            self.value += 1
            if self.failure == 'edit':
                raise RuntimeError('original failure after partial edit')
            return self.result
        self.probes += 1
        self.calls.append(('probe', self.probes))
        if self.failure == 'post_cancel' and self.probes == 2:
            raise asyncio.CancelledError('cancel during evidence')
        tree = ast.parse(code)
        save = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == 'save_as_mainfile')
        keywords = {item.arg: ast.literal_eval(item.value) for item in save.keywords}
        assert keywords['copy'] is True
        destination = Path(keywords['filepath'])
        if self.failure == 'pre' and self.probes == 1 or self.failure == 'post' and self.probes == 2:
            return response('Error executing code: simulated checkpoint failure')
        destination.write_bytes(b'BLENDER-v500-offline-fixture')
        state = {'checkpoint': {'path': str(destination), 'identity_preserved': True},
                 'objects': [{'name': 'lens', 'positions': self.value}], 'materials': [], 'node_groups': [],
                 'scene': {'filepath': 'original-working.blend'}}
        return response('Code executed successfully: ' + STATE_MARKER + json.dumps(state))


def journal_record(path):
    directory = next(path.iterdir())
    return directory, json.loads((directory / 'journal.json').read_text())


def test_safe_mode_and_active_file_identity(tmp_path):
    validator = pytest.importorskip('blender_mcp.safe_mode')
    code = snapshot_code(tmp_path / "copy's.blend")
    validator.validate_code(code)
    assert 'copy=True' in code and 'compress=True' in code
    assert 'bpy.data.filepath == original_path' in code


def test_original_script_is_protected_and_result_unchanged(tmp_path):
    upstream = Upstream()
    args = {'code': 'bpy.context.object.location.x += 1\n', 'user_prompt': 'The exact user wording'}
    journal = EvidenceJournal(tmp_path, lambda: {'tool_call_id': 'call_123', 'interaction': 4})
    result = asyncio.run(journal.call(upstream, 'execute_blender_code', args, {'meta': 'unchanged'}))
    assert result is upstream.result
    assert [row[0] for row in upstream.calls] == ['probe', 'edit', 'probe']
    assert upstream.calls[1][1] is args and upstream.calls[1][2] == {'meta': 'unchanged'}
    directory, record = journal_record(tmp_path)
    assert (directory / 'issued-code.py').read_text() == args['code']
    assert json.loads((directory / 'arguments.json').read_text()) == args
    assert record['context'] == {'tool_call_id': 'call_123', 'interaction': 4}
    assert record['status'] == 'returned' and record['post_evidence'] == 'saved'
    before = json.loads((directory / 'before.json').read_text())
    assert len(before['checkpoint']['sha256']) == 64
    delta = json.loads((directory / 'delta.json').read_text())
    assert delta['objects']['changed'] == {'lens': ['positions']}


def test_missing_pre_checkpoint_blocks_original_mutation(tmp_path):
    upstream = Upstream('pre')
    with pytest.raises(RuntimeError, match='verified checkpoint'):
        asyncio.run(EvidenceJournal(tmp_path).call(upstream, 'execute_blender_code', {'code': 'never executed'}))
    assert [row[0] for row in upstream.calls] == ['probe']
    _, record = journal_record(tmp_path)
    assert record['status'] == 'blocked_before_execution' and record['dispatched'] is False


def test_partial_edit_exception_survives_and_post_scene_is_saved(tmp_path):
    upstream = Upstream('edit')
    with pytest.raises(RuntimeError, match='original failure after partial edit'):
        asyncio.run(EvidenceJournal(tmp_path).call(upstream, 'execute_blender_code', {'code': 'partial edit'}))
    directory, record = journal_record(tmp_path)
    assert record['status'] == 'execution_exception' and record['post_evidence'] == 'saved'
    assert json.loads((directory / 'after.json').read_text())['objects'][0]['positions'] == 1


def test_post_evidence_failure_does_not_replay_or_replace_result(tmp_path):
    upstream = Upstream('post')
    result = asyncio.run(EvidenceJournal(tmp_path).call(upstream, 'execute_blender_code', {'code': 'edit'}))
    assert result is upstream.result
    assert sum(row[0] == 'edit' for row in upstream.calls) == 1
    _, record = journal_record(tmp_path)
    assert record['post_evidence'].startswith('unavailable:')


def test_user_cancellation_during_post_evidence_is_not_swallowed(tmp_path):
    upstream = Upstream('post_cancel')
    with pytest.raises(asyncio.CancelledError, match='cancel during evidence'):
        asyncio.run(EvidenceJournal(tmp_path).call(upstream, 'execute_blender_code', {'code': 'edit'}))
    _, record = journal_record(tmp_path)
    assert record['post_evidence'].startswith('unavailable: CancelledError')
    assert sum(row[0] == 'edit' for row in upstream.calls) == 1


def test_server_constructor_and_context_api_do_not_connect(tmp_path):
    server = EvidenceMCPServer(params={'command': 'never launched'}, evidence_directory=tmp_path)
    server.bind_context(tool_call_id='call_sdk', response_index=12, parent_tool='preview_ar')
    assert server.evidence.context_callback() == {'tool_call_id': 'call_sdk', 'response_index': 12, 'parent_tool': 'preview_ar'}
    server.bind_context()
    assert server.evidence.context_callback()['tool_call_id'] is None


def test_read_only_tools_bypass_checkpoints_and_native_export_is_protected(tmp_path):
    upstream = Upstream()
    journal = EvidenceJournal(tmp_path)
    assert asyncio.run(journal.call(upstream, 'get_scene_info', {})) is upstream.result
    assert not list(tmp_path.iterdir())
    asyncio.run(journal.call(upstream, 'export_scene', {'filepath': 'preview.glb'}))
    assert upstream.probes == 2


def test_delta_reports_material_world_and_object_identity_changes():
    before = {'objects': [{'name': 'old'}], 'materials': [{'name': 'Glass', 'nodes': '1'}], 'scene': {'world_nodes': '1'}}
    after = {'objects': [{'name': 'new'}], 'materials': [{'name': 'Glass', 'nodes': '2'}], 'scene': {'world_nodes': '2'}}
    delta = state_delta(before, after)
    assert delta['objects']['added'] == ['new'] and delta['objects']['removed'] == ['old']
    assert delta['materials']['changed'] == {'Glass': ['nodes']}
    assert delta['scene_changed_fields'] == ['world_nodes']


@pytest.mark.slow
def test_real_blender_probe_is_read_only_and_detects_edit(tmp_path):
    blender = shutil.which('blender') or r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
    if not Path(blender).is_file():
        pytest.skip('Host Blender unavailable')
    original = tmp_path / 'working.blend'
    script = tmp_path / 'fixture.py'
    fixture = '''
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2)
obj = bpy.context.object
obj.name = 'Closed lens'
obj['partRole'] = 'lens'
material = bpy.data.materials.new('Physical glass')
material.use_nodes = True
obj.data.materials.append(material)
mod = obj.modifiers.new('Bevel', 'BEVEL')
mod.width = .1
mod.segments = 2
bpy.ops.wm.save_as_mainfile(filepath=ORIGINAL)
image = bpy.data.images.new('Relative external image identity', width=2, height=2)
image.filepath = '//original-relative-asset.png'
'''.replace('ORIGINAL', repr(str(original)))
    fixture += snapshot_code(tmp_path / 'before.blend')
    fixture += "\nassert image.filepath == '//original-relative-asset.png'\n"
    fixture += "\nobj.data.vertices[0].co.z += .25\nmaterial.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value = .173\n"
    fixture += snapshot_code(tmp_path / 'after.blend')
    fixture += "\nassert bpy.data.filepath == " + repr(str(original)) + "\n"
    script.write_text(fixture, encoding='utf-8')
    run = subprocess.run([blender, '--background', '--factory-startup', '--python-exit-code', '1', '--python', str(script)],
                         capture_output=True, text=True, timeout=90)
    assert run.returncode == 0, run.stdout + run.stderr
    records = [json.loads(line.split(STATE_MARKER, 1)[1]) for line in run.stdout.splitlines() if STATE_MARKER in line]
    assert len(records) == 2
    before, after = records
    delta = state_delta(before, after)
    assert before['checkpoint']['identity_preserved'] and after['checkpoint']['identity_preserved']
    assert 'source_mesh' in delta['objects']['changed']['Closed lens']
    assert 'evaluated_mesh' in delta['objects']['changed']['Closed lens']
    assert delta['materials']['changed'] == {'Physical glass': ['nodes']}
    assert before['objects'][0]['source_mesh']['vertices'] == 8
    assert before['objects'][0]['evaluated_mesh']['vertices'] > 8
    assert (tmp_path / 'before.blend').stat().st_size > 1000 and (tmp_path / 'after.blend').stat().st_size > 1000
