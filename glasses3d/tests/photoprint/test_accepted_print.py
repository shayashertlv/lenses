"""Fixed owner-accepted evidence and the explicit migration used by the full replay proof."""
import json
from pathlib import Path

from glasses3d.g3d.printuv import glbmesh as GM
from glasses3d.tests.fixtures import legacy_ft1119 as FT


def test_owner_accepted_ft_print_remains_exact():
    for folder in ('quilt_joins_20261008/slope/bake', 'slope_default_20261008/bake'):
        assert FT.sha(FT.REPO / 'glasses3d/data/wp5' / folder / 'print.png') == FT.APPROVED_PRINT_SHA


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
