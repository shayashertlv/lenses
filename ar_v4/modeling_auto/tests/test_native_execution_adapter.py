import json
from types import SimpleNamespace

import pytest

from app.blender_runner import timeout_message
from app.script_validation import safe_builtins, validate_script


def test_dynamic_adapter_cannot_bypass_attribute_guards():
    calls = []
    env = safe_builtins(attribute_getter=lambda *args: calls.append(args),
                        attribute_setter=lambda *args: calls.append(args))
    for name in ('__class__', 'as_pointer', 'filepath', 'from_address', 'ops'):
        with pytest.raises(ValueError, match='contract'):
            env['getattr'](SimpleNamespace(), name)
        with pytest.raises(ValueError, match='contract'):
            env['setattr'](SimpleNamespace(), name, 0)
    assert calls == []
    image = SimpleNamespace(pixels=[1])
    env['getattr'](image, 'pixels')
    env['setattr'](image, 'pixels', [2])
    assert calls == [(image, 'pixels'), (image, 'pixels', [2])]
    assert env['hasattr']([], '__len__') is True


@pytest.mark.parametrize('source', [
    'import math as __modeling_auto_pixel_load',
    'from math import sqrt as __modeling_auto_pixel_owner',
    'try:\n    pass\nexcept Exception as __modeling_auto_pixel_load:\n    pass',
    'match image:\n    case __modeling_auto_pixel_load:\n        pass',
    'match images:\n    case [*__modeling_auto_pixel_owner]:\n        pass',
    'match images:\n    case {**__modeling_auto_pixel_owner}:\n        pass',
])
def test_private_binding_aliases_rejected_before_native_execution(source):
    with pytest.raises(ValueError, match='unavailable'):
        validate_script(source)


def test_timeout_reports_recognized_native_phase(tmp_path):
    (tmp_path / 'native_progress.json').write_text(json.dumps({'current': {'phase': 'astra_edit'}}))
    assert timeout_message(tmp_path, 1200) == (
        'Blender exceeded 1200 seconds while executing the saved Astra edit')
    (tmp_path / 'native_progress.json').write_text(json.dumps({'current': {'phase': 'export_glb'}}))
    assert timeout_message(tmp_path, 1200).endswith('while exporting GLB')


@pytest.mark.parametrize('content', ['{', 'null', '[]', '{"current":null}',
                                    '{"current":{"phase":"untrusted free text"}}',
                                    '{"current":{"phase":[]}}', 'x' * 16385])
def test_missing_or_malformed_progress_keeps_original_timeout(tmp_path, content):
    assert timeout_message(tmp_path, 1200) == 'Blender exceeded 1200 seconds'
    (tmp_path / 'native_progress.json').write_text(content)
    assert timeout_message(tmp_path, 1200) == 'Blender exceeded 1200 seconds'
