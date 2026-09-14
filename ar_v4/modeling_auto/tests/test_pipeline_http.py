"""Pipeline selection is a creation-only choice, with no implicit start."""
import json

import httpx
import pytest

from app.server import create_app
from app.workflows import LEGACY_STAGES, STANDARD_STAGES
from test_controller import harness, photos


def multipart(pipeline):
    fields = [('name', (None, 'Synthetic selection')), ('notes', (None, 'Keep green')),
              ('dimensions', (None, json.dumps({'frame_width': 140, 'lens_width': 50, 'lens_height': 40})))]
    if pipeline is not None:
        fields.append(('pipeline', (None, pipeline)))
    fields.extend((a, (a + '.jpg', v, 'image/jpeg')) for a, v in photos().items())
    return fields


@pytest.mark.asyncio
@pytest.mark.parametrize('pipeline,expected', [
    (None, 'standard'), ('standard', 'standard'), ('legacy', 'legacy'),
    # The names the plans first shipped under remain accepted from older pages.
    ('test', 'standard'), ('current', 'legacy'),
])
async def test_http_selection_creates_correct_immutable_draft_without_dispatch(harness, pipeline, expected):
    c = harness
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url='http://127.0.0.1:8060') as http:
        response = await http.post('/api/jobs', files=multipart(pipeline))
        assert response.status_code == 200, response.text
        job = response.json()
        assert job['pipeline'] == expected
        assert job['pipeline_stages'] == list(STANDARD_STAGES if expected == 'standard' else LEGACY_STAGES)
        assert job['status'] == 'draft' and job['calls'] == {'meshy': 0, 'astra': 0}
        changed = await http.post('/api/jobs/' + job['id'] + '/pipeline', json={'pipeline': 'legacy', 'version': job['version']})
        assert changed.status_code == 404
        assert (await http.get('/api/jobs/' + job['id'])).json() == job
        assert not c.meshy.submits and not c.astra.calls


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['unknown', 'duplicate', 'file'])
async def test_invalid_mode_is_rejected_without_store_or_provider_mutation(harness, case):
    c = harness
    fields = multipart('unknown' if case == 'unknown' else 'standard')
    if case == 'duplicate':
        fields.append(('pipeline', (None, 'legacy')))
    elif case == 'file':
        fields = [(key, ('mode.txt', b'standard', 'text/plain') if key == 'pipeline' else value) for key, value in fields]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(c)), base_url='http://127.0.0.1:8060') as http:
        response = await http.post('/api/jobs', files=fields)
        assert response.status_code == 400, response.text
    assert not c.store.all() and not c.meshy.submits and not c.astra.calls
