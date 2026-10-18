"""Fixed owner-accepted evidence and the explicit migration used by the full replay proof."""
import json
from pathlib import Path

import pytest

from glasses3d.g3d.printuv import glbmesh as GM
from glasses3d.tests.fixtures import legacy_ft1119 as FT


def test_owner_accepted_ft_print_remains_exact():
    for folder in ('quilt_joins_20261008/slope/bake', 'slope_default_20261008/bake'):
        path = FT.REPO / 'glasses3d/data/wp5' / folder / 'print.png'
        if not path.is_file():
            import os
            if os.environ.get('G3D_REQUIRE_DATA') == '1':
                pytest.fail(f'local-only accepted FT1119 evidence absent: {path}')
            pytest.skip(f'local-only accepted FT1119 evidence absent: {path}')
        assert FT.sha(path) == FT.APPROVED_PRINT_SHA


@pytest.mark.slow
def test_production_replays_owner_accepted_ft1119_bytes(tmp_path):
    import os
    from glasses3d.agent.tools import fit_workers, single_threaded_blas
    from glasses3d.tests.photoprint.conftest import require_ft1119
    from glasses3d.tests.proofs.ft1119_print_pin import run
    require_ft1119(pin=True)
    workers = fit_workers(2 if os.environ.get('PYTEST_XDIST_WORKER') else None)
    with single_threaded_blas():
        record = run(tmp_path / 'accepted-replay', workers=workers)
    assert record['print_byte_identical_to_approved']
    assert record['audit']['pass'] and record['source_hashes_unchanged']
    assert all(same for name, same in record['unchanged_outputs'].items() if name != 'registration.json')
    # The pre-task generality cleanup removed this product-specific prose from runtime reports. Every
    # registration datum must still equal the historical receipt; migrate only that exact description suffix.
    accepted = FT.REPO / 'glasses3d/data/wp5/quilt_joins_20261008/slope/bake/registration.json'
    expected = json.loads(accepted.read_text(encoding='utf-8'))
    expected['mode'] = expected['mode'].removesuffix(' (none on FT1119)')
    actual = json.loads((tmp_path / 'accepted-replay/bake/registration.json').read_text(encoding='utf-8'))
    assert actual == expected
    assert record.get('gain_field_identical', True)


def test_legacy_fixture_migration_changes_only_explicit_node_names(ft1119_fixture):
    fixture = ft1119_fixture
    record = fixture['receipt']
    original = record['model']['source']
    source, binary = GM.chunks(Path(original).read_bytes())
    copied, new_binary = GM.chunks(fixture['model'].read_bytes())
    assert binary == new_binary
    assert record['model']['source_sha256'] == FT.MODEL_SHA
    for old, new in zip(source['nodes'], copied['nodes']):
        expected = dict(old)
        if old.get('name') in FT.NAME_MAP:
            expected['name'] = FT.NAME_MAP[old['name']]
        assert new == expected
    source['nodes'] = copied['nodes']
    assert copied == source
    assert all(FT.sha(path) == digest for path, digest in record['sources'].items())
    cameras = json.loads(fixture['cameras'].read_bytes())
    report = json.loads(fixture['step0'].read_bytes())
    assert cameras['model']['sha256'] == report['inputs']['glb_sha256'] == FT.sha(fixture['model'])
    assert report['inputs']['cameras_sha256'] == FT.sha(fixture['cameras'])
