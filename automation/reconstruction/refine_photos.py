"""Offline multiview geometry proposal from an existing GLB and product photos.

This is a reusable refinement stage, not a finished photo-to-model generator.
Camera/edge hypotheses remain candidate-dependent and cannot accept quality.
Example: python -m reconstruction.refine_photos --model model.glb
  --photo front=front.jpg --photo angled=angled.jpg --output data/refinement/run
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .camera import Camera, fit_camera, project, render_mask
from .deformation import CageField, NormalizedField, ReprojectionConstraint, assess_proposal, fit_cage
from .deform_glb import deform_glb
from .edge_correspondence import match_contour_edges
from .evidence import (SourceImage, EvidenceProvenance, ImagePoint, EdgeEvidence,
                       Coverage, ComponentEvidence, PhotoEvidence)
from .mesh import TriangleMesh, load_glb
from .observations import observe_image
from .raster import rasterize, contour_samples


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class PhotoInput:
    """Photo identity is independent of an optional, possibly repeated view prior."""
    id: str
    path: Path
    view: str = 'unknown'


def photo_inputs(photos):
    result = [item if isinstance(item, PhotoInput) else PhotoInput(item[0], Path(item[1]), item[0])
              for item in photos]
    for item in result:
        if not isinstance(item.id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', item.id):
            raise ValueError('Photo identities must be safe directory names')
        if item.id.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *[f'COM{i}' for i in range(1, 10)], *[f'LPT{i}' for i in range(1, 10)]}:
            raise ValueError('Reserved photo identity')
        if item.view not in ('front', 'back', 'left', 'right', 'angled', 'unknown'):
            raise ValueError('Unsupported view label')
    if len(result) < 2 or len({item.id.casefold() for item in result}) != len(result):
        raise ValueError('Supply at least two distinct photo identities')
    return result


def preflight(model, photos, output, resolution, camera_evaluations):
    """Reject invalid, duplicate or stale inputs before creating run artifacts."""
    photos = photo_inputs(photos)
    if not 128 <= resolution <= 768 or not 20 <= camera_evaluations <= 1000:
        raise ValueError('Use resolution 128–768 and camera evaluations 20–1000')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory must be empty; keep previous run evidence intact')
    hashes, decoded_hashes = {}, set()
    for photo in photos:
        view, path = photo.id, photo.path
        with Image.open(path) as image:
            if image.getexif().get(274, 1) != 1:
                raise ValueError('Normalize EXIF orientation with a recorded transform before this experimental stage')
            pixels = np.asarray(image.convert('RGB'))
            key = hashlib.sha256(str(pixels.shape).encode() + pixels.tobytes()).hexdigest()
        photo_hash = digest(path)
        if photo_hash in hashes.values() or key in decoded_hashes:
            raise ValueError('Duplicate photos cannot supply independent view constraints')
        hashes[view] = photo_hash
        decoded_hashes.add(key)
    return digest(model), hashes


def implementation_manifest():
    import shapely
    sources = {path.name: digest(path) for path in sorted(Path(__file__).parent.glob('*.py'))}
    root=Path(__file__).resolve().parents[1]
    for name,path in [('scripts/mesh_lod.mjs',root/'scripts/mesh_lod.mjs'),
                      ('meshoptimizer/meshopt_simplifier.js',root.parent/'ar/node_modules/meshoptimizer/meshopt_simplifier.js')]:
        if path.is_file():sources[name]=digest(path)
    return {'source_sha256': sources,
            'packages': {name: importlib.metadata.version(name) for name in ('numpy', 'scipy', 'Pillow', 'opencv-python', 'shapely')},
            'native_geometry': {'geos': shapely.geos_version_string}}


def assert_sources_unchanged(model, model_hash, photos, photo_hashes):
    if digest(model) != model_hash or any(digest(item.path) != photo_hashes[item.id] for item in photo_inputs(photos)):
        raise ValueError('An input changed during the run; outputs cannot be associated with the pinned evidence')


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def geometry_rgb(image):
    """An alpha cutout supplies shape edges on a fixed diagnostic white field.

    This compositing is restricted to geometry matching. It is not passed to
    appearance fitting or represented as an observed studio background.
    """
    rgba = image.convert('RGBA')
    return Image.alpha_composite(Image.new('RGBA', rgba.size, (255, 255, 255, 255)), rgba).convert('RGB')


def opaque_partition(mesh):
    """One explicit camera hypothesis from existing material declarations."""
    faces = []
    for part in mesh.parts:
        if part['transmission'] <= 0 and part.get('declared_role') != 'lens':
            start = part['face_start']
            faces.extend(range(start, start + part['face_count']))
    if not faces or len(faces) == len(mesh.faces):
        return None
    triangles = mesh.faces[faces]
    used, inverse = np.unique(triangles, return_inverse=True)
    return TriangleMesh(mesh.vertices[used], inverse.reshape(-1, 3), [])


def view_geometry_diagnostics(constraints):
    """Necessary viewing-direction diagnostics, not a cage-identification claim."""
    axes = []
    for item in constraints:
        yaw, pitch = np.radians([item.camera.yaw, item.camera.pitch])
        axes.append(np.array([np.cos(pitch) * np.sin(yaw), np.sin(pitch), np.cos(pitch) * np.cos(yaw)]))
    pairs = [{'views': [constraints[i].view_id, constraints[j].view_id],
              'axis_separation_degrees_modulo_sign': float(np.degrees(np.arccos(np.clip(abs(axes[i] @ axes[j]), 0, 1))))}
             for i in range(len(axes)) for j in range(i)]
    maximum = max((item['axis_separation_degrees_modulo_sign'] for item in pairs), default=0.)
    return {'pairs': pairs, 'maximum_axis_separation_degrees': maximum,
            'view_axis_singular_values': np.linalg.svd(np.asarray(axes), compute_uv=False).tolist(),
            'essentially_parallel_axes': maximum < 1., 'shape_identification': 'unmeasured',
            'scope': 'Fitted cameras only. Angular separation does not prove shape identifiability; front/back antiparallel axes have zero baseline here.'}


def surface_reference(mesh, count=600):
    triangle = mesh.vertices[mesh.faces]
    areas = np.linalg.norm(np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0]), axis=1) / 2
    cumulative = np.cumsum(areas)
    if cumulative[-1] <= 0:
        raise ValueError('No nondegenerate source surface')
    selected = np.searchsorted(cumulative, (np.arange(count) + .5) / count * cumulative[-1])
    return triangle[selected].mean(axis=1), np.ones(count)


def export_backtracked(model, destination, cage, center, extent, model_hash, constraints, maximum_backtracks=6):
    """Reduce the whole field when fixed triangle chords cannot represent it.

    Every attempt uses the same evidence and retention policy. No threshold or
    product-specific constants change to make an unsafe export pass.
    """
    base = CageField(cage.lower, cage.upper, np.zeros_like(cage.displacements))
    attempts = []
    for step in range(maximum_backtracks + 1):
        scale = 2. ** -step
        trial = CageField(cage.lower, cage.upper, cage.displacements * scale)
        assessment = assess_proposal(base, trial, constraints)
        item = {'scale': scale, 'assessment': assessment, 'exported': False}
        attempts.append(item)
        if not assessment['retained']:
            item['reason'] = 'Frozen correspondence retention policy rejected this field'
            continue
        try:
            result = deform_glb(model, destination, NormalizedField(trial, center, extent, model_hash).transform)
        except ValueError as error:
            item['reason'] = str(error)
            continue
        item['exported'] = True
        return trial, result, {'attempts': attempts, 'selected_scale': scale,
                               'scope': 'Frozen correspondence retention and discrete export integrity, not reconstruction acceptance'}
    return None, None, {'attempts': attempts, 'selected_scale': None,
                        'scope': 'No tested nonzero field passed both correspondence and export checks'}


def boundary_distance(raster, targets):
    edge = raster.mask & ~ndimage.binary_erosion(raster.mask)
    if not edge.any():
        return {'measured': False, 'reason': 'candidate boundary missing'}
    distance = ndimage.distance_transform_edt(~edge)
    values = ndimage.map_coordinates(distance, targets.T[::-1], order=1, mode='constant', cval=float(max(distance.shape)))
    return {'measured': True, 'mean_px': float(values.mean()), 'p95_px': float(np.quantile(values, .95)),
            'maximum_px': float(values.max()), 'scope': 'selected frozen photo edges to actual candidate footprint only'}


def compare_image(rgb, before, after, targets, output):
    height, width = rgb.shape[:2]
    canvas = Image.new('RGB', (width * 3, height + 38), 'white')
    for i, (raster, label) in enumerate(((None, 'Photo'), (before, 'Before: red boundary'), (after, 'Proposal: cyan boundary'))):
        pixels = rgb.copy()
        if raster is not None:
            boundary = raster.mask & ~ndimage.binary_erosion(raster.mask)
            pixels[boundary] = [230, 40, 40] if i == 1 else [0, 190, 190]
        panel = Image.fromarray(pixels)
        draw = ImageDraw.Draw(panel)
        if i:
            for x, y in targets:
                draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(255, 180, 0))
        canvas.paste(panel, (i * width, 36))
        ImageDraw.Draw(canvas).text((i * width + 4, 3), label, fill='black')
    ImageDraw.Draw(canvas).text((4, 19), 'Orange: candidate-dependent photo edge hypotheses. Quality remains unmeasured.', fill='black')
    canvas.save(output)


def _automatic_articulated_run(model, photos, output, world_mesh, model_hash, photo_hashes, *,
                               resolution, camera_evaluations):
    """Fit front cameras and independently opened arms, keeping one rest asset."""
    from .automatic_articulation import infer_automatic_part_bindings, fit_photo_arm_states, assess_photo_arm_state
    from .structured_refinement import SurfaceObservation, fit_front_cameras, fit_shared_geometry, StructuredPolicy
    from .view_scene import (Hinge, PartBinding, ViewState, mesh_geometry_sha256,
                             make_view_scene_contract, pose_scene)
    from .camera_pose_selection import (front_camera_split, measure_front_camera_witness,
                                         choose_camera_with_pose_support)
    inferred = infer_automatic_part_bindings(world_mesh)
    if not inferred['bindings']:
        return None, inferred['report']
    binding = inferred['bindings'][0]
    lower, upper = world_mesh.vertices.min(axis=0), world_mesh.vertices.max(axis=0)
    center, extent = (lower + upper) / 2, float(np.max(upper - lower))
    mesh = TriangleMesh((world_mesh.vertices - center) / extent, world_mesh.faces, world_mesh.parts)
    normalized_binding = PartBinding(mesh_geometry_sha256(mesh), binding.face_roles,
        tuple(Hinge(h.side, (h.origin - center) / extent, h.axis, h.minimum_degrees, h.maximum_degrees)
              for h in binding.hinges), binding.provenance)
    front_ids = np.flatnonzero(np.asarray(binding.face_roles) == 'front')
    front_mesh = TriangleMesh(mesh.vertices, mesh.faces[front_ids], [])
    views, states, pose_reports, front_evidence, fitted_cameras = [], {}, {}, [], {}
    geometry_images = {}
    for photo in photos:
        folder = output / photo.id; folder.mkdir(parents=True, exist_ok=True)
        image = Image.open(photo.path)
        original_size = np.array(image.size)
        size = tuple(max(5, round(v * min(1., resolution / max(image.size)))) for v in image.size)
        rgb = np.asarray(geometry_rgb(image).resize(size, Image.Resampling.LANCZOS))
        geometry_images[photo.id] = rgb
        observation = observe_image(image)
        summary = {'view_id': photo.id, 'view_label': photo.view, 'source': str(photo.path.resolve()),
                   'source_sha256': photo_hashes[photo.id], 'image_size_original': original_size.tolist(),
                   'image_size_working': list(size), 'contrast': observation.to_report(), 'status': 'unmeasured'}
        views.append(summary)
        if observation.usable_mask is None:
            summary['reason'] = 'No supported photographic silhouette for camera seed'
            continue
        mask = np.asarray(Image.fromarray(observation.mask).resize(size, Image.Resampling.NEAREST), bool)
        coarse = fit_camera(mesh, mask, view=photo.view, max_evaluations=camera_evaluations)
        camera = coarse_camera = Camera(**coarse['camera'])
        front_raster = rasterize(front_mesh, camera, rgb.shape[:2])
        sampled = contour_samples(front_mesh, front_raster)
        matches = match_contour_edges(rgb, sampled['xy'], sampled['normals'],
            source_sha256=photo_hashes[photo.id], model_sha256=model_hash)
        selected = matches.selected_mask
        fitted = None
        split = None
        if selected.sum() >= 12:
            xy = sampled['xy'][selected].astype(int)
            faces = front_ids[sampled['faces'][selected]]
            barycentric = front_raster.barycentric[xy[:, 1], xy[:, 0]]
            evidence = SurfaceObservation(photo.id, 'front-contour', photo_hashes[photo.id],
                normalized_binding.source_geometry_sha256, faces, barycentric,
                matches.matched_xy[selected], np.repeat(matches.sigma_px[selected, None], 2, axis=1),
                'Candidate-guided front-only contour image edges; arm observations excluded from camera loss',
                normal_xy=sampled['normals'][selected])
            split = front_camera_split(evidence.targets_xy)
            train = split['fit']
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
        elif fitted is not None:
            camera_selection = {'selected': 'front_refined',
                'reason': 'full_front_camera_hypothesis_retained_without_sufficient_guard_witnesses',
                'guard_status': 'insufficient_witnesses', 'front_split': split['report'],
                'independent_validation': False,
                'scope': 'Legacy full-front fit; no held-out camera superiority is claimed.'}
        else:
            camera, pose = coarse_camera, coarse_pose
            camera_selection = {'selected': 'whole_scene', 'reason': 'insufficient_total_front_points',
                                'guard_status': 'insufficient_witnesses',
                                'front_split': split['report'] if split is not None else None,
                                'independent_validation': False}
        fit = {**coarse, 'camera': camera.to_dict(), 'whole_scene_camera': coarse_camera.to_dict(),
               'front_only_candidate_camera': front_candidate.to_dict(),
               'front_only_refinement': fitted['report'] if fitted else None,
               'camera_support_selection': camera_selection}
        write_json(folder / 'camera-support-selection.json', {'source_sha256': photo_hashes[photo.id],
            'model_sha256': model_hash, **camera_selection})
        fitted_cameras[photo.id] = camera
        states[photo.id] = pose['state']; pose_reports[photo.id] = pose['report']
        before = rasterize(mesh, camera, rgb.shape[:2])
        after = rasterize(pose_scene(mesh, normalized_binding, pose['state']).mesh, camera, rgb.shape[:2])
        compare_image(rgb, before, after, np.empty((0, 2)), folder / 'comparison.png')
        write_json(folder / 'arm-pose-fit.json', pose['report'])
        write_json(folder / 'front-edge-hypotheses.json', matches.to_report())
        summary.update(status='automatic_articulated_camera', camera_fit=fit,
            camera_hypothesis=('whole_scene_camera_retained_by_support_guard' if camera_selection['selected'] == 'whole_scene'
                               else 'front_only_edges_after_whole_scene_seed'),
            selected_front_edge_count=int(selected.sum()), view_state=pose['state'].to_dict(),
            articulation_evidence=pose['report'])
    if len(states) < 2:
        return None, {**inferred['report'], 'status': 'insufficient_articulated_photo_support'}
    selected_mesh, selected_binding, selected_path, selected_hash = world_mesh, binding, model, model_hash
    shape_report, exported = None, None
    if len(front_evidence) >= 2:
        shared = fit_shared_geometry(mesh, normalized_binding, front_evidence, fitted_cameras, states,
                                    policy=StructuredPolicy(maximum_shape_evaluations=30))
        shape_report = shared['report']
        if shared['field'] is not None:
            try:
                exported = deform_glb(model, output / 'proposal.glb',
                    NormalizedField(shared['field'], center, extent, model_hash).transform)
                actual = load_glb(output / 'proposal.glb')
                if len(actual.faces) != len(world_mesh.faces):
                    raise ValueError('Shared shape export changed source face lineage')
                actual_normalized = TriangleMesh((actual.vertices - center) / extent, actual.faces, actual.parts)
                checks = []
                for evidence in front_evidence:
                    camera = fitted_cameras[evidence.view_id]
                    summary = next(v for v in views if v['view_id'] == evidence.view_id)
                    shape = tuple(summary['image_size_working'][::-1])
                    before = rasterize(front_mesh, camera, shape)
                    after = rasterize(TriangleMesh(actual_normalized.vertices, actual_normalized.faces[front_ids], []), camera, shape)
                    a, b = boundary_distance(before, evidence.targets_xy), boundary_distance(after, evidence.targets_xy)
                    passed = a['measured'] and b['measured'] and b['mean_px'] <= a['mean_px'] + .25 and b['p95_px'] <= a['p95_px'] + .25
                    checks.append({'view_id': evidence.view_id, 'before': a, 'after': b, 'nonregression': bool(passed)})
                    summary['rerender'] = {'before': a, 'proposal': b, 'nonregression': bool(passed),
                        'geometry_source': 'exported_glb_float32', 'sampling_tolerance_working_px': .25,
                        'independent_validation': False, 'scope': 'front-only photographic contour'}
                shape_report['exported_front_rerenders'] = checks
                if not all(check['nonregression'] for check in checks):
                    raise ValueError('Exported shared front shape regressed actual photo boundaries')
                actual_binding = PartBinding(mesh_geometry_sha256(actual_normalized), binding.face_roles,
                    shared['binding'].hinges, binding.provenance)
                arm_checks = []
                for view_id, state in states.items():
                    args = (geometry_images[view_id], fitted_cameras[view_id], state)
                    before = assess_photo_arm_state(mesh, normalized_binding, *args)
                    after = assess_photo_arm_state(actual_normalized, actual_binding, *args)
                    for side, a in before.items():
                        b = after[side]
                        passed = (not a['supported'] or (b['supported'] and
                            b['visible_boundary_points'] >= a['visible_boundary_points'] * .7 and
                            b['holdout_error_px'] <= a['holdout_error_px'] + .25))
                        arm_checks.append({'view_id': view_id, 'side': side, 'before': a, 'after': b,
                                           'nonregression': bool(passed)})
                shape_report['exported_frozen_arm_rerenders'] = arm_checks
                if not all(check['nonregression'] for check in arm_checks):
                    raise ValueError('Exported shared shape regressed frozen photographed arm poses')
                selected_mesh, selected_path, selected_hash = actual, output / 'proposal.glb', exported['output_sha256']
                selected_binding = PartBinding(mesh_geometry_sha256(actual), binding.face_roles,
                    tuple(Hinge(h.side, h.origin * extent + center, h.axis, h.minimum_degrees, h.maximum_degrees)
                          for h in shared['binding'].hinges), binding.provenance)
            except ValueError as error:
                shape_report['export_rejection'] = str(error)
                exported = None
                if (output / 'proposal.glb').exists():
                    (output / 'proposal.glb').replace(output / 'rejected-proposal.glb')
    contract = make_view_scene_contract(selected_mesh, selected_binding, states, source_model=selected_path,
        source_sha256=selected_hash, photo_sha256=photo_hashes,
        evidence={'automatic_binding': inferred['report'], 'photo_pose_reports': pose_reports,
                  'shared_shape': shape_report})
    report = {'schema_version': 1, 'status': 'articulated_views_fitted', 'quality_verdict': 'unmeasured',
        'source_model': str(model.resolve()), 'source_sha256': model_hash, 'implementation': implementation_manifest(),
        'normalization': {'center': center.tolist(), 'extent': extent}, 'views': views,
        'view_scene': contract, 'automatic_binding': inferred['report'],
        'settings': {'resolution': resolution, 'camera_evaluations': camera_evaluations, 'automatic_articulation': True},
        'rest_geometry_unchanged': exported is None, 'shared_shape': shape_report, 'accepted': False,
        'limitations': ['Source part roles and hinge axes are geometric manufacturing hypotheses.',
                       'Photo edges may include unrelated structure; uncertain or occluded arm poses retain rest.',
                       'No topology is created and no independent product geometry acceptance is claimed.']}
    if exported is not None:
        report.update(status='proposal_exported', export=exported, proposal_artifact='proposal.glb', rerender_nonregression=True)
    assert_sources_unchanged(model, model_hash, photos, photo_hashes)
    write_json(output / 'report.json', report)
    return report, inferred['report']


def run(model, photos, output, *, resolution=320, camera_evaluations=160, automatic_articulation=True):
    model, output = Path(model), Path(output)
    photos = photo_inputs(photos)
    model_hash, photo_hashes = preflight(model, photos, output, resolution, camera_evaluations)
    output.mkdir(parents=True, exist_ok=True)
    world_mesh = load_glb(model)
    automatic_report = None
    if automatic_articulation:
        articulated, automatic_report = _automatic_articulated_run(model, photos, output, world_mesh, model_hash,
            photo_hashes, resolution=resolution, camera_evaluations=camera_evaluations)
        if articulated is not None:
            return articulated
    lower, upper = world_mesh.vertices.min(axis=0), world_mesh.vertices.max(axis=0)
    center, extent = (lower + upper) / 2, float(np.max(upper - lower))
    if not np.isfinite(extent) or extent <= 0:
        raise ValueError('Source geometry has no finite spatial extent')
    mesh = TriangleMesh((world_mesh.vertices - center) / extent, world_mesh.faces, world_mesh.parts)
    opaque = opaque_partition(mesh)
    report = {'schema_version': 1, 'status': 'running', 'quality_verdict': 'unmeasured',
              'source_model': str(model.resolve()), 'source_sha256': model_hash,
              'implementation': implementation_manifest(),
              'settings': {'resolution': resolution, 'camera_evaluations': camera_evaluations},
              'normalization': {'center': center.tolist(), 'extent': extent}, 'views': [],
              'automatic_articulation': automatic_report,
              'limitations': ['Starts from an existing model; it does not generate missing components.',
                              'Cameras and edge hypotheses depend on the initial model and any supplied view priors; unknown views use geometric seed hypotheses.',
                              'Contrast/camera material hypotheses are not semantic masks.',
                              'Temples may have different articulation between photographs; this stage cannot estimate it.',
                              'No independent evaluation observations or final appearance gate are present.',
                              'The current exporter checks triangle collapse/inversion, but not global self-intersections.']}
    constraints, view_data = [], []
    for photo_input in photos:
        view, label, photo = photo_input.id, photo_input.view, Path(photo_input.path)
        folder = output / view
        folder.mkdir(exist_ok=True)
        print(f'Preparing {view}: {photo.name}', flush=True)
        image = Image.open(photo)
        if image.getexif().get(274, 1) != 1:
            raise ValueError('Normalize EXIF orientation with a recorded transform before this experimental stage')
        observation = observe_image(image)
        summary = {'view_id': view, 'view_label': label, 'source': str(photo.resolve()), 'source_sha256': photo_hashes[view],
                   'contrast': observation.to_report(), 'status': 'unmeasured'}
        report['views'].append(summary)
        if observation.usable_mask is None:
            summary['reason'] = 'No usable background-contrast camera initialization'
            continue
        original_size = np.array(image.size)
        scale = min(1., resolution / max(image.size))
        size = tuple(max(5, round(v * scale)) for v in image.size)
        rgb = np.asarray(geometry_rgb(image).resize(size, Image.Resampling.LANCZOS))
        mask = np.asarray(Image.fromarray(observation.mask).resize(size, Image.Resampling.NEAREST), dtype=bool)
        fits = [('geometric_footprint', mesh, fit_camera(mesh, mask, view=label, max_evaluations=camera_evaluations))]
        # A separate optical-visibility hypothesis is necessary for clear lenses.
        # It is chosen from the same input and therefore is never validation.
        if opaque is not None:
            fits.append(('declared_opaque_parts', opaque, fit_camera(opaque, mask, view=label, max_evaluations=camera_evaluations)))
        name, visible_mesh, fit = min(fits, key=lambda item: item[2]['final_loss'])
        camera = Camera(**fit['camera'])
        raster = rasterize(visible_mesh, camera, mask.shape)
        sampled = contour_samples(visible_mesh, raster)
        if len(sampled['xy']) < 12:
            summary['reason'] = 'Too few geometric boundary samples'
            continue
        camera_hash = hashlib.sha256(json.dumps(camera.to_dict(), sort_keys=True).encode()).hexdigest()
        matches = match_contour_edges(rgb, sampled['xy'], sampled['normals'], source_sha256=summary['source_sha256'],
                                      model_sha256=model_hash, camera_sha256=camera_hash)
        write_json(folder / 'edge-hypotheses.json', matches.to_report())
        selected = matches.selected_mask
        summary.update(camera_fit=fit, camera_hypothesis=name,
                       camera_alternatives=[{'geometry': label, 'fit': result} for label, _, result in fits],
                       image_size_original=original_size.tolist(), image_size_working=list(size),
                       working_rgb_sha256=hashlib.sha256(rgb.tobytes()).hexdigest(),
                       edge_status_counts={s: matches.status.count(s) for s in sorted(set(matches.status))},
                       selected_edge_count=int(selected.sum()))
        if selected.sum() < 12:
            summary['reason'] = 'Too few unambiguous edge proposals; no silent fallback to guessed coordinates'
            continue
        # Preserve evidence in original pixel coordinates, recording the working
        # grid and exact center-coordinate conversion separately with its camera.
        ratio = original_size / np.asarray(size)
        native_xy = (matches.matched_xy[selected] + .5) * ratio - .5
        native_sigma = matches.sigma_px[selected, None] * ratio[None]
        provenance = EvidenceProvenance('automatic', 'candidate_guided_rgb_edge_v1', summary['source_sha256'],
            'Candidate-dependent edge identity; heuristic uncertainty, not semantic or statistical calibration.')
        normals = sampled['normals'][selected]
        native_normals = normals / ratio[None]
        normal_scale = np.linalg.norm(native_normals, axis=1)
        native_normals /= normal_scale[:, None]
        native_normal_sigma = matches.sigma_px[selected] / normal_scale
        edges = tuple(EdgeEvidence(f'edge_{int(index)}', ImagePoint(*xy, *sigma), tuple(normal), float(normal_sigma),
                                   float(matches.confidence[index]), provenance)
                      for index, xy, sigma, normal, normal_sigma in zip(np.flatnonzero(selected), native_xy,
                          native_sigma, native_normals, native_normal_sigma))
        evidence = PhotoEvidence(SourceImage(summary['source_sha256'], *map(int, original_size), view),
            (ComponentEvidence('image_edges', 'other', 'present', float(matches.confidence[selected].mean()),
                               provenance, Coverage(None, None, None, 'Coverage and semantic identity unmeasured.'),
                               edges=edges, notes='Measured image edges near an initial geometric contour; tangent position and glasses identity are not established.'),))
        evidence_json = evidence.to_dict()
        write_json(folder / 'evidence.json', evidence_json)
        summary.update(status='candidate_dependent_correspondences', evidence_sha256=evidence_json['evidence_sha256'])
        constraints.append(ReprojectionConstraint(view, summary['source_sha256'], evidence_json['evidence_sha256'], camera,
            sampled['xyz'][selected], matches.matched_xy[selected], np.repeat(matches.sigma_px[selected, None], 2, axis=1),
            normal_xy=normals))
        view_data.append((summary, visible_mesh, camera, rgb, raster, matches.matched_xy[selected]))
        print(f'{view}: {int(selected.sum())}/{len(selected)} edge hypotheses selected; camera geometry {name}', flush=True)
    if len(constraints) < 2:
        assert_sources_unchanged(model, model_hash, photos, photo_hashes)
        report.update(status='insufficient_multiview_correspondences', reason='At least two usable views are required for a 3D shape proposal')
        write_json(output / 'report.json', report)
        return report
    print('Fitting one shared cage with source-surface similarity gauge', flush=True)
    report['view_geometry'] = view_geometry_diagnostics(constraints)
    assert_sources_unchanged(model, model_hash, photos, photo_hashes)
    write_json(output / 'frozen-correspondences.json', {
        'schema_version': 1, 'source_model_sha256': model_hash,
        'normalization': report['normalization'],
        'scope': 'candidate-dependent fitting evidence, not independent evaluation',
        'views': [{'view_id': c.view_id, 'source_sha256': c.source_sha256, 'evidence_sha256': c.evidence_sha256,
                   'camera': c.camera.to_dict(), 'points_xyz': c.points_xyz.tolist(),
                   'targets_xy': c.targets_xy.tolist(), 'sigma_xy': c.sigma_xy.tolist(),
                   'normal_xy': c.normal_xy.tolist(), 'tangent_unconstrained': True} for c in constraints]})
    if report['view_geometry']['essentially_parallel_axes']:
        report.update(status='insufficient_angular_diversity', reason='Fitted viewing axes supply essentially no depth baseline')
        write_json(output / 'report.json', report)
        return report
    cage, fit_report = fit_cage(constraints, mesh.vertices.min(axis=0) - .00001, mesh.vertices.max(axis=0) + .00001,
                               gauge_reference=surface_reference(mesh))
    field = NormalizedField(cage, center, extent, model_hash)
    report['deformation_fit'] = fit_report
    write_json(output / 'optimization-field.json', field.to_dict())
    exported_mesh = None
    if not fit_report['retained']:
        report['status'] = 'no_supported_shared_deformation'
    else:
        selected, export, backtracking = export_backtracked(model, output / 'proposal.glb', cage, center, extent,
                                                           model_hash, constraints)
        report['export_backtracking'] = backtracking
        if selected is None:
            report['status'] = 'export_rejected'
        else:
            cage = selected
            field = NormalizedField(cage, center, extent, model_hash)
            write_json(output / 'field.json', field.to_dict())
            report['export'] = export
            report['selected_field'] = {'gradient_bound': cage.gradient_bound(), 'file_sha256': digest(output / 'field.json'),
                                        'maximum_control_displacement_units': float(np.max(np.linalg.norm(cage.displacements, axis=-1)))}
        try:
            exported = load_glb(output / 'proposal.glb') if selected is not None else None
            # Keep the original coordinate frame. Re-normalizing an edited
            # bounding box would conceal unwanted scale or translation.
            if exported is not None:
                exported_mesh = TriangleMesh((exported.vertices - center) / extent, exported.faces, exported.parts)
                report['status'] = 'proposal_exported'
        except ValueError as error:
            report.update(status='export_rejected', export_error=str(error))
            (output / 'proposal.glb').replace(output / 'rejected-proposal.glb')
            report['rejected_artifact'] = 'rejected-proposal.glb'
    # Recompute visible contours after the proposal. Old silhouette points do
    # not remain the actual silhouette when the shape changes.
    passes = []
    for summary, visible_mesh, camera, rgb, before, targets in view_data:
        if exported_mesh is not None:
            moved = opaque_partition(exported_mesh) if summary['camera_hypothesis'] == 'declared_opaque_parts' else exported_mesh
            if moved is None:
                raise ValueError('Export changed the selected material partition')
            geometry_source = 'exported_glb_float32'
        else:
            moved = TriangleMesh(cage.transform(visible_mesh.vertices)[0], visible_mesh.faces, visible_mesh.parts)
            geometry_source = 'unexported_float64_proposal'
        after = rasterize(moved, camera, before.mask.shape)
        before_score, after_score = boundary_distance(before, targets), boundary_distance(after, targets)
        nonregression = (before_score['measured'] and after_score['measured'] and
                         after_score['mean_px'] <= before_score['mean_px'] + .25 and
                         after_score['p95_px'] <= before_score['p95_px'] + .25)
        summary['rerender'] = {'before': before_score, 'proposal': after_score, 'nonregression': nonregression,
                               'geometry_source': geometry_source,
                               'sampling_tolerance_working_px': .25, 'independent_validation': False}
        passes.append(nonregression)
        compare_image(rgb, before, after, targets, output / summary['view_id'] / 'comparison.png')
    report['rerender_nonregression'] = all(passes)
    if report['status'] == 'proposal_exported' and not all(passes):
        report['status'] = 'proposal_rejected_after_rerender'
        (output / 'proposal.glb').replace(output / 'rejected-proposal.glb')
        report['rejected_artifact'] = 'rejected-proposal.glb'
    elif report['status'] == 'proposal_exported':
        report['proposal_artifact'] = 'proposal.glb'
    assert_sources_unchanged(model, model_hash, photos, photo_hashes)
    write_json(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--photo', action='append', required=True, help='[id:]front|back|left|right|angled|unknown=path')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resolution', type=int, default=320)
    parser.add_argument('--camera-evaluations', type=int, default=160)
    parser.add_argument('--rigid-refinement', action='store_true', help='Explicit legacy rigid-camera/shared-cage comparison')
    args = parser.parse_args()
    photos = []
    for item in args.photo:
        identity, separator, filename = item.partition('=')
        name, colon, view = identity.partition(':')
        if not colon:
            view = name
        if separator != '=' or view not in ('front', 'back', 'left', 'right', 'angled', 'unknown'):
            parser.error('Photos require a supported view=path label')
        photos.append(PhotoInput(name, Path(filename), view))
    if not 128 <= args.resolution <= 768 or not 20 <= args.camera_evaluations <= 1000:
        parser.error('Use resolution 128–768 and camera evaluations 20–1000')
    report = run(args.model, photos, args.output, resolution=args.resolution, camera_evaluations=args.camera_evaluations,
                 automatic_articulation=not args.rigid_refinement)
    print(json.dumps({'status': report['status'], 'quality_verdict': report['quality_verdict'],
                      'views': [(row['view_id'], row['status']) for row in report['views']]}), flush=True)


if __name__ == '__main__':
    main()
