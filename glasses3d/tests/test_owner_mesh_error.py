"""A private option's physical refusal must survive process transport intact."""
from concurrent.futures import ProcessPoolExecutor
import pickle

import pytest

from glasses3d.g3d.owner_mesh import OwnerMeshError


def _refuse_option():
    raise OwnerMeshError('E_OWNER_THICKNESS', 'edited wall crosses the opposite surface',
                         object_id='synthetic-part', minimum_mm=.1, measured_mm=-.2,
                         witness={'triangles': [3, 7]})


def test_owner_refusal_pickle_preserves_code_message_and_physical_evidence():
    try:
        _refuse_option()
    except OwnerMeshError as original:
        restored = pickle.loads(pickle.dumps(original))
        assert restored.result() == original.result()
        assert restored.args == original.args


def test_preview_pool_surfaces_refusal_without_breaking_later_tasks():
    with ProcessPoolExecutor(max_workers=1) as pool:
        with pytest.raises(OwnerMeshError, match='crosses the opposite surface') as caught:
            pool.submit(_refuse_option).result()
        assert caught.value.code == 'E_OWNER_THICKNESS'
        assert caught.value.facts['witness'] == {'triangles': [3, 7]}
        assert pool.submit(abs, -3).result() == 3
