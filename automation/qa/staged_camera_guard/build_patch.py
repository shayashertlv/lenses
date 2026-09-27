"""Build a reviewable patch without touching frozen production sources."""
import difflib
import hashlib
import json
from pathlib import Path


ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
path=ROOT/'reconstruction'/'refine_photos.py'
original=path.read_text(encoding='utf-8');updated=original


def change(old,new):
    global updated
    if updated.count(old)!=1:raise ValueError('Frozen refinement context changed: '+old[:90])
    updated=updated.replace(old,new)


change('from dataclasses import dataclass\n','from dataclasses import dataclass, replace\n')
change('from .camera import Camera, fit_camera\n','from .camera import Camera, fit_camera, project, render_mask\n')
change("    inferred = infer_automatic_part_bindings(world_mesh)\n",'''    from .camera_pose_selection import (front_camera_split, measure_front_camera_witness,
                                         choose_camera_with_pose_support)
    inferred = infer_automatic_part_bindings(world_mesh)
''')
change("        camera = Camera(**coarse['camera'])\n        front_raster", "        camera = coarse_camera = Camera(**coarse['camera'])\n        front_raster")
change('''        fitted = None
        if selected.sum() >= 12:
''','''        fitted = None
        split = None
        if selected.sum() >= 12:
''')
change('''            fitted = fit_front_cameras(mesh, normalized_binding, [evidence], {photo.id: camera},
                policy=StructuredPolicy(maximum_camera_evaluations=min(camera_evaluations, 100)))
            camera = fitted['cameras'].get(photo.id, camera)
            front_evidence.append(evidence)
        fit = {**coarse, 'camera': camera.to_dict(), 'front_only_refinement': fitted['report'] if fitted else None}
        pose = fit_photo_arm_states(mesh, normalized_binding, rgb, camera, photo.id)
''','''            split = front_camera_split(evidence.targets_xy)
            if split['supported']:
                train = split['train']
                camera_evidence = replace(evidence, face_ids=evidence.face_ids[train],
                    barycentric=evidence.barycentric[train], targets_xy=evidence.targets_xy[train],
                    sigma_xy=evidence.sigma_xy[train], normal_xy=evidence.normal_xy[train])
                fitted = fit_front_cameras(mesh, normalized_binding, [camera_evidence], {photo.id: camera},
                    policy=StructuredPolicy(maximum_camera_evaluations=min(camera_evaluations, 100)))
                camera = fitted['cameras'].get(photo.id, camera)
            front_evidence.append(evidence)
        front_candidate = camera
        coarse_pose = fit_photo_arm_states(mesh, normalized_binding, rgb, coarse_camera, photo.id)
        pose = (fit_photo_arm_states(mesh, normalized_binding, rgb, front_candidate, photo.id)
                if front_candidate != coarse_camera else coarse_pose)
        if split is not None and split['supported']:
            target = evidence.targets_xy
            before_witness = measure_front_camera_witness(render_mask(front_mesh, coarse_camera, rgb.shape[:2]),
                                                          target, split['holdout'])
            after_witness = measure_front_camera_witness(render_mask(front_mesh, front_candidate, rgb.shape[:2]),
                                                         target, split['holdout'])
            reference_width = max(1., min(float(rgb.shape[1]), float(np.ptp(project(mesh.vertices, coarse_camera)[:, 0]))))
            camera_selection = choose_camera_with_pose_support(before_witness, after_witness,
                coarse_pose['report'], pose['report'], reference_width)
            camera_selection['front_split'] = split['report']
            if camera_selection['selected'] == 'whole_scene':
                camera, pose = coarse_camera, coarse_pose
        else:
            camera, pose = coarse_camera, coarse_pose
            camera_selection = {'selected': 'whole_scene', 'reason': 'insufficient_front_train_holdout_support',
                                'front_split': split['report'] if split is not None else None,
                                'independent_validation': False}
        fit = {**coarse, 'camera': camera.to_dict(), 'whole_scene_camera': coarse_camera.to_dict(),
               'front_only_candidate_camera': front_candidate.to_dict(),
               'front_only_refinement': fitted['report'] if fitted else None,
               'camera_support_selection': camera_selection}
        write_json(folder / 'camera-support-selection.json', {'source_sha256': photo_hashes[photo.id],
            'model_sha256': model_hash, **camera_selection})
''')
change("            camera_hypothesis='front_only_edges_after_whole_scene_seed',\n",'''            camera_hypothesis=('whole_scene_camera_retained_by_support_guard' if camera_selection['selected'] == 'whole_scene'
                               else 'front_only_edges_after_whole_scene_seed'),
''')

staged=HERE/'refine_photos.py.staged';staged.write_text(updated,encoding='utf-8')
module=(HERE/'camera_pose_selection.py').read_text(encoding='utf-8')
tests=(HERE/'test_camera_pose_selection.py').read_text(encoding='utf-8').replace(
    'from qa.staged_camera_guard.camera_pose_selection import','from reconstruction.camera_pose_selection import')
diff=list(difflib.unified_diff(original.splitlines(),updated.splitlines(),n=3))[2:]
hunks=['@@' if line.startswith('@@ ') else line for line in diff]
patch='*** Begin Patch\n*** Add File: reconstruction/camera_pose_selection.py\n'
patch+='\n'.join('+'+line for line in module.splitlines())+'\n'
patch+='*** Update File: reconstruction/refine_photos.py\n'+'\n'.join(hunks)+'\n'
patch+='*** Add File: tests/test_camera_pose_selection.py\n'+'\n'.join('+'+line for line in tests.splitlines())+'\n*** End Patch\n'
(HERE/'camera-support-guard.patch').write_text(patch,encoding='utf-8')
(HERE/'patch-manifest.json').write_text(json.dumps({'source_refine_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
    'staged_refine_sha256':hashlib.sha256(staged.read_bytes()).hexdigest(),
    'patch_sha256':hashlib.sha256((HERE/'camera-support-guard.patch').read_bytes()).hexdigest(),
    'production_sources_modified':False},indent=2)+'\n',encoding='utf-8')
compile(updated,str(staged),'exec')
print('Patch and staged implementation written; production sources unchanged.')
