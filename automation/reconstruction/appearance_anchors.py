"""Numerical evidence conditional on source-bound semantic region proposals.

Only photographed samples with actual exported lens coordinates are used. The
unknown studio and coarse proposal boundaries prevent physical identification;
derived density intervals are explicitly weak priors for ordinary-tint branches.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy

import numpy as np

from .lens_appearance import srgb_to_linear
from .photo_semantics import validate_product_hypotheses
from .photo_lens_fit import PhotoLensFitPolicy, _read_observation


@dataclass(frozen=True)
class AppearanceAnchorPolicy:
    minimum_pixels: int = 4
    maximum_incidence_degrees: float = 70.
    minimum_background_linear: float = .15
    maximum_background_relative_spread: float = .12
    minimum_density_sigma: float = .25
    maximum_reflected_backdrop_ratio: float = 1.5
    maximum_density: float = 8.
    maximum_v_span: float = .3

    def __post_init__(self):
        if type(self.minimum_pixels) is not int or self.minimum_pixels < 3:
            raise ValueError('At least three pixels are required for an appearance anchor')
        for key, value in asdict(self).items():
            if key != 'minimum_pixels' and (isinstance(value, bool) or not np.isfinite(value) or value <= 0):
                raise ValueError('Anchor policy values must be positive and finite')
        if self.maximum_incidence_degrees >= 90 or self.maximum_v_span > 1:
            raise ValueError('Unsupported incidence or v span')


def _inside(xy, size, box):
    normalized = (xy + .5) / np.asarray(size)
    return ((normalized[:, 0] >= box[0]) & (normalized[:, 0] < box[2]) &
            (normalized[:, 1] >= box[1]) & (normalized[:, 1] < box[3]))


def _preferences(hypotheses):
    result = []
    for row in hypotheses:
        if row['coating'] == 'colored_mirror':
            families = (['gradient_angular_mirror'] if row['absorption'] == 'gradient_tint' else
                        ['angular_mirror', 'colored_mirror'] if row['gradient_direction'] == 'angular_or_view_dependent'
                        or row.get('legacy_family') == 'angular_color_mirror' else ['colored_mirror', 'angular_mirror'])
        elif row['absorption'] == 'gradient_tint':
            families = ['gradient_tint']
        elif row['absorption'] in ('clear', 'uniform_tint'):
            families = ['uniform_tint']
        else:
            families = []
        result.extend(f for f in families if f not in result)
    return result


def sample_appearance_anchors(groups, hypotheses, policy=None, *, grounding=None, image_evidence=None):
    """Intersect clean proposals with geometry-bound, unique photo samples.

    `groups` accepts the existing list of {surface_binding, observations}, or
    its group-id mapping. Samples are deduplicated across mask alternatives;
    observations are not reclassified and no source pixel is synthesized.
    """
    policy = policy or AppearanceAnchorPolicy()
    if not isinstance(policy, AppearanceAnchorPolicy):
        raise ValueError('Expected AppearanceAnchorPolicy')
    report = validate_product_hypotheses(hypotheses, hypotheses['image_manifest'])
    if grounding is not None:
        if grounding.get('report', {}).get('method') != 'grounded_photo_appearance_evidence_v1':
            raise ValueError('Unexpected semantic region grounding')
        from .image_appearance_evidence import _sha
        for row in grounding['report']['regions']:
            mask = grounding['masks'].get(row['region_id'])
            if mask is None or mask.dtype != bool or _sha(np.packbits(mask).tobytes()) != row['mask_numeric_sha256']:
                raise ValueError('Grounded semantic sampling mask changed')
    manifest = {v['sha256']: v for v in report['image_manifest']}
    values = list(groups.values()) if isinstance(groups, dict) else groups
    if not isinstance(values, list) or not values:
        raise ValueError('At least one observation group is required')
    output = {'schema_version': 1, 'method': 'semantic_appearance_anchors_v1', 'accepted': False,
              'policy': asdict(policy), 'groups': {}, 'photos': {},
              'source_image_sha256': list(manifest), 'limitations': [
                  'Clean boxes and optical group membership remain hypotheses.',
                  'Density requires an ordinary coating and a bounded studio/backdrop assumption.',
                  'Mask alternatives share pixels; deduplicated union support is conditional, not a new mask.']}
    if image_evidence is not None:
        from .image_appearance_evidence import validate_image_appearance_evidence
        output['image_evidence'] = validate_image_appearance_evidence(image_evidence, report['image_manifest'])
    for image in report['image_manifest']:
        output['photos'][image['photo_id']] = {'source_sha256': image['sha256'],
            'image_size': image['image_size'], 'illuminant_rgb': [1., 1., 1.],
            'illumination_hypothesis': 'neutral_chromaticity_not_measurement',
            'reflection_regions': [{'bbox_xyxy_normalized': v['bbox_xyxy_normalized'], 'feather': .015,
                                    'semantic_region_id': v['id']}
                                   for v in report['regions'] if v['source_sha256'] == image['sha256']
                                   and v['kind'] == 'reflection']}
    for group in values:
        binding = group['surface_binding']
        gid = binding['material_group_id']
        if gid in output['groups']:
            raise ValueError('Duplicate material group id')
        if binding.get('uv_semantics') != 'lens_local_bottom_0_top_1':
            raise ValueError('Appearance anchors require actual canonical lens coordinates')
        result = {'density_anchors': [], 'unsupported_regions': [], 'family_preferences': _preferences(report['hypotheses']),
                  'surface_binding': copy.deepcopy(binding)}
        output['groups'][gid] = result
        records = [_read_observation(row, PhotoLensFitPolicy()) for row in group['observations']]
        for row in records:
            image = manifest.get(row['source_sha256'])
            if image is None or image['photo_id'] != row['photo_id'] or image['image_size'] != row['image_size']:
                raise ValueError('Observation and semantic source hash/id/pixel grid do not match')
        for region in [r for r in report['regions'] if r['kind'] == 'clean_sampling']:
            unavailable = {'semantic_region_id': region['id'], 'photo_id': region['photo_id']}
            if region['confidence'] == 'low':
                result['unsupported_regions'].append({**unavailable, 'reason': 'low_confidence_region'})
                continue
            samples, sources = {}, set()
            for row in records:
                if row['source_sha256'] != region['source_sha256']:
                    continue
                a = row['arrays']
                selected = row['eligible'] & _inside(a['xy'], row['image_size'], region['bbox_xyxy_normalized'])
                if grounding is not None:
                    entries = [r for r in grounding['report']['regions'] if r['region_id'] == region['id']]
                    if len(entries) != 1 or entries[0]['source_sha256'] != row['source_sha256']:
                        raise ValueError('Grounded region source binding differs from observation')
                    mask = grounding['masks'][region['id']]
                    if list(mask.shape[::-1]) != row['image_size']:
                        raise ValueError('Grounded region grid differs from observation')
                    xy = np.floor(a['xy']).astype(int)
                    selected &= mask[xy[:, 1], xy[:, 0]]
                    if entries[0]['status'] != 'conditional_supported':
                        selected[:] = False
                selected &= (a['angle'] <= policy.maximum_incidence_degrees) & (a['rear_weight'] == 0)
                # Clipped photographic channels are inequalities, not ordinary
                # RGB samples. Do not turn them into finite density equalities.
                selected &= np.all((a['code'] > 1) & (a['code'] < 254), axis=1)
                for reflection in output['photos'][row['photo_id']]['reflection_regions']:
                    selected &= ~_inside(a['xy'], row['image_size'], reflection['bbox_xyxy_normalized'])
                backdrop = row['provenance'].get('backdrop', {})
                if 'p10' not in backdrop or 'p90' not in backdrop:
                    continue
                p10, p90 = np.asarray(backdrop['p10']), np.asarray(backdrop['p90'])
                if p10.shape != (3,) or p90.shape != (3,) or not np.isfinite([p10, p90]).all():
                    continue
                spread = (p90 - p10) / np.maximum(np.median(a['background'], axis=0), .001)
                if np.any(spread < 0) or np.any(spread > policy.maximum_background_relative_spread):
                    continue
                selected &= np.all(a['background'] >= policy.minimum_background_linear, axis=1)
                for i in np.flatnonzero(selected):
                    key = tuple(a['xy'][i])
                    entry = np.r_[a['code'][i], a['background'][i], a['v'][i], a['angle'][i]]
                    if key in samples and not np.array_equal(samples[key], entry):
                        raise ValueError('Duplicate source pixel has contradictory color or lens coordinates')
                    samples[key] = entry
                    sources.add(row['id'])
            if len(samples) < policy.minimum_pixels:
                result['unsupported_regions'].append({**unavailable, 'reason': 'insufficient_clean_bound_pixels',
                                                       'support_pixels': len(samples)})
                continue
            data = np.stack(list(samples.values()))
            # Split broad boxes by actual material v, never by screen y. Keep
            # intervals local; endpoints are not extrapolated here.
            buckets = np.floor(data[:, 6] / policy.maximum_v_span).astype(int)
            found = False
            for bucket in sorted(set(buckets)):
                pixels = data[buckets == bucket]
                if len(pixels) < policy.minimum_pixels:
                    continue
                code, background, v, angle = pixels[:, :3], pixels[:, 3:6], pixels[:, 6], pixels[:, 7]
                ratio = srgb_to_linear(code / 255.) / background
                theta = np.radians(angle)
                reflection = .04 + .96 * (1 - np.cos(theta)) ** 5
                cos_inside = np.sqrt(1 - (np.sin(theta) / 1.5) ** 2)
                upper_t = ratio / (1 - reflection[:, None])
                lower_t = ((ratio - reflection[:, None] * policy.maximum_reflected_backdrop_ratio)
                           / (1 - reflection[:, None]))
                # A lower transmission bound at/below zero implies no finite
                # upper density bound. Preserve unsupported instead of clipping.
                if np.any(lower_t <= 0):
                    continue
                lower_d = -np.log(np.minimum(upper_t, 1)) * cos_inside[:, None]
                upper_d = -np.log(np.minimum(lower_t, 1)) * cos_inside[:, None]
                low = np.quantile(lower_d, .1, axis=0)
                high = np.quantile(upper_d, .9, axis=0)
                if np.any(high > policy.maximum_density) or np.any(low > high):
                    continue
                midpoint = (low + high) / 2
                sigma = np.maximum(policy.minimum_density_sigma, (high - low) / 2)
                result['density_anchors'].append({**unavailable, 'source_sha256': region['source_sha256'],
                    'v': float(np.median(v)), 'v_interval': [float(v.min()), float(v.max())],
                    'density_rgb': midpoint.tolist(), 'sigma': sigma.tolist(), 'weight': 1.,
                    'density_interval_rgb': np.column_stack((low, high)).tolist(),
                    'status': 'conditional_supported', 'support_pixels': len(pixels),
                    'applicable_families': ['uniform_tint', 'gradient_tint'],
                    'observed_linear_rgb': np.median(srgb_to_linear(code / 255.), axis=0).tolist(),
                    'source_observation_ids': sorted(sources),
                    'assumptions': ['ordinary_schlick_coating_r0_0.04_index_1.5',
                                    'backdrop_is_the_transmitted_radiance',
                                    f'reflected_radiance_between_zero_and_{policy.maximum_reflected_backdrop_ratio}_times_backdrop',
                                    'semantic_clean_box_and_geometry_are_unverified']})
                found = True
            if not found:
                result['unsupported_regions'].append({**unavailable, 'reason': 'density_unbounded_or_local_support_too_sparse',
                                                       'support_pixels': len(samples)})
        if result['density_anchors']:
            # More mask branches/boxes must not produce unbounded prior strength.
            weight = 1. / len(result['density_anchors'])
            for anchor in result['density_anchors']:
                anchor['weight'] = weight
    return output


def compile_material_priors(hypotheses, anchors, declared_facts=None):
    """Return the direct semantic_material_priors_v1 fitter contract.

    User/product facts are supplied separately and may restrict families. Model
    interpretations never create facts or eliminate contrary fit candidates.
    """
    report = validate_product_hypotheses(hypotheses, hypotheses['image_manifest'])
    if anchors.get('method') != 'semantic_appearance_anchors_v1' or set(anchors['source_image_sha256']) != {
            v['sha256'] for v in report['image_manifest']}:
        raise ValueError('Anchors and semantic report bind different images')
    facts = copy.deepcopy(declared_facts or {})
    if not isinstance(facts, dict) or set(facts) - {'mirror_coating'}:
        raise ValueError('Only the declared mirror_coating product fact is supported')
    if 'mirror_coating' in facts and type(facts['mirror_coating']) is not bool:
        raise ValueError('Declared mirror_coating must be boolean')
    groups = copy.deepcopy(anchors['groups'])
    ordinary_supported = any(v['coating'] == 'ordinary' and v['absorption'] != 'uncertain'
                             and v['confidence'] != 'low' for v in report['hypotheses'])
    for group in groups.values():
        if facts.get('mirror_coating') is True or (not ordinary_supported and facts.get('mirror_coating') is not False):
            group['density_anchors'] = []
        if 'mirror_coating' in facts:
            mirror = facts['mirror_coating']
            group['family_preferences'] = [f for f in group['family_preferences'] if ('mirror' in f) == mirror]
    result = {'schema_version': 1, 'method': 'semantic_material_priors_v1', 'accepted': False,
            'source_image_sha256': anchors['source_image_sha256'], 'groups': groups,
            'photos': copy.deepcopy(anchors['photos']), 'declared_facts': facts,
            'hypotheses': copy.deepcopy(report['hypotheses']),
            'family_preferences_are_soft': True,
            'limitations': anchors['limitations'] + ['Ordinal model confidence is not a calibrated probability.']}
    if 'image_evidence' in anchors:
        from .image_appearance_evidence import validate_image_appearance_evidence, propose_transmission_candidates, rear_image_constraints
        evidence = validate_image_appearance_evidence(anchors['image_evidence'], report['image_manifest'])
        result['image_appearance_observations'] = copy.deepcopy(evidence['observations'])
        for row in evidence['observations']:
            if row['view_label']=='back':
                result['photos'][row['photo_id']]['interface']='rear'
        for photo,label in evidence.get('declared_view_labels',{}).items():
            if str(label).strip().lower() in ('back','rear') and photo in result['photos']:
                result['photos'][photo]['interface']='rear'
        result['image_appearance_evidence'] = {k: copy.deepcopy(v) for k, v in evidence.items() if k != 'observations'}
        result['transmission_candidate_hypotheses'] = propose_transmission_candidates(report, evidence, groups)
        for gid,constraints in rear_image_constraints(report,evidence,groups).items():
            result['groups'][gid]['rear_image_constraints']=constraints
    return result


def estimate_transmission_from_contrast(photo_linear, lens_patch, rear_template,
                                        coordinate_fields=None, template_uncertainty=0., *,
                                        maximum_residual=.025, minimum_contrast=.04):
    """Fit local I = T*B + a + gx*x + gy*y, with conservative failure states.

    `rear_template` must be independently supplied linear RGB on the same grid;
    this function does not invent it from generated textures. Registration and
    refractive displacement must already be resolved by the caller. A sharp
    reflection exactly correlated with B is fundamentally unidentifiable here.
    """
    photo, template = np.asarray(photo_linear, float), np.asarray(rear_template, float)
    mask = np.asarray(lens_patch)
    if photo.ndim != 3 or photo.shape[-1] != 3 or template.shape != photo.shape or mask.shape != photo.shape[:2] or mask.dtype != bool:
        raise ValueError('Contrast inputs require aligned HxWx3 RGB and a boolean HxW mask')
    uncertainty = np.asarray(template_uncertainty, float)
    if uncertainty.shape not in ((), (3,)) or not np.isfinite(uncertainty).all() or np.any(uncertainty < 0):
        raise ValueError('Template uncertainty must be nonnegative scalar or RGB')
    if not np.isfinite([maximum_residual, minimum_contrast]).all() or min(maximum_residual, minimum_contrast) <= 0:
        raise ValueError('Contrast thresholds must be positive')
    selected = mask & np.isfinite(photo).all(axis=2) & np.isfinite(template).all(axis=2)
    selected &= np.all((photo > 0) & (photo < 1), axis=2) & np.all((template >= 0) & (template <= 1), axis=2)
    result = {'method': 'local_contrast_transmission_v1', 'status': 'unsupported', 'accepted': False,
              'support_pixels': int(selected.sum()), 'transmission_rgb': None, 'transmission_interval_rgb': None,
              'assumptions': ['independently_observed_aligned_rear_template', 'locally_constant_transmission',
                              'reflection_is_additive_and_planar_in_patch', 'linear_srgb_radiance'],
              'limitations': ['No automatic rear-template correspondence or refraction registration is implemented.',
                              'Reflection exactly correlated with the rear template is not identifiable.']}
    if selected.sum() < 16:
        return {**result, 'reason': 'insufficient_unclipped_support'}
    yy, xx = np.indices(mask.shape)
    if coordinate_fields is None:
        coordinates = np.stack((xx / max(1, mask.shape[1] - 1), yy / max(1, mask.shape[0] - 1)), axis=-1)
    else:
        coordinates = np.asarray(coordinate_fields, float)
        if coordinates.shape != (*mask.shape, 2) or not np.isfinite(coordinates[selected]).all():
            raise ValueError('Coordinate fields require finite aligned HxWx2 values')
    nuisance = np.column_stack((np.ones(selected.sum()), coordinates[selected]))
    if np.linalg.matrix_rank(nuisance) < 3:
        return {**result, 'reason': 'insufficient_spatial_support'}
    target, rear = photo[selected], template[selected]
    # Residualizing both fields against the same plane exposes how much rear
    # structure is independently available to measure its contrast attenuation.
    rear_residual = rear - nuisance @ np.linalg.lstsq(nuisance, rear, rcond=None)[0]
    target_residual = target - nuisance @ np.linalg.lstsq(nuisance, target, rcond=None)[0]
    contrast = np.sqrt(np.mean(rear_residual ** 2, axis=0))
    if np.any(contrast < minimum_contrast):
        return {**result, 'reason': 'rear_template_has_insufficient_nonplanar_contrast'}
    transmission = np.sum(rear_residual * target_residual, axis=0) / np.sum(rear_residual ** 2, axis=0)
    residual = target_residual - rear_residual * transmission
    rms = np.sqrt(np.mean(residual ** 2, axis=0))
    if np.any(rms > maximum_residual) or np.any(transmission < 0) or np.any(transmission > 1):
        return {**result, 'reason': 'registration_reflection_or_constant_transmission_model_mismatch',
                'residual_rms_rgb': rms.tolist()}
    # Spatial/template systematic error is not divided by sqrt(N). More pixels
    # cannot make an uncertain rear-template calibration falsely precise.
    radius = np.maximum(.01, (2 * rms + 2 * uncertainty * np.abs(transmission)) / contrast)
    low, high = np.maximum(0, transmission - radius), np.minimum(1, transmission + radius)
    return {**result, 'status': 'conditional_supported', 'reason': None,
            'transmission_rgb': transmission.tolist(), 'transmission_interval_rgb': np.column_stack((low, high)).tolist(),
            'residual_rms_rgb': rms.tolist(), 'rear_contrast_rms_rgb': contrast.tolist(),
            'template_uncertainty_linear_rgb': np.broadcast_to(uncertainty, (3,)).tolist()}
