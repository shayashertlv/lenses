"""Deterministic mixed-arm-pose diagnostic; no providers or production edits.

python -m qa.structured_geometry_probe --output data/structured-geometry-v1
Optional --inventory inspects an existing component inventory for binding scope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from reconstruction.camera import Camera, project
from reconstruction.mesh import TriangleMesh
from reconstruction.structured_refinement import (SurfaceObservation, assess_geometry_candidate,
    fit_front_cameras, fit_temple_poses)
from reconstruction.view_scene import Hinge, ViewState, bind_parts, pose_points


def synthetic_fixture():
    vertices, faces, groups = [], [], {}
    cube_faces = np.array([[0, 2, 1], [1, 2, 3], [4, 5, 6], [5, 7, 6],
        [0, 1, 4], [1, 5, 4], [2, 6, 3], [3, 6, 7], [0, 4, 2], [2, 4, 6], [1, 3, 5], [3, 7, 5]])
    for name, lower, upper in (
        ('left_lens', [-.47, -.15, -.012], [-.08, .15, .012]),
        ('right_lens', [.08, -.15, -.012], [.47, .15, .012]),
        ('bridge', [-.08, .04, -.016], [.08, .08, .016]),
        ('left_temple', [-.49, -.018, -.62], [-.47, .018, 0.]),
        ('right_temple', [.47, -.018, -.62], [.49, .018, 0.])):
        points = np.array([[upper[axis] if corner & (1 << axis) else lower[axis]
                            for axis in range(3)] for corner in range(8)])
        groups[name] = np.arange(len(faces) * 12, (len(faces) + 1) * 12)
        faces.append(cube_faces + len(vertices) * 8)
        vertices.append(points)
    scene = TriangleMesh(np.concatenate(vertices), np.concatenate(faces), [])
    binding = bind_parts(scene, front_faces=np.r_[groups['left_lens'], groups['right_lens'], groups['bridge']],
        left_temple_faces=groups['left_temple'], right_temple_faces=groups['right_temple'],
        hinges=(Hinge('left', np.array([-.48, 0., 0.]), np.array([0., 1., 0.])),
                Hinge('right', np.array([.48, 0., 0.]), np.array([0., 1., 0.]))),
        provenance='Controlled synthetic box assembly with known material-point observations')
    cameras = {'front': Camera(3., 12., 2., 0., 190., 128., 128.),
               'angled': Camera(38., 19., -3., 0., 185., 126., 131.)}
    states = {'front': ViewState('front', 61., -43.), 'angled': ViewState('angled', 7., -67.)}
    evidence = []
    for view_id, camera in cameras.items():
        for group, face_ids in groups.items():
            points = scene.vertices[scene.faces[face_ids]].mean(axis=1)
            roles = np.asarray(binding.face_roles)[face_ids]
            target = project(pose_points(points, roles, binding, states[view_id]), camera)
            evidence.append(SurfaceObservation(view_id, group, hashlib.sha256(view_id.encode()).hexdigest(),
                binding.source_geometry_sha256, face_ids, np.full((len(face_ids), 3), 1 / 3), target,
                np.ones_like(target), 'Independent analytic synthetic projection'))
    seeds = {name: Camera(camera.yaw + 4, camera.pitch + 3, camera.roll + 1,
        camera.perspective, camera.scale * .95, camera.center_x + 3, camera.center_y - 2)
        for name, camera in cameras.items()}
    return scene, binding, evidence, cameras, states, seeds


def run_synthetic():
    scene, binding, evidence, true_cameras, true_states, seeds = synthetic_fixture()
    camera_result = fit_front_cameras(scene, binding, evidence, seeds)
    camera_only = assess_geometry_candidate(scene, binding, evidence, camera_result['cameras'], {})
    pose_result = fit_temple_poses(scene, binding, evidence, camera_result['cameras'])
    articulated = assess_geometry_candidate(scene, binding, evidence, camera_result['cameras'], pose_result['view_states'])
    return {'schema_version': 1, 'status': 'synthetic_diagnostic_complete', 'accepted': False,
        'fixture': 'Two lens boxes, bridge and two rigid arms; two photographs with different arm openings',
        'source_geometry_sha256': binding.source_geometry_sha256,
        'expected_cameras': {key: value.to_dict() for key, value in true_cameras.items()},
        'expected_view_states': {key: value.to_dict() for key, value in true_states.items()},
        'front_camera_fit': camera_result['report'], 'temple_pose_fit': pose_result['report'],
        'fitted_view_states': {key: value.to_dict() for key, value in pose_result['view_states'].items()},
        'camera_only_assessment': camera_only, 'articulated_assessment': articulated,
        'maximum_group_rms_px': max(row['rms_px'] for row in articulated['groups']),
        'shared_rest_mesh_changed': False,
        'limitations': ['Exact supplied synthetic correspondences and hinge axes; automatic evidence is not tested.',
                       'No claim of corrected topology, GLB export, or integrated downstream photographic sampling.']}


def inventory_scope(path):
    from reconstruction.component_scene import load_component_scene
    scene = load_component_scene(Path(path))
    mesh = scene['mesh']
    components = []
    for row in scene['component_table']:
        points = mesh.vertices[mesh.faces[scene['face_components'] == row['component_id']]]
        components.append({'component_id': row['component_id'], 'face_count': row['source_face_count'],
            'world_bounds': [points.min(axis=(0, 1)).tolist(), points.max(axis=(0, 1)).tolist()]})
    return {'source_sha256': scene['source_sha256'], 'source_face_count': len(mesh.faces),
        'component_count': len(components), 'components': components,
        'binding_status': 'not_inferred',
        'remaining_requirements': ['Ground front/temple face membership independently of provider component names.',
            'Locate a shared hinge origin/axis for each arm and verify attachment under rotation.',
            'Bind visible photo landmarks/normal edges to source triangle coordinates.',
            'Supply the same posed scene to aperture, rear-content and optical ray consumers.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--inventory', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a fresh output directory to preserve diagnostic receipts')
    report = run_synthetic()
    if args.inventory:
        report['cached_inventory'] = inventory_scope(args.inventory)
    report['implementation_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (Path(__file__), Path('reconstruction/view_scene.py'), Path('reconstruction/structured_refinement.py'))}
    args.output.mkdir(parents=True)
    (args.output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'report': str((args.output / 'report.json').resolve()),
                      'maximum_group_rms_px': report['maximum_group_rms_px']}, indent=2))


if __name__ == '__main__':
    main()
