"""Audit a completed articulation probe and render old/new photo projections."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

from reconstruction.camera import Camera
from reconstruction.interior_contact import interior_contact_faces
from reconstruction.mesh import TriangleMesh
from reconstruction.raster import rasterize
from reconstruction.refine_photos import compare_image, geometry_rgb
from reconstruction.view_scene import (read_view_scene_contract, pose_mesh_from_contract,
                                      pose_scene, transfer_face_roles)


def _posed(report, view):
    source, binding, state = read_view_scene_contract(report['view_scene'], view_id=view['view_id'],
                                                     photo_sha256=view['source_sha256'])
    norm = report['normalization']
    result = pose_scene(source, binding, state, compact=True)
    mesh = result.mesh
    return TriangleMesh((mesh.vertices - norm['center']) / norm['extent'], mesh.faces, mesh.parts)


def _arms(view):
    result = {}
    for side, row in view.get('articulation_evidence', {}).items():
        key = 'proposal' if row.get('retained') else 'baseline'
        quality = row.get('absolute_correspondence_quality' if row.get('retained') else 'baseline_correspondence_quality', {})
        result[side] = {'status': row['status'], 'angle': view['view_state'][side + '_degrees'],
                        'holdout_px': row.get(key, {}).get('holdout_error_px'),
                        'absolute_supported': quality.get('supported', False)}
    return result


def audit(refinement, baseline=None):
    path = Path(refinement).resolve(); report = json.loads(path.read_bytes())
    source, binding, _ = read_view_scene_contract(report['view_scene'])
    norm = report['normalization']; roles = np.asarray(binding.face_roles)
    normalized = TriangleMesh((source.vertices - norm['center']) / norm['extent'], source.faces, source.parts)
    reconstructed = TriangleMesh(normalized.vertices * norm['extent'] + norm['center'], source.faces, source.parts)
    transferred, transfer = transfer_face_roles(source, binding, reconstructed)
    unresolved = np.asarray(transferred.face_roles) == 'unresolved'
    interior, _ = interior_contact_faces(source)
    rows = []
    for view in report['views']:
        _, _, state = read_view_scene_contract(report['view_scene'], view_id=view['view_id'], photo_sha256=view['source_sha256'])
        posed = pose_mesh_from_contract(normalized, report['view_scene'], view['view_id'],
            photo_sha256=view['source_sha256'], normalization=norm)['mesh']
        direct = pose_scene(source, binding, state, compact=True)
        expected = (direct.mesh.vertices[direct.mesh.faces] - norm['center']) / norm['extent']
        actual = posed.vertices[posed.faces]
        front_error = float(np.max(np.abs(actual[roles == 'front'] - normalized.vertices[normalized.faces[roles == 'front']]), initial=0.))
        error = float(np.max(np.abs(actual[~unresolved] - expected[~unresolved]), initial=0.))
        raster = rasterize(posed, Camera(**view['camera_fit']['camera']), tuple(view['image_size_working'][::-1]))
        visible = np.unique(raster.face_index[raster.face_index >= 0])
        rows.append({'view_id': view['view_id'], 'front_maximum_coordinate_change': front_error,
                     'resolved_pose_maximum_error': error, 'unresolved_visible_faces': int(unresolved[visible].sum()),
                     'face_order_preserved': bool(np.array_equal(direct.source_face_ids, np.arange(len(source.faces))))})
    result = {'method': 'source_bound_articulation_contract_audit_v1',
        'refinement': {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
        'source_faces': len(source.faces), 'views': len(rows), 'role_transfer': transfer,
        'unresolved_interior_faces': int((unresolved & interior).sum()),
        'unresolved_noninterior_faces': int((unresolved & ~interior).sum()),
        'face_order_preserved': all(r['face_order_preserved'] for r in rows),
        'front_triangles_unchanged': all(r['front_maximum_coordinate_change'] < 1e-12 for r in rows),
        'resolved_pose_adapter_matches_direct': all(r['resolved_pose_maximum_error'] < 1e-12 for r in rows),
        'per_view': rows}
    (path.parent / 'contract-audit.json').write_text(json.dumps(result, indent=2)+'\n')
    if baseline is not None:
        old = json.loads(Path(baseline).read_bytes()); old_views = {v['view_id']: v for v in old['views']}
        changes = []
        for view in report['views']:
            previous = old_views[view['view_id']]
            if previous['source_sha256'] != view['source_sha256']:
                raise ValueError('Old/new comparison photographs differ')
            shape = tuple(view['image_size_working'][::-1])
            image = geometry_rgb(Image.open(view['source'])).resize(tuple(view['image_size_working']), Image.Resampling.LANCZOS)
            before = rasterize(_posed(old, previous), Camera(**previous['camera_fit']['camera']), shape)
            after = rasterize(_posed(report, view), Camera(**view['camera_fit']['camera']), shape)
            compare_image(np.asarray(image), before, after, np.empty((0, 2)), path.parent / view['view_id'] / 'previous-vs-current.png')
            changes.append({'view_id': view['view_id'], 'old_camera': previous['camera_fit']['camera'],
                'new_camera': view['camera_fit']['camera'], 'old_arms': _arms(previous), 'new_arms': _arms(view),
                'camera_selection': view['camera_fit'].get('camera_support_selection')})
        (path.parent / 'previous-vs-current.json').write_text(json.dumps({'baseline': str(Path(baseline).resolve()),
            'current': str(path), 'views': changes, 'scope': 'Actual photo projections and internal arm witnesses, not independent product accuracy.'}, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refinement', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    print(json.dumps(audit(args.refinement, args.baseline), indent=2))
