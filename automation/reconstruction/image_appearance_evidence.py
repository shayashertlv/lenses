"""Image-only numerical appearance evidence from independently proposed apertures.

These measurements include source views without a usable 3D camera. They do not
invent lens UV, incidence angles, or optical group membership. Display-referred
transmission scenarios are explicitly separated from physical measurements.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from .lens_appearance import srgb_to_linear
from .photo_semantics import validate_product_hypotheses


METHOD = 'grounded_photo_appearance_evidence_v1'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def validate_image_appearance_evidence(value, image_manifest):
    """Check source identities and finite interval data before fitter propagation."""
    report = json.loads(json.dumps(value, allow_nan=False))
    sources = {r['photo_id']: r['sha256'] for r in image_manifest}
    if (report.get('method') != 'image_appearance_observations_v1'
            or set(report.get('source_image_sha256', [])) != set(sources.values())):
        raise ValueError('Image appearance evidence binds different source photos')
    rows = report.get('observations')
    if not isinstance(rows, list) or len(rows) > 12:
        raise ValueError('Image appearance observations must be bounded')
    for row in rows:
        if sources.get(row.get('photo_id')) != row.get('source_sha256'):
            raise ValueError('Image appearance observation source binding differs')
        if any(row.get(name) is not None for name in ('intrinsic_v', 'incidence_degrees', 'material_group_id')):
            raise ValueError('Image-only evidence cannot invent optical coordinates or groups')
        for name in ('linear_rgb_interval', 'backdrop_linear_rgb_interval'):
            if name in row:
                interval = np.asarray(row[name], float)
                if interval.shape != (3, 2) or not np.isfinite(interval).all() or np.any(interval < 0) or np.any(interval > 1) or np.any(interval[:, 0] > interval[:, 1]):
                    raise ValueError('Invalid image appearance interval')
        for scenario in row.get('display_referred_transmission_hypotheses', []):
            interval = np.asarray(scenario['transmission_interval_rgb'], float)
            if (interval.shape != (3, 2) or not np.isfinite(interval).all() or np.any(interval < 0)
                    or np.any(interval > 1) or np.any(interval[:, 0] > interval[:, 1])
                    or scenario.get('physical_identification') is not False
                    or scenario.get('status') != 'conditional_scenario_not_measurement'):
                raise ValueError('Transmission scenarios must be finite and explicitly conditional')
    return report


def _read_image(entry):
    raw = Path(entry['local_path']).read_bytes()
    if _sha(raw) != entry['sha256']:
        raise ValueError('Grounding source image bytes changed')
    with Image.open(io.BytesIO(raw)) as image:
        rgba = np.asarray(image.convert('RGBA')).copy()
    if list(rgba.shape[1::-1]) != entry['image_size'] or _sha(rgba.tobytes()) != entry['pixel_sha256']:
        raise ValueError('Grounding image pixels/grid changed')
    return rgba


def _box_mask(shape, box):
    h, w = shape
    y, x = np.ogrid[:h, :w]
    return (((x + .5) / w >= box[0]) & ((x + .5) / w < box[2]) &
            ((y + .5) / h >= box[1]) & ((y + .5) / h < box[3]))


def ground_semantic_regions(hypotheses, aperture_engine, output=None, *, erosion_pixels=3,
                           maximum_edge_codes_per_pixel=8., minimum_pixels=32):
    """Intersect clean box proposals with all lens-head aperture alternatives.

    The engine is explicitly supplied (for example OfflineLensApertureEngine).
    Full-image and crop masks are retained; their conservative intersection is
    used for color sampling. SAM/detector confidence never becomes identity.
    Strong image edges and proposed reflection regions are excluded. This does
    not establish that smooth rear content or faint reflections are absent.
    """
    semantic = validate_product_hypotheses(hypotheses, hypotheses['image_manifest'])
    if type(erosion_pixels) is not int or not 0 <= erosion_pixels <= 20:
        raise ValueError('Grounding erosion must be 0..20 pixels')
    if not 0 < maximum_edge_codes_per_pixel <= 255 or type(minimum_pixels) is not int or minimum_pixels < 4:
        raise ValueError('Invalid grounding thresholds')
    engine_description = aperture_engine.describe()
    policy = {'erosion_pixels': erosion_pixels, 'maximum_edge_codes_per_pixel': maximum_edge_codes_per_pixel,
              'minimum_pixels': minimum_pixels, 'aperture_combination': 'intersection_of_every_known_positive_alternative',
              'fit_support_version':2}
    recipe = {'method': METHOD, 'manifest': semantic['image_manifest'],
              'regions': semantic['regions'], 'engine': engine_description, 'policy': policy}
    recipe_hash = _hash(recipe)
    folder = Path(output).resolve() if output is not None else None
    cache_report = folder / 'grounding.json' if folder is not None else None
    if cache_report is not None and cache_report.exists():
        report = json.loads(cache_report.read_bytes())
        if report.get('request_sha256') != recipe_hash:
            raise ValueError('Grounding cache belongs to different images, regions, engine or policy')
        masks = {}
        for row in report['regions']:
            path = folder / row['mask']['path']
            if path.parent != folder or _sha(path.read_bytes()) != row['mask']['sha256']:
                raise ValueError('Cached grounding mask changed')
            with Image.open(path) as image:
                mask = np.asarray(image.convert('L')) > 0
            if int(mask.sum()) != row['support_pixels']:
                raise ValueError('Cached grounding mask support changed')
            masks[row['region_id']] = mask
        images = {r['photo_id']: _read_image(r) for r in semantic['image_manifest']
                  if any(v['photo_id'] == r['photo_id'] for v in report['photos'])}
        fit_masks, aperture_masks = {}, {}
        for row in report['photos']:
            for key,target in (('fit_support',fit_masks),('aperture_support',aperture_masks)):
                path=folder/row[key]['path']
                if path.parent!=folder or _sha(path.read_bytes())!=row[key]['sha256']:
                    raise ValueError('Cached grounding support mask changed')
                with Image.open(path) as image:
                    target[row['photo_id']]=np.asarray(image.convert('L'))>0
        return {'report': report, 'masks': masks, 'images': images,'fit_masks':fit_masks,'aperture_masks':aperture_masks}
    if folder is not None:
        folder.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'method': METHOD, 'request_sha256': recipe_hash, 'accepted': False,
              'engine': engine_description, 'policy': policy, 'photos': [], 'regions': [],
              'limitations': ['Aperture masks, semantic labels and clean boxes remain independent hypotheses.',
                             'Smooth rear objects and subtle reflections may survive edge exclusion.']}
    images, masks, fit_masks, aperture_masks = {}, {}, {}, {}
    clean = [r for r in semantic['regions'] if r['kind'] == 'clean_sampling']
    for entry in semantic['image_manifest']:
        regions = [r for r in clean if r['source_sha256'] == entry['sha256']]
        rgba = _read_image(entry)
        images[entry['photo_id']] = rgba
        proposal = aperture_engine.propose(rgba[:, :, :3])
        if proposal.get('size_xy') != entry['image_size']:
            raise ValueError('Aperture proposal grid differs from the source photo')
        variants = proposal.get('variants', {})
        if not isinstance(variants, dict) or not 1 <= len(variants) <= 8:
            raise ValueError('Aperture engine needs bounded alternatives')
        photo_record = {'photo_id': entry['photo_id'], 'source_sha256': entry['sha256'],
                        'image_size': entry['image_size'], 'alternatives': []}
        proposals = []
        for name, variant in sorted(variants.items()):
            mask, known = np.asarray(variant['mask']), np.asarray(variant['known_domain'])
            if mask.dtype != bool or known.dtype != bool or mask.shape != rgba.shape[:2] or known.shape != mask.shape:
                raise ValueError('Aperture masks need boolean source-sized grids')
            proposals.append(mask & known)
            photo_record['alternatives'].append({'name': name, 'positive_pixels': int((mask & known).sum()),
                'known_pixels': int(known.sum()), 'mask_sha256': _sha(np.packbits(mask & known).tobytes())})
        common = np.logical_and.reduce(proposals) & (rgba[:, :, 3] == 255)
        aperture_masks[entry['photo_id']]=common.copy()
        photo_record['aperture_consensus_pixels'] = int(common.sum())
        if erosion_pixels:
            common = ndimage.binary_erosion(common, iterations=erosion_pixels)
        # Derivative scale is in input codes/pixel. It is independent of a
        # region's color and does not censor an authentic smooth gradient.
        rgb = rgba[:, :, :3].astype(float)
        dx = np.stack([ndimage.sobel(rgb[:, :, ch], axis=1, mode='nearest') / 8. for ch in range(3)], axis=2)
        dy = np.stack([ndimage.sobel(rgb[:, :, ch], axis=0, mode='nearest') / 8. for ch in range(3)], axis=2)
        edge = np.max(np.hypot(dx, dy), axis=2) > maximum_edge_codes_per_pixel
        edge = ndimage.binary_dilation(edge, iterations=2)
        reflection = np.zeros(common.shape, bool)
        for region in semantic['regions']:
            if region['source_sha256'] == entry['sha256'] and region['kind'] == 'reflection':
                reflection |= _box_mask(common.shape, region['bbox_xyxy_normalized'])
        fit_masks[entry['photo_id']]=common & ~edge
        photo_record['fit_support_pixels']=int(fit_masks[entry['photo_id']].sum())
        if folder is not None:
            for key,mask in (('fit_support',fit_masks[entry['photo_id']]),('aperture_support',aperture_masks[entry['photo_id']])):
                path=folder/f'photo-{len(report["photos"]):03d}-{key}.png'
                Image.fromarray(mask.astype(np.uint8)*255).save(path)
                photo_record[key]={'path':path.name,'sha256':_sha(path.read_bytes())}
        report['photos'].append(photo_record)
        for region in regions:
            box = _box_mask(common.shape, region['bbox_xyxy_normalized'])
            grounded = box & common & ~reflection & ~edge
            if region['confidence'] == 'low':
                grounded[:] = False
            record = {'region_id': region['id'], 'photo_id': entry['photo_id'], 'source_sha256': entry['sha256'],
                      'bbox_xyxy_normalized': region['bbox_xyxy_normalized'],
                      'aperture_label': region['aperture_label'], 'semantic_lens_height': region['lens_height'],
                      'box_pixels': int(box.sum()), 'aperture_consensus_pixels': int((box & common).sum()),
                      'reflection_excluded_pixels': int((box & common & reflection).sum()),
                      'edge_excluded_pixels': int((box & common & edge & ~reflection).sum()),
                      'support_pixels': int(grounded.sum()),
                      'status': 'conditional_supported' if grounded.sum() >= minimum_pixels else 'unsupported',
                      'mask_numeric_sha256': _sha(np.packbits(grounded).tobytes())}
            masks[region['id']] = grounded
            if folder is not None:
                path = folder / f'region-{len(report["regions"]):03d}.png'
                Image.fromarray(grounded.astype(np.uint8) * 255).save(path)
                record['mask'] = {'path': path.name, 'sha256': _sha(path.read_bytes())}
            report['regions'].append(record)
    if cache_report is not None:
        cache_report.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return {'report': report, 'masks': masks, 'images': images,'fit_masks':fit_masks,'aperture_masks':aperture_masks}


def _code_interval(codes):
    values = np.asarray(codes, float)
    low = srgb_to_linear(np.clip((values - .5) / 255., 0, 1))
    high = srgb_to_linear(np.clip((values + .5) / 255., 0, 1))
    return low, high


def sample_image_appearance_evidence(hypotheses, grounding, *, view_labels=None):
    """Measured color distributions plus explicitly conditional T scenarios.

    A saturated white backdrop is not calibrated radiance. The resulting T
    scenarios use its decoded display value as a radiance proxy; they must not
    become physical equalities or normal-incidence coordinates in a fitter.
    """
    semantic = validate_product_hypotheses(hypotheses, hypotheses['image_manifest'])
    manifest = {r['photo_id']: r for r in semantic['image_manifest']}
    report = grounding['report']
    if report.get('method') != METHOD:
        raise ValueError('Unexpected image-grounding report')
    labels = view_labels or {}
    if not isinstance(labels, dict) or set(labels) - set(manifest) or any(
            label not in ('front', 'back', 'left', 'right', 'angled', 'unknown') for label in labels.values()):
        raise ValueError('View labels must bind known photos to explicit input view labels')
    results = []
    for row in report['regions']:
        entry = manifest[row['photo_id']]
        if entry['sha256'] != row['source_sha256']:
            raise ValueError('Grounded appearance source differs from semantic evidence')
        rgba = _read_image(entry)
        mask = grounding['masks'][row['region_id']]
        if mask.shape != rgba.shape[:2] or _sha(np.packbits(mask).tobytes()) != row['mask_numeric_sha256']:
            raise ValueError('Grounded appearance mask changed')
        result = {'region_id': row['region_id'], 'photo_id': row['photo_id'], 'source_sha256': row['source_sha256'],
                  'view_label': labels.get(row['photo_id'], 'unknown'), 'bbox_xyxy_normalized': row['bbox_xyxy_normalized'],
                  'grounded_mask_sha256': row['mask_numeric_sha256'], 'support_pixels': int(mask.sum()),
                  'status': row['status'], 'aperture_label': row['aperture_label'],
                  'intrinsic_v': None, 'incidence_degrees': None, 'material_group_id': None}
        if row['status'] != 'conditional_supported':
            result['reason'] = 'insufficient_grounded_source_pixels'
            results.append(result)
            continue
        codes = rgba[:, :, :3][mask]
        low, high = _code_interval(codes)
        lo, hi = np.quantile(low, .1, axis=0), np.quantile(high, .9, axis=0)
        border = np.zeros(mask.shape, bool)
        border[[0, -1], :] = True; border[:, [0, -1]] = True
        border &= rgba[:, :, 3] == 255
        bg_code = rgba[:, :, :3][border]
        result.update(code_rgb_p10=np.quantile(codes, .1, axis=0).tolist(),
                      code_rgb_median=np.median(codes, axis=0).tolist(),
                      code_rgb_p90=np.quantile(codes, .9, axis=0).tolist(),
                      linear_rgb_interval=np.column_stack((lo, hi)).tolist(),
                      clipped_black_fraction_rgb=np.mean(codes == 0, axis=0).tolist(),
                      clipped_white_fraction_rgb=np.mean(codes == 255, axis=0).tolist(),
                      physical_transmission_status='unidentified_without_calibrated_backdrop_and_reflection',
                      display_referred_transmission_hypotheses=[])
        if len(bg_code) < 8:
            result['backdrop_status'] = 'unsupported'
            results.append(result)
            continue
        bg_low, bg_high = _code_interval(bg_code)
        bg_lo, bg_hi = np.quantile(bg_low, .1, axis=0), np.quantile(bg_high, .9, axis=0)
        result['backdrop_linear_rgb_interval'] = np.column_stack((bg_lo, bg_hi)).tolist()
        result['backdrop_clipped_white_fraction_rgb'] = np.mean(bg_code == 255, axis=0).tolist()
        stable = np.all(bg_lo > .15) and np.all(bg_hi - bg_lo < .12)
        result['backdrop_status'] = 'stable_display_proxy' if stable else 'unsupported_nonuniform_or_dark'
        if stable:
            # Explore bounded additive reflection separately; a pink rear image
            # need not mean pink absorption, nor does a white lens prove T=1.
            for bound in (0., .1, .3):
                t_lo = np.maximum(0, lo / bg_hi - bound)
                t_hi = np.minimum(1, hi / bg_lo)
                if np.any(t_lo > t_hi):
                    continue
                result['display_referred_transmission_hypotheses'].append({
                    'transmission_interval_rgb': np.column_stack((t_lo, t_hi)).tolist(),
                    'maximum_additive_reflection_relative_to_backdrop': bound,
                    'status': 'conditional_scenario_not_measurement',
                    'assumptions': ['decoded_display_backdrop_equals_transmitted_radiance_proxy',
                                    'same_exposure_and_white_balance_throughout_photo',
                                    'bounded_additive_reflection_in_this_region',
                                    'semantic_clean_region_contains_only_lens_over_background'],
                    'physical_identification': False})
        results.append(result)
    return {'schema_version': 1, 'method': 'image_appearance_observations_v1', 'accepted': False,
            'source_image_sha256': [r['sha256'] for r in semantic['image_manifest']],
            'grounding_request_sha256': report['request_sha256'], 'observations': results,
            'limitations': ['Observed lens color mixes transmission and reflection, including rear views.',
                           'Clipped display codes censor physical radiance; display-proxy scenarios are not physical bounds.',
                           'No lens UV, incidence angle, hidden background, or group association is inferred.']}


def propose_transmission_candidates(hypotheses, image_evidence, groups):
    """Bounded uniform-density search suggestions from a single-group rear view.

    This does not create a numerical observation. Nominal normal incidence is a
    candidate construction assumption, while the original angle remains unknown.
    Multiple optical groups are deliberately unsupported without independent
    image-to-group association. Other tint/reflection candidates must remain.
    """
    semantic = validate_product_hypotheses(hypotheses, hypotheses['image_manifest'])
    evidence = validate_image_appearance_evidence(image_evidence, semantic['image_manifest'])
    values = list(groups.values()) if isinstance(groups, dict) else groups
    if len(values) != 1:
        return []
    binding = values[0].get('surface_binding', {})
    gid, prepared = binding.get('material_group_id'), binding.get('prepared_glb_sha256')
    if not gid or not prepared or len(prepared) != 64:
        raise ValueError('Transmission candidate requires an explicit prepared material-group binding')
    if not any(r['absorption'] in ('uniform_tint', 'clear') and r['confidence'] != 'low'
               for r in semantic['hypotheses']):
        return []
    back = [r for r in evidence['observations'] if r['view_label'] == 'back'
            and r['status'] == 'conditional_supported' and r.get('display_referred_transmission_hypotheses')]
    # Choose the most-supported source image deterministically; combining
    # uncalibrated exposures from different photos would invent radiance scale.
    if not back:
        return []
    photo = min({r['photo_id'] for r in back}, key=lambda p: (-sum(r['support_pixels'] for r in back if r['photo_id'] == p), p))
    back = [r for r in back if r['photo_id'] == photo]
    result = []
    for index in range(3):
        intervals = [np.asarray(r['display_referred_transmission_hypotheses'][index]['transmission_interval_rgb'])
                     for r in back if len(r['display_referred_transmission_hypotheses']) > index]
        if not intervals:
            continue
        low = np.min([r[:, 0] for r in intervals], axis=0)
        high = np.max([r[:, 1] for r in intervals], axis=0)
        value = (low + high) / 2
        if np.any(value <= 0):
            continue
        reflection_bound = back[0]['display_referred_transmission_hypotheses'][index]['maximum_additive_reflection_relative_to_backdrop']
        result.append({'photo_id': photo, 'source_sha256': back[0]['source_sha256'],
            'group_ids': [gid], 'prepared_glb_sha256': prepared, 'transmission_rgb': value.tolist(),
            'transmission_interval_rgb': np.column_stack((low, high)).tolist(),
            'incidence_degrees': 0., 'incidence_status': 'nominal_normal_incidence_candidate_assumption',
            'observed_incidence_degrees': None, 'reflection_hypothesis': f'additive reflected radiance proxy between zero and {reflection_bound} times the display backdrop',
            'uniform_absorption_hypothesis': True, 'status': 'conditional_display_proxy_not_physical_measurement',
            'support_pixels': sum(r['support_pixels'] for r in back),
            'source_region_ids': [r['region_id'] for r in back],
            'source_region_boxes': [r['bbox_xyxy_normalized'] for r in back],
            'confidence': 'uncalibrated_conditional_hypothesis',
            'assumptions': ['single_material_group_association_for_the_product',
                            'semantic_uniform_absorption_hypothesis', 'normal_incidence_for_candidate_construction_only',
                            'decoded_display_backdrop_equals_transmitted_radiance_proxy',
                            'back_photo_reflection_is_bounded_not_absent'],
            'limitations': ['No back-facing ray is flipped into a measured front-facing sample.',
                           'Different front/back reflectance or unknown studio exposure may invalidate this candidate.']})
    return result


def rear_image_constraints(hypotheses,image_evidence,groups):
    """Rear radiance intervals with independent bounded reflection nuisance.

    This is a conditional reciprocal-transmission hypothesis. Back incidence is
    unknown, and the front angular reflection is never copied onto the back.
    """
    semantic=validate_product_hypotheses(hypotheses,hypotheses['image_manifest'])
    evidence=validate_image_appearance_evidence(image_evidence,semantic['image_manifest'])
    values=list(groups.values()) if isinstance(groups,dict) else groups
    if len(values)!=1 or not any(h['absorption'] in ('uniform_tint','clear') and h['confidence']!='low' for h in semantic['hypotheses']):
        return {}
    binding=values[0]['surface_binding']; gid=binding['material_group_id']
    rows=[r for r in evidence['observations'] if r['view_label']=='back' and r['status']=='conditional_supported']
    result=[]
    for row in rows[:6]:
        result.append({'photo_id':row['photo_id'],'source_sha256':row['source_sha256'],
            'prepared_glb_sha256':binding['prepared_glb_sha256'],'region_id':row['region_id'],
            'linear_rgb_interval':row['linear_rgb_interval'],'backdrop_linear_rgb_interval':row['backdrop_linear_rgb_interval'],
            'incidence_range_degrees':[0.,75.], 'maximum_reflected_radiance':.3,
            'maximum_exposure_ratio':2.,'sigma_linear_rgb':.04,'weight':1/max(1,len(rows)),
            'assumption':'reciprocal_effective_transmission_uniform_absorption_unknown_rear_incidence_independent_rear_reflection',
            'status':'conditional_radiance_interval_not_calibrated_transmission'})
    return {gid:result} if result else {}
