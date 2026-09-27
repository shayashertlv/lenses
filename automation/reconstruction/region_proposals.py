"""Automatic image regions, with candidate-conditioned identity kept explicit.

SAM masks and prompt stability are hypotheses, never verified component labels.
All decoder alternatives survive; measured photograph colors are not converted
to transmission, absorption, mirror reflectance or an accepted lens material.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .camera import Camera
from .mesh import TriangleMesh, load_glb
from .observations import observe_image
from .photo_appearance import measure_region_appearance
from .raster import rasterize


@dataclass(frozen=True)
class RegionPolicy:
    version: str = 'candidate_conditioned_regions_v1'
    minimum_projected_component_pixels: int = 4
    maximum_lens_regions: int = 12
    bbox_scale_perturbation: float = .06
    bbox_shift_perturbation: float = .04
    stable_iou_threshold: float = .85


POLICY = RegionPolicy()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def _bbox(mask):
    rows, cols = np.nonzero(mask)
    return [int(cols.min()), int(rows.min()), int(cols.max()) + 1, int(rows.max()) + 1] if len(rows) else None


def _iou(left, right):
    union = np.count_nonzero(left | right)
    # Empty masks supply no stability evidence.
    return float(np.count_nonzero(left & right) / union) if union else 0.


def _save_mask(folder, name, mask):
    path = folder / name
    Image.fromarray(mask.astype(np.uint8) * 255).save(path)
    return {'path': name, 'sha256': _sha(path), 'pixels': int(mask.sum()), 'bbox_xyxy': _bbox(mask)}


def _prompts(mask):
    box = np.asarray(_bbox(mask), dtype=float)
    if box.shape != (4,):
        raise ValueError('Cannot prompt an empty region')
    h, w = mask.shape
    span, center = box[2:] - box[:2], (box[2:] + box[:2]) / 2
    variants = [(1., 0.), (1. + POLICY.bbox_scale_perturbation, 0.),
                (1. - POLICY.bbox_scale_perturbation, 0.),
                (1., -POLICY.bbox_shift_perturbation), (1., POLICY.bbox_shift_perturbation)]
    result = []
    for scale, shift in variants:
        half = np.maximum(.5, span * scale / 2)
        middle = center + np.array([shift * span[0], 0.])
        lower, upper = np.maximum(0., middle - half), np.minimum([w, h], middle + half)
        # At a clipped image edge preserve an at least one-pixel box.
        lower = np.minimum(lower, upper - 1.)
        result.append({'bbox_xyxy': [*lower.tolist(), *upper.tolist()]})
    return result


def _native_grid(field, shape):
    """Nearest working pixel under the recorded pixel-center resize transform."""
    height, width = shape
    rows = np.minimum(field.shape[0] - 1, ((np.arange(height) + .5) * field.shape[0] / height).astype(int))
    cols = np.minimum(field.shape[1] - 1, ((np.arange(width) + .5) * field.shape[1] / width).astype(int))
    return field[rows[:, None], cols[None, :]]


def _lens_parts(mesh):
    selected, ledger = [], []
    for index, part in enumerate(mesh.parts):
        role = str(part.get('declared_role') or '').lower()
        label = f"{part.get('name', '')} {part.get('material', '')}".lower()
        if role in ('lens', 'lenses', 'optical'):
            basis = 'candidate_declared_role'
        elif part.get('has_lens_appearance_extension'):
            basis = 'candidate_lens_extension_presence_unvalidated'
        elif part.get('transmission', 0) > 0:
            basis = 'candidate_transmission_material_heuristic'
        elif re.search(r'(^|[^a-z])(lens(?:es)?|optical)([^a-z]|$)', label):
            basis = 'candidate_name_heuristic'
        else:
            basis = None
        ledger.append({'part_index': index, **part, 'optical_region_prior_basis': basis,
                       'photographic_component_identity': 'unverified'})
        if basis:
            selected.append(index)
    return selected, ledger


def _camera_regions(mesh, model_hash, refinement, photo, shape):
    """No camera or semantic identity is silently invented when a prior is missing."""
    if mesh is None or not refinement:
        return [], {'status': 'no_candidate_camera'}
    views = [v for v in refinement.get('views', []) if v.get('view_id') == photo['id']]
    if len(views) > 1:
        raise ValueError('Duplicate camera view identity')
    if not views or 'camera_fit' not in views[0]:
        return [], {'status': 'no_fitted_camera_for_photo'}
    view = views[0]
    if view.get('source_sha256') != photo['sha256']:
        raise ValueError('Camera is bound to a different photograph')
    original_binding = refinement.get('source_sha256') == model_hash
    exported_binding = (refinement.get('status') == 'proposal_exported' and
                        refinement.get('rerender_nonregression') is True and
                        refinement.get('export', {}).get('output_sha256') == model_hash)
    if not (original_binding or exported_binding):
        raise ValueError('Camera report is bound to a different candidate model')
    if view.get('image_size_original') != [shape[1], shape[0]]:
        raise ValueError('Camera original pixel grid differs from photograph')
    size = view.get('image_size_working')
    if (not isinstance(size, list) or len(size) != 2 or
            any(type(v) is not int or not 2 <= v <= 768 for v in size)):
        raise ValueError('Invalid or unsupported working camera grid')
    center = np.asarray(refinement['normalization']['center'], dtype=float)
    extent = refinement['normalization']['extent']
    if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(extent) or extent <= 0:
        raise ValueError('Invalid camera normalization')
    observed_mesh, articulation = mesh, None
    if refinement.get('view_scene') is not None:
        from .view_scene import pose_mesh_from_contract
        posed = pose_mesh_from_contract(mesh, refinement['view_scene'], photo['id'], photo_sha256=photo['sha256'])
        observed_mesh, articulation = posed['mesh'], posed['report']
    normalized = TriangleMesh((observed_mesh.vertices - center) / extent, observed_mesh.faces, observed_mesh.parts)
    camera = Camera(**view['camera_fit']['camera'])
    if not np.isfinite(list(camera.to_dict().values())).all() or camera.scale <= 0 or camera.perspective < 0:
        raise ValueError('Invalid camera parameters')
    raster = rasterize(normalized, camera, (size[1], size[0]))
    selected, ledger = _lens_parts(mesh)
    selected_faces = np.zeros(len(mesh.faces), dtype=bool)
    for index in selected:
        part = mesh.parts[index]
        selected_faces[part['face_start']:part['face_start'] + part['face_count']] = True
    visible = raster.mask & selected_faces[np.maximum(0, raster.face_index)]
    components, count = ndimage.label(visible, structure=np.ones((3, 3), dtype=bool))
    sizes = np.bincount(components.ravel(), minlength=count + 1)
    component_ids = sorted(range(1, count + 1), key=lambda i: (-int(sizes[i]), i))
    kept = [i for i in component_ids if sizes[i] >= POLICY.minimum_projected_component_pixels][:POLICY.maximum_lens_regions]
    camera_pin = _digest(camera.to_dict())
    metadata = {'status': 'candidate_conditioned_projection', 'candidate_sha256': model_hash,
                'camera_sha256': camera_pin, 'camera': camera.to_dict(), 'working_size': size,
                'candidate_binding': 'original' if original_binding else 'exported_proposal_original_camera',
                'normalization': refinement['normalization'], 'parts': ledger,
                'optical_components_projected': count, 'optical_regions_retained': len(kept),
                'optical_components_omitted': [{'component': i, 'working_pixels': int(sizes[i]),
                    'reason': 'below_pixel_support_or_region_capacity'} for i in component_ids if i not in kept],
                'occlusion_model': 'frontmost opaque geometry footprint; physical lens visibility unknown',
                'coordinate_resampling': 'native pixel center -> nearest working pixel; quantization retained',
                'camera_identification': 'unmeasured', 'semantic_identity': 'unverified'}
    if articulation is not None:
        metadata['view_scene'] = {'contract': refinement['view_scene'], 'view_id': photo['id'],
                                  'source_image_sha256': photo['sha256'], 'projection': articulation}
    regions = []
    for region_number, component in enumerate(kept):
        mask = components == component
        rows, cols = np.nonzero(mask)
        faces = raster.face_index[rows, cols]
        xyz = raster.surface_points(normalized, rows, cols)
        part_ids = [i for i in selected if np.any((faces >= mesh.parts[i]['face_start']) &
                    (faces < mesh.parts[i]['face_start'] + mesh.parts[i]['face_count']))]
        # Height belongs to the authored candidate, not the image row. This is
        # explicitly a proxy, not recovered intrinsic UV or a gradient estimate.
        part_vertices = np.unique(np.concatenate([normalized.faces[p['face_start']:p['face_start'] + p['face_count']].ravel()
                                                 for p in (mesh.parts[i] for i in part_ids)]))
        local_y = normalized.vertices[part_vertices, 1]
        height = np.full(mask.shape, np.nan)
        if np.ptp(local_y) > 1e-12:
            height[rows, cols] = np.clip((xyz[:, 1] - local_y.min()) / np.ptp(local_y), 0., 1.)
        triangle = normalized.vertices[normalized.faces[faces]]
        normal = np.cross(triangle[:, 1] - triangle[:, 0], triangle[:, 2] - triangle[:, 0])
        norm = np.linalg.norm(normal, axis=1)
        yaw, pitch = np.radians([camera.yaw, camera.pitch])
        toward = np.array([np.cos(pitch) * np.sin(yaw), np.sin(pitch), np.cos(pitch) * np.cos(yaw)])
        direction = np.broadcast_to(toward, xyz.shape).copy() if camera.perspective == 0 else toward / camera.perspective - xyz
        direction /= np.linalg.norm(direction, axis=1)[:, None]
        valid = norm > 1e-12
        angle = np.full(mask.shape, np.nan)
        angle[rows[valid], cols[valid]] = np.degrees(np.arccos(np.clip(np.sum(normal[valid] / norm[valid, None] * direction[valid], axis=1), -1., 1.)))
        provenance = {'source': 'candidate', 'candidate_sha256': model_hash,
                      'camera_sha256': camera_pin, 'part_indices': part_ids,
                      'method': 'normalized_authored_y_height_proxy_and_triangle_normal_incidence',
                      'intrinsic_coordinate_identity': 'unverified_height_proxy',
                      'resampling': metadata['coordinate_resampling'], 'working_size': size}
        regions.append({'id': f'optical-prior-{region_number:02d}', 'kind': 'candidate_optical_region',
                        'mask': _native_grid(mask, shape), 'height': _native_grid(height, shape),
                        'incidence': _native_grid(angle, shape), 'coordinate_provenance': provenance,
                        'prior': {'basis': 'frontmost_candidate_lens_component', 'component': component,
                                  'part_indices': part_ids, 'semantic_identity': 'unverified'}})
    return regions, metadata


def _checked_predictions(engine, rgb, prompts):
    predictions = engine.predict(rgb, prompts)
    if not isinstance(predictions, list) or len(predictions) != len(prompts):
        raise ValueError('Region engine changed prompt count')
    for alternatives in predictions:
        if not isinstance(alternatives, list) or len(alternatives) != 3:
            raise ValueError('Region engine must preserve all three alternatives')
        for item in alternatives:
            mask, score = item.get('mask'), item.get('predicted_quality')
            if not isinstance(mask, np.ndarray) or mask.dtype != bool or mask.shape != rgb.shape[:2]:
                raise ValueError('Region engine returned an invalid native mask')
            if isinstance(score, (bool, np.bool_)) or not isinstance(score, (int, float, np.number)) or not np.isfinite(score) or not 0 <= score <= 1:
                raise ValueError('Invalid model predicted quality')
    return predictions


def _region_result(region, predictions, pixels, source_hash, folder):
    prior = region['mask']
    result = {'id': region['id'], 'kind': region['kind'], 'identity': 'unverified', 'prior': region['prior'],
              'prior_mask': _save_mask(folder, 'prior.png', prior), 'prompts': _prompts(prior),
              'coordinate_fields': region.get('coordinate_provenance'), 'alternatives': [], 'hypotheses': [],
              'selected_hypothesis': None, 'semantic_coverage': 'unmeasured'}
    if predictions is None:
        result['status'] = 'engine_unavailable'
        return result
    for variant, alternatives in enumerate(predictions):
        result['alternatives'].append([{'decoder_index': i, 'predicted_quality': float(item['predicted_quality']),
            'mask': _save_mask(folder, f'variant-{variant}-alternative-{i}.png', item['mask'])}
            for i, item in enumerate(alternatives)])
    for index, baseline in enumerate(predictions[0]):
        mask = baseline['mask']
        matches, masks, overlaps, ambiguous = [[index]], [mask], [], []
        for variants in predictions[1:]:
            values = [_iou(mask, item['mask']) for item in variants]
            best_value = max(values)
            best = [i for i, value in enumerate(values) if abs(value - best_value) <= 1e-12]
            matches.append(best)
            masks.extend(variants[i]['mask'] for i in best)
            overlaps.append(best_value)
            ambiguous.append(any(not np.array_equal(variants[best[0]]['mask'], variants[i]['mask']) for i in best[1:]))
        core, union = np.logical_and.reduce(masks), np.logical_or.reduce(masks)
        diagnostic = {'minimum_matched_iou': min(overlaps), 'matched_decoder_indices': matches,
                      'stable_under_prompts': bool(mask.any() and min(overlaps) >= POLICY.stable_iou_threshold),
                      'association_ambiguous': any(ambiguous),
                      'association_rule': 'all co-best IoU matches within absolute 1e-12; duplicates retained',
                      'prior_overlap_iou': _iou(mask, prior),
                      'touches_image_border': bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any()),
                      'image_fraction': float(mask.mean()),
                      'scope': 'prompt/initialization sensitivity, not semantic confidence'}
        provenance = {'method': POLICY.version, 'hypothesis': index, 'region_prior': region['prior'],
                      'mask_identity': 'unverified', 'support': 'intersection_of_all_cobest_iou_prompt_alternatives'}
        if region.get('coordinate_provenance'):
            provenance['coordinate_fields'] = region['coordinate_provenance']
        appearance = measure_region_appearance(pixels, core, source_sha256=source_hash,
            region_id=f"{region['id']}/hypothesis-{index}", provenance=provenance,
            intrinsic_v=region.get('height'), incidence_degrees=region.get('incidence'))
        appearance_name = f'hypothesis-{index}-appearance.json'
        _write(folder / appearance_name, appearance)
        result['hypotheses'].append({'index': index, 'diagnostics': diagnostic,
            'intersection': _save_mask(folder, f'hypothesis-{index}-intersection.png', core),
            'union': _save_mask(folder, f'hypothesis-{index}-union.png', union),
            'appearance': {'path': appearance_name, 'sha256': _sha(folder / appearance_name),
                           'interpretation': 'conditional photographed signal; no identified optical material'}})
    result['status'] = 'alternative_region_hypotheses'
    return result


def run_region_stage(photos: list[dict], output: Path, *, engine=None, model: Path | None = None,
                     refinement_report: dict | None = None) -> dict:
    """Run one frozen policy over normalized images; output must be new/empty."""
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Region output must be empty; preserve previous hypotheses')
    if not photos:
        raise ValueError('At least one photo is required')
    ids = [photo.get('id') for photo in photos]
    if any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Photo identities must be unique safe names')
    for photo in photos:
        if _sha(photo['path']) != photo['sha256']:
            raise ValueError('Photo source hash mismatch')
    model_hash = _sha(model) if model else None
    mesh = load_glb(model) if model and refinement_report else None
    engine_pin = json.loads(json.dumps(engine.describe() if engine is not None else None, allow_nan=False))
    refinement_pin = _digest(refinement_report)
    report = {'schema_version': 1, 'method': POLICY.version, 'policy': asdict(POLICY),
              'status': 'region_hypotheses_available' if engine is not None else 'engine_unavailable',
              'quality_verdict': 'unmeasured', 'accepted': False, 'engine': engine_pin,
              'candidate_sha256': model_hash, 'refinement_report_sha256': refinement_pin, 'photos': [],
              'limitations': ['Candidate labels and SAM regions do not establish photographic component identity.',
                  'All decoder alternatives are preserved; none is automatically accepted as the true lens.',
                  'Signal gradients can arise from illumination, reflection, background, exposure or absorption.',
                  'Observed RGB is not mirror reflectance or lens transmission; photographic calibration is unknown.',
                  'Missing regions are unknown, never verified absence. No hidden surfaces are recovered.',
                  'Only frontmost candidate footprints are proposed; frame partitions and articulation are unresolved.']}
    output.mkdir(parents=True, exist_ok=True)
    for photo in photos:
        folder = output / photo['id']
        folder.mkdir()
        with Image.open(photo['path']) as image:
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Region stage requires a normalized single-frame image')
            if image.getexif().get(274, 1) != 1:
                raise ValueError('Normalize EXIF orientation and pin the resulting image before region proposals')
            if image.info.get('icc_profile') or image.mode not in ('RGB', 'RGBA'):
                raise ValueError('Normalize embedded ICC profiles and non-RGB images through input_bundle before region proposals')
            pixels = np.asarray(image.convert('RGBA')).copy()
        print(f"Proposing regions: {photo['id']}", flush=True)
        observation = observe_image(pixels)
        regions, geometry = _camera_regions(mesh, model_hash, refinement_report, photo, pixels.shape[:2])
        if observation.usable_mask is not None:
            regions.insert(0, {'id': 'contrast-object', 'kind': 'contrast_object_region', 'mask': observation.mask,
                               'prior': {'basis': observation.method, 'semantic_identity': 'unknown_object'}})
        record = {'id': photo['id'], 'source': str(Path(photo['path']).resolve()), 'source_sha256': photo['sha256'],
                  'view_prior': photo.get('view', 'unknown'), 'image_size': [pixels.shape[1], pixels.shape[0]],
                  'coordinate_system': 'pinned decoded image pixel centers; bbox upper bounds exclusive',
                  'color_encoding': photo.get('color_space', 'untagged_assumed_srgb_uncalibrated'),
                  'normalization_provenance': photo.get('normalization_provenance'),
                  'contrast': observation.to_report(), 'candidate_projection': geometry, 'regions': [],
                  'status': 'no_supported_region_prior' if not regions else 'region_priors_available'}
        report['photos'].append(record)
        # Nonopaque pixels are not composited over an invented background for SAM.
        supported = bool(np.all(pixels[:, :, 3] == 255))
        predictions = None
        if engine is not None and regions and supported:
            prompts = [prompt for region in regions for prompt in _prompts(region['mask'])]
            predictions = _checked_predictions(engine, pixels[:, :, :3].copy(), prompts)
            record['status'] = 'alternative_region_hypotheses'
        elif not supported:
            record['status'] = 'nonopaque_input_region_inference_unsupported'
        elif regions and engine is None:
            record['status'] = 'engine_unavailable'
        overlay = Image.fromarray(pixels[:, :, :3]).convert('RGB')
        draw = ImageDraw.Draw(overlay)
        for index, region in enumerate(regions):
            region_folder = folder / region['id']
            region_folder.mkdir()
            result = _region_result(region, predictions[index*5:index*5+5] if predictions is not None else None,
                                    pixels, photo['sha256'], region_folder)
            result['directory'] = region['id']
            if not supported:
                result['status'] = 'nonopaque_input_region_inference_unsupported'
            record['regions'].append(result)
            box = _bbox(region['mask'])
            draw.rectangle([box[0], box[1], box[2]-1, box[3]-1], outline=(230, 65, 20), width=2)
            draw.text((box[0]+2, box[1]+2), region['id'], fill=(190, 0, 0))
        draw.text((4, 4), 'Candidate/contrast region PRIORS; identity unverified', fill=(190, 0, 0))
        overlay.save(folder / 'overlay.png')
        record['overlay'] = {'path': f"{photo['id']}/overlay.png", 'sha256': _sha(folder / 'overlay.png')}
        _write(folder / 'report.json', record)
    for photo in photos:
        if _sha(photo['path']) != photo['sha256']:
            raise ValueError('Photo changed during region inference')
    if model and _sha(model) != model_hash:
        raise ValueError('Candidate changed during region inference')
    if _digest(refinement_report) != refinement_pin:
        raise ValueError('Camera report changed during region inference')
    if engine is not None and engine.describe() != engine_pin:
        raise ValueError('Region engine provenance changed during inference')
    report['counts'] = {'photos': len(report['photos']),
                        'regions': sum(len(photo['regions']) for photo in report['photos']),
                        'hypotheses': sum(len(region['hypotheses']) for photo in report['photos'] for region in photo['regions'])}
    report['status'] = ('region_hypotheses_available' if report['counts']['hypotheses'] else
                        'engine_unavailable' if engine is None else 'no_supported_region_hypotheses')
    if engine is not None and callable(getattr(engine, 'receipt', None)):
        report['engine_runtime_receipt'] = engine.receipt()
    _write(output / 'report.json', report)
    return report
