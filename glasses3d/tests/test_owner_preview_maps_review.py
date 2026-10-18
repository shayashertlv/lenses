"""AT-02/05/07: shown amounts, fitted directions, bounded lossless map discovery."""
import copy
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent import edits
from glasses3d.agent.tools import model_map as M, model_map_geometry as G
from glasses3d.agent.tools import owner_preview as P, owner_edit
from glasses3d.g3d_host.camera import Camera
from glasses3d.tests.test_model_map import snapshot
from glasses3d.tests.agent.test_owner_edit_lifecycle_review import selected_source
from glasses3d.tests.agent.test_owner_understanding_dispositions import shown, item


@pytest.mark.parametrize('kind,params', [
    ('translate_region', {'delta_mm': [.2, 0, 0]}),
    ('taper_region', {'delta_mm': [.2, 0, 0], 'axis_points_mm': [[0, 0, 0], [0, 0, -6]]}),
    ('rotate_region', {'delta_deg': 1., 'axis_points_mm': [[0, 0, 0], [0, 0, 4]]}),
])
def test_move_only_understanding_requires_delivered_amount_preview(shown, kind, params):
    h = shown
    record = item(h, disposition='executable')
    operation = {**h.operation, 'kind': kind, 'params': params}
    record['operation_json'] = json.dumps(operation)
    with pytest.raises(ValueError, match='delivered.*preview'):
        owner_edit.understand(h.scene, 'missing-amounts', h.mid, edits.sha(h.map),
                             [h.capture_result['capture_receipt']], [record], [])
    assert not (h.root / 'work/owner_edit/understanding.json').exists()


def _basis(monkeypatch, rows):
    refs = {rid: {'view_kind': label} for rid, label, yaw, pitch in rows}
    cams = {rid: Camera(yaw, pitch, 0, 0, 1000, 160, 120) for rid, label, yaw, pitch in rows}
    monkeypatch.setattr(P.look.C, 'load_basis', lambda *args: SimpleNamespace(
        refs=refs, usable_refs=lambda: list(refs), camera=lambda rid: cams[rid], pose=lambda rid: None))
    monkeypatch.setattr(P.look.C, 'ref_id', lambda name: name)


def test_revealing_cameras_use_fit_not_labels_or_capitalisation(monkeypatch):
    _basis(monkeypatch, [('chosen', 'side', 90, 0), ('upper', 'Top', 0, 90),
                        ('behind', 'rear three quarter', 155, 0), ('back', 'back', 180, 0),
                        ('mislabelled', 'top', -90, 0)])
    op = {'kind': 'translate_region', 'params': {'delta_mm': [3, 0, 0]}}
    assert P.revealing_refs(None, None, [op], ['chosen']) == ['chosen', 'upper', 'behind', 'back']


def test_rotation_adds_fitted_views_of_actual_supported_motion(monkeypatch):
    _basis(monkeypatch, [('side', 'Side', 90, 0), ('upper', 'topb', 0, 90),
                        ('rear', 'back', 180, 0)])
    source = snapshot()
    obj = source['objects'][0]
    op = {'kind': 'rotate_region', 'scope': G.support(source, G.catalogue(source)['targets'], [obj['id']]),
          'params': {'axis_points_mm': [[0, 0, 0], [0, 1, 0]], 'delta_deg': 10}}
    assert P.revealing_refs(None, None, [op], ['side'], source=source) == ['side', 'upper', 'rear']


def torus():
    source = snapshot()
    obj = source['objects'][0]
    n, m = 32, 16
    obj['vertices_world_mm'] = [[(5 + np.cos(b))*np.cos(a), (5 + np.cos(b))*np.sin(a), np.sin(b)]
        for a in np.arange(n)*2*np.pi/n for b in np.arange(m)*2*np.pi/m]
    obj['polygons'] = [[i*m+j, ((i+1)%n)*m+j, ((i+1)%n)*m+(j+1)%m, i*m+(j+1)%m]
                       for i in range(n) for j in range(m)]
    obj['edges'] = [list(edge) for edge in sorted({tuple(sorted((a, b))) for p in obj['polygons']
                                                for a, b in zip(p, p[1:]+p[:1])})]
    obj['aliases'] = {}
    return source


@pytest.mark.parametrize('kind,target_kind', [('faces', 'face_region'), ('edges', 'edge_chain'), ('points', 'point')])
def test_smooth_catalogue_bounded_discovery_keeps_every_target_reachable(kind, target_kind):
    catalogue = G.catalogue(torus())
    expected = [t['id'] for t in catalogue['targets'].values() if t['kind'] == target_kind]
    first = M._select(catalogue, None, kind)
    assert len(first) <= 256, 'smooth meshes must not project every face/edge/point on one request'
    reached = [t['id'] for page in range((len(expected)+255)//256)
               for t in M._select(catalogue, None, kind, target_page=page)]
    assert reached == expected
    assert [t['id'] for t in M._select(catalogue, [expected[-1]], kind)] == [expected[-1]]
    assert len(catalogue['targets']) == 2049  # full 512 faces, 1024 edges, 512 points, object


def _annotation(tmp_path, monkeypatch, selected, source, projected):
    for name in ('photo', 'drawing', 'ids'):
        Image.new('RGB', (80, 60), 'white').save(tmp_path / (name+'.png'))
    row = {'ref': 'fit', 'camera': {'width': 80, 'height': 60},
           **{key: str(tmp_path / (name+'.png')) for key, name in
              [('photo', 'photo'), ('drawing', 'drawing'), ('part_id', 'ids')]}}
    projection_sizes, ray_sizes = [], []
    monkeypatch.setattr(M.look_panels, 'posed_geometry', lambda geometry, pose: geometry)
    monkeypatch.setattr(M.G, 'first_hits', lambda *args: np.full((60, 80), -1))
    def project(points, camera):
        projection_sizes.append(len(points))
        xy = projected(points)
        return xy, np.ones(len(points))
    def visible(points, camera, geometry):
        ray_sizes.append(len(points))
        return np.zeros(len(points), bool)
    monkeypatch.setattr(M.G, 'project', project)
    monkeypatch.setattr(M.G, 'visible_points', visible)
    result = M._annotate(row, source, selected, SimpleNamespace(), np.array(['O0001'], object),
                         np.array([0]), [], tmp_path, 0)
    return result, projection_sizes, ray_sizes


def test_camera_projects_large_object_once_not_once_per_edge(tmp_path, monkeypatch):
    source = snapshot()
    obj = source['objects'][0]
    target = next(t for t in G.catalogue(source)['targets'].values() if t['kind'] == 'edge_chain')
    obj['vertices_world_mm'] += [[10, 0, 0]] * (25000-len(obj['vertices_world_mm']))
    selected = [{**target, 'id': f'E{i:04d}'} for i in range(64)]
    _, projected, _ = _annotation(tmp_path, monkeypatch, selected, source,
                                  lambda xyz: np.asarray(xyz)[:, :2] + 30)
    assert projected.count(25000) == 1, 'whole-object projection must be cached for the camera'


def test_long_edge_raycast_work_is_bounded_and_continuation_is_reported(tmp_path, monkeypatch):
    source = snapshot()
    target = next(t for t in G.catalogue(source)['targets'].values() if t['kind'] == 'edge_chain')
    result, _, ray_sizes = _annotation(tmp_path, monkeypatch, [target], source,
                                      lambda xyz: np.asarray(xyz)[:, :2] * 30000 + 30)
    assert max(ray_sizes) <= 4096, 'one long edge must not bypass the per-target sample budget'
    assert result['edge_sample_pages'][target['id']] == 3


def test_edge_sample_pages_recover_exact_original_samples_without_skipping_short_edges():
    obj = {'edges': [[0, 1], [1, 2]]}
    points = np.array([[0., 0, 0], [20000., 0, 0], [20001., 0, 0]])
    target = {'edge_ids': [0, 1]}
    pages = [M._edge_samples(obj, target, points, points[:, :2], page) for page in range(3)]
    expected = np.concatenate([points[0]*(1-t)+points[1]*t for t in np.linspace(0, 1, 10000)]).reshape(-1, 3)
    expected = np.concatenate((expected, points[1:]))
    np.testing.assert_allclose(np.concatenate([row[0] for row in pages]), expected, atol=1e-11)
    assert all(row[1] == 10002 and len(row[0]) <= 4096 for row in pages)
