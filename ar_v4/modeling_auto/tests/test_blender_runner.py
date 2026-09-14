import asyncio
from pathlib import Path
import uuid
import json
import struct

import pytest

from app.blender_runner import BlenderRunner, BlenderError, ROOT, contained, validate_embedded_glb


def folder():
    value=ROOT/'data'/'selftest'/('runner-unit-'+uuid.uuid4().hex)
    value.mkdir(parents=True)
    return value


def test_paths_cannot_escape_new_workspace():
    with pytest.raises(BlenderError,match='inside modeling_auto'):
        contained(ROOT.parent/'outside.glb')
    assert contained(ROOT/'data'/'file.glb').is_relative_to(ROOT)


def test_invalid_action_rejected_before_process():
    with pytest.raises(BlenderError,match='Unknown'):
        asyncio.run(BlenderRunner().run('unknown',ROOT/'missing',ROOT/'out'))


def test_existing_revision_never_overwritten():
    root=folder(); source=root/'source.glb'; source.write_bytes(b'input')
    output=root/'revision'; output.mkdir(); (output/'master.blend').write_bytes(b'keep')
    with pytest.raises(BlenderError,match='overwrite'):
        asyncio.run(BlenderRunner().run('import',source,output))
    assert source.read_bytes()==b'input'
    assert (output/'master.blend').read_bytes()==b'keep'


def test_pre_cancel_starts_no_process():
    root=folder(); source=root/'source.glb'; source.write_bytes(b'input')
    cancel=asyncio.Event(); cancel.set()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(BlenderRunner().run('import',source,root/'out',cancel=cancel))
    assert not (root/'out'/'request.json').exists()


def test_script_validation_occurs_before_blender():
    root=folder(); source=root/'source.blend'; source.write_bytes(b'input')
    with pytest.raises(ValueError,match='unavailable'):
        asyncio.run(BlenderRunner().run('edit',source,root/'out',script='import os',stage='finish'))
    assert not (root/'out'/'request.json').exists()


def test_output_cannot_contain_input():
    root=folder(); source=root/'source.glb'; source.write_bytes(b'input')
    with pytest.raises(BlenderError,match='separate'):
        asyncio.run(BlenderRunner().run('import',source,root))


def glb_bytes(document):
    chunk=json.dumps(document).encode()
    chunk += b' ' * ((-len(chunk)) % 4)
    return struct.pack('<4sII',b'glTF',2,len(chunk)+20)+struct.pack('<I4s',len(chunk),b'JSON')+chunk


@pytest.mark.parametrize('uri',['../../private.png','C:/private.png','https://example.com/asset.png','file:///private.png','\\\\host\\share\\image.png'])
@pytest.mark.parametrize('group',['images','buffers'])
def test_glb_cannot_read_external_assets(uri,group):
    path=folder()/'external.glb'; path.write_bytes(glb_bytes({'asset':{'version':'2.0'},group:[{'uri':uri}]}))
    with pytest.raises(BlenderError,match='embed all'):
        validate_embedded_glb(path)


def test_embedded_data_uri_and_buffer_view_allowed():
    path=folder()/'embedded.glb'; path.write_bytes(glb_bytes({'asset':{'version':'2.0'},'images':[{'uri':'data:image/png;base64,AAAA'},{'bufferView':0}]}))
    validate_embedded_glb(path)


def test_glb_invalid_chunk_size_rejected():
    path=folder()/'broken.glb'; value=bytearray(glb_bytes({'asset':{'version':'2.0'}})); struct.pack_into('<I',value,12,0x7ffffffc); path.write_bytes(value)
    with pytest.raises(BlenderError,match='bounds'):
        validate_embedded_glb(path)
