"""A resumed proof cannot mix old delivered camera fits with a new candidate fitter."""
import json
from types import SimpleNamespace

import pytest

from glasses3d.g3d_host import relief_fit as RF
from glasses3d.tests.proofs import wp6_prada as P


def _cached(tmp_path):
    cache = tmp_path / 'fits_delivered.pkl'
    cache.write_bytes(b'opaque cached data; this test must never unpickle it')
    meta = {'fit_version': RF.FIT_VERSION, 'pickle_sha256': P.sha(cache),
            'camera_basis_sha256': 'camera A', 'source_scene_sha256': 'scene A'}
    (tmp_path / 'fits_delivered_meta.json').write_text(json.dumps(meta))
    return cache, meta


def test_matching_cache_and_absent_cache(tmp_path):
    assert P.delivered_cache_metadata(tmp_path) is None
    _, meta = _cached(tmp_path)
    assert P.delivered_cache_metadata(tmp_path, camera_sha256='camera A', scene_sha256='scene A') == meta


@pytest.mark.parametrize('change', ['version', 'bytes', 'metadata', 'not_object'])
def test_stale_cache_refuses_before_rewriting_old_proof(tmp_path, change):
    cache, meta = _cached(tmp_path)
    if change == 'version':
        meta['fit_version'] = 'fit-4'
        (tmp_path / 'fits_delivered_meta.json').write_text(json.dumps(meta))
    elif change == 'bytes':
        cache.write_bytes(b'changed')
    elif change == 'metadata':
        (tmp_path / 'fits_delivered_meta.json').unlink()
    else:
        (tmp_path / 'fits_delivered_meta.json').write_text('[]')
    proof = tmp_path / 'proof.json'
    proof.write_bytes(b'old proof retained verbatim')
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(ValueError, match='delivered camera cache'):
        P.Proof(tmp_path, SimpleNamespace(resume=True))
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir()}


@pytest.mark.parametrize('kwargs', [{'camera_sha256': 'camera B'}, {'scene_sha256': 'scene B'}])
def test_changed_camera_or_scene_is_not_same_comparator(tmp_path, kwargs):
    _cached(tmp_path)
    with pytest.raises(ValueError, match='provenance mismatch'):
        P.delivered_cache_metadata(tmp_path, **kwargs)
