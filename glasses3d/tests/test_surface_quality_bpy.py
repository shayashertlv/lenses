"""Named surface measurements and the native Blender collinear-ear regression."""
from pathlib import Path
import json
import subprocess

import numpy as np
import pytest

from glasses3d.g3d.surface_quality_bpy import facet_triangle_map
from glasses3d.g3d.surface_quality import analyse
from glasses3d.g3d.temple import build_temple, fixtures
from glasses3d.g3d_host import blender


def test_named_mapping_measures_current_geometry_after_topology_changes():
    tb = build_temple(fixtures.prada_default())
    v, _, f = tb.world()
    provenance = {'canonical_spec': tb.spec.canonical, 'side': '+x'}
    mapping, report = facet_triangle_map(v, f, provenance)
    measured = analyse(v, f, facets=mapping)['facets']
    assert report['status'] == 'charted'
    assert any(row['triangle_count'] for row in measured.values())
    for row in measured.values():
        if row['status'] == 'measured':
            assert row['max_mm'] < 1e-8
    # A later nonlinear edit leaves the named footprint but destroys planarity.
    changed = v.copy()
    changed[:, 0] += .002*(v[:, 2]+30)**2
    new_map, _ = facet_triangle_map(changed, f[::-1], provenance)
    new = analyse(changed, f[::-1], facets=new_map)['facets']
    assert max(row.get('max_mm', 0) for row in new.values()) > .05


def test_named_mapping_mirrored_side_and_no_geometry_write():
    tb = build_temple(fixtures.prada_default())
    left, _, lf = tb.world(1)
    right, _, rf = tb.world(-1)
    before = right.copy()
    a, _ = facet_triangle_map(left, lf, {'canonical_spec': tb.spec.canonical, 'side': '+x'})
    b, _ = facet_triangle_map(right, rf, {'canonical_spec': tb.spec.canonical, 'side': '-x'})
    assert a == b
    np.testing.assert_array_equal(before, right)


def test_named_mapping_rejects_invalid_provenance():
    with pytest.raises((ValueError, KeyError)):
        facet_triangle_map(np.zeros((3, 3)), np.array([[0, 1, 2]]), {'canonical_spec': {}})


@pytest.mark.skipif(blender.blender_exe() is None, reason='Blender is not installed')
def test_native_collinear_ears_preserve_geometry_normals_uv_and_boundary(tmp_path):
    script = Path(__file__).parent/'proofs'/'surface_quality_bpy_run.py'
    target = tmp_path/'result.json'
    proc = subprocess.run([str(blender.blender_exe()), '-b', '--factory-startup', '--python', str(script), '--', str(target)],
                          capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, proc.stdout+proc.stderr
    report = json.loads(target.read_text())
    assert report['repaired_polygons'] == 1
    assert report['before_degenerate'] >= 1
    assert report['after_degenerate'] == 0
    assert report['positions_unchanged']
    assert report['boundary_unchanged']
    assert report['uv_max_error'] < 1e-7
    # Blender re-encodes custom loop normals relative to each new triangle;
    # verify direction rather than requiring identical encoded components.
    assert report['normal_max_angle_deg'] < .05
    assert report['second_repaired'] == 0
    assert report['aliases_after_core_triangulation'] == 'verified_topology'
    assert report['aliases_reject_extra_face'] == 'unavailable'
    assert len(report['decal_charts']) == 4
    for row in report['decal_charts']:
        assert row['regions'] == (['inner'] if row['inner'] else ['base'])
        assert row['max_position_error_mm'] < 2e-5
        assert row['max_standoff_error_mm'] < 2e-5
