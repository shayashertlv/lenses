"""Wire-level contracts for the separately selected test workflow; no network."""
import asyncio
import base64
import hashlib
import json

import httpx
import pytest
from PIL import Image

from app.prompts import image_labels, stage_context, stage_instructions
from app.providers import AstraClient, ProviderError
from test_providers import astra_response


@pytest.fixture
def images(tmp_path):
    paths = []
    for i in range(12):
        path = tmp_path / f'image-{i}.png'
        Image.new('RGB', (12, 12), (i * 20, 80, 40)).save(path)
        paths.append(path)
    return paths


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,count', [('lenses', 5), ('connections', 6), ('finish', 11), ('finish_refine', 11)])
async def test_test_pipeline_wire_preserves_scope_and_exact_image_order(stage, count, images, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=astra_response())
    context = {'pipeline': 'standard', 'name': 'Synthetic glasses', 'notes': 'Keep green frame',
               'edit_instructions': 'Make the lenses less reflective', 'model_sha256': 'a' * 64,
               'inspection': {'objects': []}, 'history': 'SECRET_HISTORY', 'api_key': 'SECRET_KEY'}
    receipt = tmp_path / 'receipt'
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AstraClient('local-fake-key', http)
        await client.edit(stage, images[:count], context, receipt, asyncio.Event())
        with pytest.raises(ProviderError, match='resubmission'):
            await client.edit(stage, images[:count], context, receipt, asyncio.Event())
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload['model'] == 'gpt-6-astra' and payload['reasoning'] == {'effort': 'high'}
    assert payload['tool_choice'] == {'type': 'custom', 'name': 'run_blender_python'}
    assert len(payload['tools']) == 1 and payload['parallel_tool_calls'] is False
    assert payload['store'] is False and payload['max_output_tokens'] == 32_000
    assert not {'previous_response_id', 'conversation', 'temperature', 'top_p'} & payload.keys()
    assert 'SECRET' not in json.dumps(payload)
    content = payload['input'][0]['content']
    assert 'Make the lenses less reflective' in content[0]['text']
    assert 'a' * 64 in content[0]['text']
    labels = image_labels(stage, pipeline='standard')
    assert [item['text'] for item in content[1::2]] == list(labels)
    wire_images = content[2::2]
    assert len(wire_images) == count
    for item, path in zip(wire_images, images):
        assert item['detail'] == 'high'
        assert base64.b64decode(item['image_url'].split(',', 1)[1]) == path.read_bytes()
    saved = json.loads((receipt / 'astra_request.json').read_text())
    assert saved['pipeline'] == 'standard' and saved['stage'] == stage
    assert saved['retry'] is False and saved['standalone'] is True
    assert [entry['sha256'] for entry in saved['images']] == [hashlib.sha256(p.read_bytes()).hexdigest() for p in images[:count]]
    assert 'base64' not in json.dumps(saved) and 'local-fake-key' not in json.dumps(saved)
    if stage in {'finish', 'finish_refine'}:
        assert all(label.startswith('original reference') for label in labels[:5])
        assert all(label.startswith('current') for label in labels[5:])
        assert 'close-up' in labels[-1]
        assert 'Geometry is locked' in payload['instructions']
        assert 'CURRENT textured model' in payload['instructions']


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['finish', 'finish_refine'])
@pytest.mark.parametrize('count', [5, 6, 10, 12])
async def test_missing_or_extra_post_texture_images_fail_before_dispatch(stage, count, images, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        raise AssertionError('No request is authorized with incomplete imagery')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(ProviderError, match='exactly 11 images'):
            await AstraClient('local-fake-key', http).edit(stage, images[:count], {'pipeline': 'standard'}, tmp_path / 'receipt', asyncio.Event())
    assert requests == [] and not (tmp_path / 'receipt').exists()


def test_current_finish_keeps_original_contract_and_extra_stage_requires_test_mode():
    assert len(image_labels('finish')) == 5
    assert stage_instructions('finish') == stage_instructions('finish', pipeline='current')
    assert 'CURRENT textured model' not in stage_instructions('finish')
    for operation in (image_labels, stage_instructions):
        with pytest.raises(ValueError, match='Unknown Astra stage'):
            operation('finish_refine')
        with pytest.raises(ValueError, match='Unknown modeling pipeline'):
            operation('finish', pipeline='typo')
    with pytest.raises(ValueError, match='Unknown modeling pipeline'):
        stage_context('finish', {'pipeline': 'typo'})


@pytest.mark.asyncio
async def test_saved_context_with_original_plan_name_still_builds_the_standard_request(images, tmp_path):
    # Operations saved before the rename carry pipeline='test' in their bound context.
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=astra_response())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await AstraClient('local-fake-key', http).edit('finish_refine', images[:11], {'pipeline': 'test'}, tmp_path / 'receipt', asyncio.Event())
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert len([item for item in payload['input'][0]['content'] if item['type'] == 'input_image']) == 11
    assert json.loads((tmp_path / 'receipt' / 'astra_request.json').read_text())['pipeline'] == 'standard'
