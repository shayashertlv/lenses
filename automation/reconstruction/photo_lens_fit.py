"""Bounded, uncalibrated photo-to-LensAppearance candidate generation.

This module NEVER identifies optical parameters. It fits conditional explanations
and emits real canonical descriptors, including explanations that disagree in AR.
The calibrated fitter in lens_fit.py is a different instrument.

Input contract (one candidate material group per call): each observation is a
dict with unique id, photo_id, region_id, hypothesis_id, source_sha256, provenance
(finite JSON object with method), and arrays xy[N,2], code_rgb[N,3] integer codes,
intrinsic_v[N], incidence_degrees[N], background_rgb[N,3]. background_rgb is a
SUPPLIED effective linear-background hypothesis, not inferred calibrated light.
Optional alpha_code[N] defaults to 255; reflected_direction[N,3] consists of unit
vectors; rear_rgb[N,3] and rear_weight[N] must be supplied together. Entirely
unknown rear_rgb rows retain geometry support without inventing a rear color. image_size
[width,height] optionally fixes the spatial validation grid. Numeric lists and
NumPy arrays are accepted. Null/NaN coordinates mean unknown; infinity is invalid.
UV must be in [0,1], incidence in [0,180]; back-side and unknown points are excluded
and counted. Alpha must be opaque. No background is invented for alpha blending.

surface_binding requires schema_version=1, prepared_glb_sha256, material_group_id,
coordinate_method and uv_semantics='lens_local_bottom_0_top_1'. Extra finite JSON
provenance is retained. The caller verifies source bytes and actual exported UV.

Alternatives are grouped by (photo_id,region_id); all distinct Cartesian branches
are explored within explicit budgets. Identical numeric hypotheses are aliases,
not independent observations. One source SHA-256 has exactly one photo_id, so an
alias cannot move the same source pixel between training and validation. Lighting is shared within each photo IN THIS CALL,
not jointly across calls. Three starts and five families are the default policy.
Roughness priors reuse the identical photo fit: this local model does not measure
reflection blur. Input endpoints are one-sided censored code intervals. A robust
code scale and fit tolerances are declared policies, never measured noise or AR
acceptance. The photo response assumes extended sRGB plus bounded exposure/WB;
unmodeled retouching, spectral effects and scene structure can cause mismatch.
The environment-base amplitude bound is separate from the conservative smooth
field bound; both are reported before exposure and white balance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import itertools
import json
from numbers import Real
import re

import numpy as np
from scipy.optimize import least_squares

from .lens_appearance import DensityKeyframe, LensAppearance, ReflectanceKeyframe


FAMILIES = ('uniform_tint', 'gradient_tint', 'colored_mirror', 'angular_mirror', 'gradient_angular_mirror')
_REQUIRED = {'id', 'photo_id', 'region_id', 'hypothesis_id', 'source_sha256', 'provenance',
             'xy', 'code_rgb', 'intrinsic_v', 'incidence_degrees', 'background_rgb'}
_OPTIONAL = {'alpha_code', 'reflected_direction', 'rear_rgb', 'rear_weight', 'image_size','image_eligible'}
REAR_CONTENT_POLICIES = ('excluded', 'explained')
# Magnitudes below this are not representable as nonzero float32 values by the AR runtime.
GPU_ZERO = 1e-30
REAR_CONTENT_EXCLUDED = 'rear_content_excluded'
_DIRECTION_NORM_TOLERANCE = 1e-5
MAXIMUM_SEMANTIC_SOFTBOXES = 4


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be nonempty text')
    return value


def _sha(value, name):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ValueError(f'{name} must be a lowercase SHA-256')
    return value


def _json(value, name):
    try:
        return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError(f'{name} must be finite JSON') from error


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _array(value, shape, name, *, unknown=False, low=None, high=None, integer=False):
    raw = np.asarray(value)
    flat_objects = np.asarray(value, dtype=object).ravel()
    if any(isinstance(v, (bool, np.bool_)) for v in flat_objects) or integer and raw.dtype.kind not in 'iu':
        raise ValueError(f'{name} must contain numeric {"integers" if integer else "values"}, not booleans')
    if raw.dtype.kind not in 'fiu' and not (unknown and raw.dtype.kind == 'O'):
        raise ValueError(f'{name} must be a numeric array')
    if raw.dtype.kind == 'O' and any(v is not None and not isinstance(v, Real) for v in flat_objects):
        raise ValueError(f'{name} contains a nonnumeric coordinate')
    try:
        result = np.asarray(value, dtype=np.float64).copy()
    except (ValueError, TypeError) as error:
        raise ValueError(f'{name} must be numeric') from error
    if result.shape != shape or np.isinf(result).any() or not unknown and not np.isfinite(result).all():
        raise ValueError(f'Invalid {name} shape or finite values')
    known = np.isfinite(result)
    if low is not None and np.any(result[known] < low) or high is not None and np.any(result[known] > high):
        raise ValueError(f'{name} is outside its allowed range')
    result[np.isnan(result)] = np.nan
    result.setflags(write=False)
    return result


def _array_hash(value):
    clean = np.where(np.isnan(value), 0, value).astype('<f8')
    return hashlib.sha256(str(value.shape).encode() + clean.tobytes() + np.isnan(value).tobytes()).hexdigest()


def _environment_bounds(policy):
    # Accepted directions can differ from unit length by the validation tolerance.
    # ||d||_1 <= sqrt(3)||d||_2; |dx^2-dy^2| <= ||d||_2^2. Each RGB coefficient
    # has absolute bound b, hence exp(dot(features,coefficients)) <= exp(b*L1).
    norm = 1 + _DIRECTION_NORM_TOLERANCE
    feature_bound = float(np.sqrt(3) * norm + norm * norm)
    with np.errstate(over='ignore', invalid='ignore'):
        smooth = float(policy.maximum_environment_base_radiance * np.exp(policy.smooth_environment_log_bound * feature_bound))
    if not np.isfinite(smooth):
        raise ValueError('Smooth environment radiance bound must remain finite')
    result = {'maximum_environment_base_radiance': policy.maximum_environment_base_radiance,
            'constant_field_maximum_channel_radiance': policy.maximum_environment_base_radiance,
            'smooth_field_maximum_channel_radiance': smooth, 'smooth_feature_l1_bound': feature_bound,
            'direction_norm_tolerance': _DIRECTION_NORM_TOLERANCE,
            'smooth_bound_derivation': 'base * exp(log_coefficient_bound * (sqrt(3)*(1+direction_tolerance)+(1+direction_tolerance)^2))',
            'scope': 'effective_reflected_environment_before_exposure_and_white_balance',
            'conservative_bound_not_measured_illumination': True}
    if 'semantic_softbox' in policy.lighting_families:
        result.update(semantic_softbox_maximum_channel_radiance=policy.maximum_environment_base_radiance*(1+MAXIMUM_SEMANTIC_SOFTBOXES),
                      semantic_softbox_bound_derivation='normalized illuminant max channel 1 * (base + at most four nonnegative bounded scalar boxes)',
                      semantic_softbox_is_image_plane_nuisance_not_physical_environment=True)
    return result


def validate_appearance_priors(value, records, group_ids):
    """Validate conditional numerical priors against exact source photographs.

    Extra source photos may provide evidence without participating in this fit.
    Inferred family preferences are deliberately not hard family filters. A
    density inferred assuming ordinary reflection applies to tint families only
    unless its author explicitly declares another applicability domain.
    """
    if value is None:
        return None
    result = _json(value, 'appearance_priors')
    if (not isinstance(result, dict) or type(result.get('schema_version')) is not int or result.get('schema_version') != 1
            or result.get('method') != 'semantic_material_priors_v1'):
        raise ValueError('appearance_priors requires semantic_material_priors_v1')
    sources = result.get('source_image_sha256')
    if not isinstance(sources, list) or not sources or len(sources) > 48:
        raise ValueError('appearance_priors requires distinct bounded source_image_sha256')
    for source in sources:
        _sha(source, 'appearance prior source')
    if len(set(sources)) != len(sources):
        raise ValueError('appearance_priors requires distinct bounded source_image_sha256')
    photos = result.get('photos')
    groups = result.get('groups')
    if not isinstance(photos, dict) or not photos or len(photos) > 48:
        raise ValueError('appearance_priors requires bounded photos')
    if not isinstance(groups, dict) or set(groups) - set(group_ids):
        raise ValueError('appearance_priors names an unknown material group')
    for photo, evidence in photos.items():
        _text(photo, 'appearance prior photo_id')
        if not isinstance(evidence, dict) or evidence.get('source_sha256') not in sources:
            raise ValueError('appearance prior photo lacks a bound source')
        if evidence.get('interface','front') not in ('front','rear'):
            raise ValueError('Photo optical interface must be front or rear')
        illuminant = _array(evidence.get('illuminant_rgb'), (3,), 'illuminant_rgb', low=1e-5, high=100)
        # Chromaticity only: scalar photographic amplitudes carry the intensity.
        evidence['illuminant_rgb'] = (illuminant / illuminant.max()).tolist()
        _text(evidence.get('illumination_hypothesis'), 'illumination_hypothesis')
        regions = evidence.get('reflection_regions', [])
        if not isinstance(regions, list) or len(regions) > MAXIMUM_SEMANTIC_SOFTBOXES:
            raise ValueError('At most four semantic reflection regions per photo')
        for region in regions:
            if not isinstance(region, dict):
                raise ValueError('A reflection region must be a JSON object')
            box = _array(region.get('bbox_xyxy_normalized'), (4,), 'reflection bbox', low=0, high=1)
            if box[0] >= box[2] or box[1] >= box[3]:
                raise ValueError('A reflection box must have positive area')
            feather = region.get('feather', .015)
            if isinstance(feather, bool) or not isinstance(feather, Real) or not .001 <= feather <= .25:
                raise ValueError('Reflection feather must lie in [.001,.25]')
            region['feather'] = float(feather)
        evidence['reflection_regions'] = regions
    for row in records:
        if row['photo_id'] not in photos or photos[row['photo_id']]['source_sha256'] != row['source_sha256']:
            raise ValueError('Appearance prior source/photo binding differs from observations')
    for group, evidence in groups.items():
        if not isinstance(evidence, dict):
            raise ValueError('Appearance group must be a JSON object')
        preferences = evidence.get('family_preferences', [])
        if not isinstance(preferences, list) or any(f not in FAMILIES for f in preferences):
            raise ValueError('Unknown inferred family preference')
        anchors = evidence.get('density_anchors', [])
        if not isinstance(anchors, list) or len(anchors) > 128:
            raise ValueError('Density anchors must be a bounded list')
        for anchor in anchors:
            if not isinstance(anchor, dict):
                raise ValueError('Density anchor must be a JSON object')
            _array([anchor.get('v')], (1,), 'density anchor v', low=0, high=1)
            _array(anchor.get('density_rgb'), (3,), 'density_rgb', low=0, high=100)
            _array(anchor.get('sigma'), (3,), 'density anchor sigma', low=1e-5, high=100)
            _array([anchor.get('weight', 1.)], (1,), 'density anchor weight', low=0, high=1)
            source_photo = photos.get(anchor.get('photo_id'), {})
            if anchor.get('source_sha256') != source_photo.get('source_sha256'):
                raise ValueError('Density anchor source/photo binding differs from priors')
            applicable = anchor.get('applicable_families', ['uniform_tint', 'gradient_tint'])
            if not isinstance(applicable, list) or not applicable or any(f not in FAMILIES for f in applicable):
                raise ValueError('Density anchor applicability must name material families')
            anchor['applicable_families'] = applicable
        evidence['density_anchors'] = anchors
        transmission=evidence.get('transmission_anchors',[])
        if not isinstance(transmission,list) or len(transmission)>16:
            raise ValueError('Contrast transmission anchors must be bounded')
        for anchor in transmission:
            if (anchor.get('status')!='conditional_observed_contrast_interval'
                    or anchor.get('template_source')!='observed_outside_aperture_pixels_only'
                    or photos.get(anchor.get('photo_id'),{}).get('source_sha256')!=anchor.get('source_sha256')):
                raise ValueError('Contrast transmission source/template binding differs')
            if isinstance(group_ids,dict) and anchor.get('prepared_glb_sha256')!=group_ids[group]['prepared_glb_sha256']:
                raise ValueError('Contrast transmission geometry binding differs')
            _sha(anchor.get('correspondence_sha256'),'contrast correspondence SHA-256')
            _array([anchor.get('v')],(1,),'contrast intrinsic v',low=0,high=1)
            _array([anchor.get('incidence_degrees')],(1,),'contrast incidence',low=0,high=85)
            interval=_array(anchor.get('transmission_interval_rgb'),(3,2),'contrast transmission interval',low=0,high=1)
            if np.any(interval[:,0]>interval[:,1]):
                raise ValueError('Contrast interval reversed')
            _array([anchor.get('sigma')],(1,),'contrast uncertainty',low=.01,high=1)
            _array([anchor.get('weight')],(1,),'contrast weight',low=0,high=1)
        rear=evidence.get('rear_image_constraints',[])
        if not isinstance(rear,list) or len(rear)>6:
            raise ValueError('Rear image constraints must be bounded')
        for constraint in rear:
            if (constraint.get('status')!='conditional_radiance_interval_not_calibrated_transmission'
                    or photos.get(constraint.get('photo_id'),{}).get('source_sha256')!=constraint.get('source_sha256')):
                raise ValueError('Rear image constraint source/photo binding differs')
            if isinstance(group_ids,dict) and constraint.get('prepared_glb_sha256')!=group_ids[group]['prepared_glb_sha256']:
                raise ValueError('Rear image constraint prepared geometry differs')
            for name in ('linear_rgb_interval','backdrop_linear_rgb_interval'):
                interval=_array(constraint.get(name),(3,2),name,low=0,high=1)
                if np.any(interval[:,0]>interval[:,1]):
                    raise ValueError('Rear image interval bounds reversed')
            angles=_array(constraint.get('incidence_range_degrees'),(2,),'rear incidence range',low=0,high=80)
            if angles[0]>=angles[1]:
                raise ValueError('Rear incidence must retain a nonempty uncertainty interval')
            _array([constraint.get('maximum_reflected_radiance')],(1,),'rear reflection bound',low=.001,high=2)
            _array([constraint.get('maximum_exposure_ratio')],(1,),'rear exposure ratio',low=1.01,high=4)
            _array([constraint.get('sigma_linear_rgb')],(1,),'rear radiance uncertainty',low=.01,high=1)
            _array([constraint.get('weight')],(1,),'rear evidence weight',low=0,high=1)
    facts = result.get('declared_facts', {})
    if not isinstance(facts, dict) or 'mirror_coating' in facts and type(facts['mirror_coating']) is not bool:
        raise ValueError('Declared mirror_coating must be a boolean product fact')
    result['declared_facts'] = facts
    if 'material_relations' in result:
        from .material_relations import validate_material_relations
        result['material_relations'] = validate_material_relations(result['material_relations'],
            group_ids=group_ids, sources=sources, photos=photos,
            bindings=group_ids if isinstance(group_ids, dict) else None, group_priors=groups)
    return result


def _prior_policy(policy, priors):
    """Explicit product facts outrank inferred preferences and anchor assumptions."""
    mirror = (priors or {}).get('declared_facts', {}).get('mirror_coating')
    if mirror is None:
        return policy
    families = tuple(f for f in policy.families if (f not in ('uniform_tint', 'gradient_tint')) == mirror)
    if not families:
        raise ValueError('Declared mirror_coating contradicts every requested material family')
    return replace(policy, families=families)


def _semantic_softbox_basis(xy, image_size, regions):
    """Image-plane nuisance bases, never intrinsic material coordinates."""
    if image_size is None:
        raise ValueError('semantic_softbox requires an explicit source image_size')
    coordinates = (np.asarray(xy, float) + .5) / np.asarray(image_size, float)
    columns = []
    for region in regions:
        x0, y0, x1, y1 = region['bbox_xyxy_normalized']
        feather = region['feather']
        edge_distance = np.column_stack((coordinates[:, 0] - x0, x1 - coordinates[:, 0],
                                         coordinates[:, 1] - y0, y1 - coordinates[:, 1]))
        t = np.clip(1 + edge_distance / feather, 0, 1)
        columns.append(np.prod(t*t*(3-2*t), axis=1))
    return np.column_stack(columns) if columns else np.empty((len(xy), 0))


@dataclass(frozen=True)
class PhotoLensFitPolicy:
    families: tuple[str, ...] = FAMILIES
    lighting_families: tuple[str, ...] = ('constant', 'smooth')
    roughness_values: tuple[float, ...] = (.05, .25)
    starts: int = 3
    max_nfev: int = 80
    maximum_mask_branches: int = 243
    maximum_optimization_runs: int = 540
    maximum_samples_per_observation: int = 4096
    maximum_observations: int = 48
    maximum_photos: int = 8
    maximum_total_samples: int = 16384
    minimum_training_points_per_photo: int = 3
    # A (group, photo) row decides the policy only on at least this many held-out
    # samples: the required fraction on a handful of samples is a coin flip (two
    # channels of forty-five decided a lens judged on fifteen samples). The split
    # tries finer grids to reach it; a row that cannot is reported as unmeasured.
    minimum_validation_points_per_photo: int = 24
    code_robust_scale: float = 4.
    photo_tolerance_codes: float = 8.
    required_within_tolerance_fraction: float = .9
    maximum_density: float = 8.
    maximum_environment_base_radiance: float = 8.
    maximum_unknown_rear_radiance: float = 2.
    maximum_exposure_stops: float = 2.
    maximum_white_balance_log: float = .3
    smooth_environment_log_bound: float = .7
    # Rear-content samples (a rear temple, pad or frame behind the lens along the
    # ray) carry an unknown rear radiance. 'excluded' keeps them out of the training
    # residual and the photo policy; the ledger still counts them. 'explained'
    # is the earlier behaviour: one unknown rear color per photo and group.
    rear_content: str = 'excluded'
    density_curvature_penalty: float = .02
    reflectance_curvature_penalty: float = .02
    nuisance_penalty: float = .002
    probe_agreement_tolerance_linear: float = .02
    # Spatial validation tiles per photo: the first grid at which every eligible
    # observation row keeps the policy minimum training and validation points
    # is used. (4,) reproduces the historical fixed 4x4 split exactly.
    spatial_split_grids: tuple[int, ...] = (4, 8, 16)
    # The validation tiles must not starve training: a grid is accepted only when
    # every eligible row keeps at most this share of its samples in validation
    # (a small far lens can otherwise sit almost entirely inside one coarse
    # validation tile). 1.0 reproduces the earlier minimum-only rule.
    maximum_validation_share: float = .5
    # Density knots of the gradient families along lens-local v (0 bottom, 1 top),
    # evenly spaced with smoothstep interpolation between neighbours. Three knots
    # cannot bend to a gradient that reaches full darkness well below the top
    # (a mid-height band of positive residual on both Victoria Beckham lenses);
    # fits saved before this field replay with three.
    gradient_density_keyframes: int = 5

    def __post_init__(self):
        grids = self.spatial_split_grids
        if (not isinstance(grids, (list, tuple)) or not grids or len(grids) > 8
                or any(type(n) is not int or not 2 <= n <= 64 for n in grids)
                or any(b <= a for a, b in zip(grids, grids[1:]))):
            raise ValueError('spatial_split_grids must be a strictly increasing tuple of integers in 2..64')
        object.__setattr__(self, 'spatial_split_grids', tuple(grids))
        share = self.maximum_validation_share
        if isinstance(share, bool) or not isinstance(share, Real) or not np.isfinite(share) or not 0 < share <= 1:
            raise ValueError('maximum_validation_share must lie in (0, 1]')
        object.__setattr__(self, 'maximum_validation_share', float(share))
        if type(self.gradient_density_keyframes) is not int or not 3 <= self.gradient_density_keyframes <= 9:
            raise ValueError('gradient_density_keyframes must be an integer in 3..9')
        for name, allowed in (('families', FAMILIES), ('lighting_families', ('constant', 'smooth', 'semantic_softbox'))):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or not values or len(set(values)) != len(values) or any(v not in allowed for v in values):
                raise ValueError(f'Invalid {name}')
            object.__setattr__(self, name, tuple(sorted(values, key=allowed.index)))
        values = self.roughness_values
        if not isinstance(values, (list, tuple)) or not values or len(set(values)) != len(values):
            raise ValueError('roughness_values must be distinct priors')
        if any(isinstance(v, bool) or not isinstance(v, Real) or not np.isfinite(v) or not 0 <= v <= 1 for v in values):
            raise ValueError('Invalid roughness prior')
        object.__setattr__(self, 'roughness_values', tuple(sorted(float(v) for v in values)))
        for name in ('starts', 'max_nfev', 'maximum_mask_branches', 'maximum_optimization_runs',
                     'maximum_samples_per_observation', 'maximum_observations', 'maximum_photos', 'maximum_total_samples',
                     'minimum_training_points_per_photo', 'minimum_validation_points_per_photo'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')
        if not 3 <= self.starts <= 8 or self.max_nfev > 2000:
            raise ValueError('Use 3..8 starts and at most 2000 evaluations per start')
        for name in ('code_robust_scale', 'photo_tolerance_codes', 'maximum_density', 'maximum_environment_base_radiance',
                     'maximum_unknown_rear_radiance', 'maximum_exposure_stops', 'maximum_white_balance_log',
                     'smooth_environment_log_bound', 'density_curvature_penalty', 'reflectance_curvature_penalty',
                     'nuisance_penalty', 'probe_agreement_tolerance_linear'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be positive and finite')
            object.__setattr__(self, name, float(value))
        if self.rear_content not in REAR_CONTENT_POLICIES:
            raise ValueError('rear_content must be excluded or explained')
        value = self.required_within_tolerance_fraction
        if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value) or not 0 < value <= 1:
            raise ValueError('Invalid required_within_tolerance_fraction')
        object.__setattr__(self, 'required_within_tolerance_fraction', float(value))
        _environment_bounds(self)


def _read_observation(value, policy):
    if not isinstance(value, dict) or not _REQUIRED <= value.keys() or value.keys() - _REQUIRED - _OPTIONAL:
        raise ValueError('Observation requires exactly the documented required/optional fields')
    names = {k: _text(value[k], k) for k in ('id', 'photo_id', 'region_id', 'hypothesis_id')}
    source = _sha(value['source_sha256'], 'source_sha256')
    provenance = _json(value['provenance'], 'provenance')
    if not isinstance(provenance, dict) or not provenance.get('method'):
        raise ValueError('provenance.method is required')
    _text(provenance['method'], 'provenance.method')
    code = np.asarray(value['code_rgb'])
    if code.ndim != 2 or code.shape[1:] != (3,) or not 1 <= len(code) <= policy.maximum_samples_per_observation:
        raise ValueError('code_rgb requires a bounded nonempty Nx3 grid')
    n = len(code)
    arrays = {
        'code': _array(code, (n, 3), 'code_rgb', low=0, high=255, integer=True),
        'xy': _array(value['xy'], (n, 2), 'xy', low=0),
        'v': _array(value['intrinsic_v'], (n,), 'intrinsic_v', unknown=True, low=0, high=1),
        'angle': _array(value['incidence_degrees'], (n,), 'incidence_degrees', unknown=True, low=0, high=180),
        'background': _array(value['background_rgb'], (n, 3), 'background_rgb', low=0, high=1e6),
        'alpha': _array(value.get('alpha_code', np.full(n, 255)), (n,), 'alpha_code', low=0, high=255, integer=True),
    }
    if 'image_eligible' in value:
        arrays['image_eligible']=_array(value['image_eligible'],(n,),'image_eligible',low=0,high=1,integer=True)
    if len(np.unique(arrays['xy'], axis=0)) != n:
        raise ValueError('Repeated photo coordinates within an observation are not independent samples')
    if 'reflected_direction' in value:
        direction = _array(value['reflected_direction'], (n, 3), 'reflected_direction', unknown=True)
        finite = np.isfinite(direction).all(axis=1)
        if np.any(np.isfinite(direction).any(axis=1) != finite) or np.any(np.abs(np.linalg.norm(direction[finite], axis=1) - 1) > _DIRECTION_NORM_TOLERANCE):
            raise ValueError('reflected_direction rows must be unit vectors or entirely unknown')
        arrays['direction'] = direction
    else:
        arrays['direction'] = np.full((n, 3), np.nan)
    if ('rear_rgb' in value) != ('rear_weight' in value):
        raise ValueError('rear_rgb and rear_weight must be supplied together')
    has_rear = 'rear_rgb' in value
    arrays['rear'] = _array(value['rear_rgb'], (n, 3), 'rear_rgb', unknown=True, low=0, high=1e6) if has_rear else np.zeros((n, 3))
    if has_rear and np.any(np.isfinite(arrays['rear']).any(axis=1) != np.isfinite(arrays['rear']).all(axis=1)):
        raise ValueError('rear_rgb rows must be entirely known or entirely unknown')
    arrays['rear_weight'] = _array(value['rear_weight'], (n,), 'rear_weight', low=0, high=1) if has_rear else np.zeros(n)
    image_size = value.get('image_size')
    if image_size is not None:
        if not isinstance(image_size, (list, tuple)) or len(image_size) != 2 or any(type(x) is not int or x < 2 for x in image_size):
            raise ValueError('image_size must be [width,height] integer pixels')
        if np.any(arrays['xy'] >= np.asarray(image_size)):
            raise ValueError('Sample coordinate lies outside image_size')
        image_size = list(image_size)
    # Spatial order is independent of input row order, including split assignment.
    order = np.lexsort((arrays['xy'][:, 1], arrays['xy'][:, 0]))
    arrays = {k: np.array(v[order], copy=True) for k, v in arrays.items()}
    eligible = (arrays['alpha'] == 255) & np.isfinite(arrays['v']) & np.isfinite(arrays['angle']) & (arrays['angle'] < 90)
    if 'image_eligible' in arrays:
        eligible &= arrays['image_eligible']==1
    rear_content = has_rear and bool(np.any(arrays['rear_weight'] > 0))
    if policy.rear_content == 'excluded':
        # Only rays that reach the backdrop behind the lens are clean transmission
        # samples. The others are kept in the arrays and counted, never fitted.
        eligible &= arrays['rear_weight'] == 0
    digest = _hash({'photo_id': names['photo_id'], 'region_id': names['region_id'], 'source': source,
                    'arrays': {k: _array_hash(v) for k, v in arrays.items()}, 'image_size': image_size, 'rear_supplied': has_rear})
    return {**names, 'source_sha256': source, 'provenance': provenance, 'arrays': arrays, 'eligible': eligible,
            'image_size': image_size, 'has_rear': has_rear, 'rear_content': rear_content, 'numeric_sha256': digest}


def _prepare(observations, surface_binding, policy, *, defer_large_branches=False):
    binding = _json(surface_binding, 'surface_binding')
    required = {'schema_version', 'prepared_glb_sha256', 'material_group_id', 'coordinate_method', 'uv_semantics'}
    if not isinstance(binding, dict) or not required <= binding.keys() or type(binding['schema_version']) is not int or binding['schema_version'] != 1:
        raise ValueError('surface_binding requires the versioned prepared-surface contract')
    _sha(binding['prepared_glb_sha256'], 'prepared_glb_sha256')
    for name in ('material_group_id', 'coordinate_method'):
        _text(binding[name], name)
    if binding['uv_semantics'] != 'lens_local_bottom_0_top_1':
        raise ValueError('Prepared UV semantics must match canonical LensAppearance')
    if not isinstance(observations, list) or not observations:
        raise ValueError('At least one observation is required')
    if len(observations) > policy.maximum_observations:
        raise ValueError('Observation count exceeds the explicit work budget')
    records = sorted((_read_observation(v, policy) for v in observations), key=lambda v: (v['photo_id'], v['region_id'], v['hypothesis_id'], v['id']))
    if len({r['photo_id'] for r in records}) > policy.maximum_photos or sum(len(r['eligible']) for r in records) > policy.maximum_total_samples:
        raise ValueError('Photo/sample count exceeds the explicit work budget')
    if len({v['id'] for v in records}) != len(records):
        raise ValueError('Observation id must be unique')
    identities = [(v['photo_id'], v['region_id'], v['hypothesis_id']) for v in records]
    if len(set(identities)) != len(identities):
        raise ValueError('Hypothesis identity must be unique within a photo region')
    source_ids = {}
    for row in records:
        previous = source_ids.setdefault(row['source_sha256'], row['photo_id'])
        if previous != row['photo_id']:
            raise ValueError('One source SHA-256 cannot have multiple photo IDs; use alternatives under the same photo_id')
    split_ledger = []
    for photo in sorted({v['photo_id'] for v in records}):
        rows = [v for v in records if v['photo_id'] == photo]
        if len({v['source_sha256'] for v in rows}) != 1 or len({str(v['image_size']) for v in rows}) != 1:
            raise ValueError('One photo_id must bind one source and pixel grid')
        pixel_values = {}
        for row in rows:
            for xy, rgb, alpha in zip(row['arrays']['xy'], row['arrays']['code'], row['arrays']['alpha']):
                key, value = tuple(xy), (*rgb, alpha)
                if key in pixel_values and pixel_values[key] != value:
                    raise ValueError('One source photo pixel has inconsistent RGB/alpha across hypotheses')
                pixel_values[key] = value
        if rows[0]['image_size'] is None:
            xy = np.concatenate([v['arrays']['xy'] for v in rows])
            lower, span = xy.min(axis=0), np.maximum(1, xy.max(axis=0) - xy.min(axis=0) + 1)
            basis = 'common_per_photo_sample_extent'
        else:
            lower, span, basis = np.zeros(2), np.asarray(rows[0]['image_size']), 'declared_source_image_size'
        grids, chosen, tried = policy.spatial_split_grids, None, []
        for grid in grids:
            met = True
            for row in rows:
                validation = _tile_validation(row['arrays']['xy'], lower, span, grid)
                eligible, held = int(row['eligible'].sum()), int((row['eligible'] & validation).sum())
                if (held < policy.minimum_validation_points_per_photo
                        or eligible - held < policy.minimum_training_points_per_photo
                        or held > policy.maximum_validation_share * eligible):
                    met = False
            tried.append({'grid': grid, 'every_row_meets_minimums': met})
            if met:
                chosen = grid
                break
        guaranteed = chosen is not None
        chosen = chosen if guaranteed else grids[-1]
        for row in rows:
            validation = _tile_validation(row['arrays']['xy'], lower, span, chosen)
            row['train'] = row['eligible'] & ~validation
            row['validation'] = row['eligible'] & validation
        ledger = {'photo_id': photo, 'method': 'fixed_4x4_spatial_tiles_mod4', 'basis': basis,
                  'lower_xy': lower.tolist(), 'span_xy': span.tolist(), 'validation_condition': '(tile_x+2*tile_y)%4==0'}
        if grids != (4,):
            ledger.update(method='adaptive_spatial_tiles_share_v2', grid=chosen, grids_tried=tried,
                          maximum_validation_share=policy.maximum_validation_share,
                          guarantee=('every eligible observation row keeps at least the policy minimum training and validation points '
                                     'and at most the maximum validation share'
                                     if guaranteed else 'not met at the finest grid; validation may be unmeasured or dominant for some rows'))
        split_ledger.append(ledger)
    grouped = {}
    for row in records:
        grouped.setdefault((row['photo_id'], row['region_id']), {}).setdefault(row['numeric_sha256'], []).append(row)
    groups = []
    aliases = []
    for key in sorted(grouped):
        representatives = []
        for digest, rows in sorted(grouped[key].items()):
            representatives.append(rows[0])
            aliases.append({'photo_id': key[0], 'region_id': key[1], 'numeric_sha256': digest, 'observation_ids': sorted(v['id'] for v in rows)})
        groups.append(representatives)
    count = int(np.prod([len(g) for g in groups], dtype=object))
    if count > policy.maximum_mask_branches:
        if defer_large_branches:
            return binding, records, [], aliases, split_ledger
        raise ValueError(f'Mask branch budget exceeded: {count} > {policy.maximum_mask_branches}; exploration would be incomplete')
    return binding, records, list(itertools.product(*groups)), aliases, split_ledger


def _tile_validation(xy, lower, span, grid):
    """Validation membership of pixel centers on a grid-by-grid tiling of the photo."""
    tile = np.minimum(grid-1, np.floor(grid*(np.asarray(xy, float)-lower)/span).astype(int))
    return (tile[:, 0]+2*tile[:, 1]) % 4 == 0


def _basis(v, knots=3):
    """Smoothstep blending weights over ``knots`` evenly spaced knots on [0, 1]; three reproduce the original basis."""
    segments = knots - 1
    scaled = np.asarray(v, float) * segments
    index = np.clip(np.floor(scaled).astype(int), 0, segments - 1)
    t = np.clip(scaled - index, 0, 1)
    weight = t * t * (3 - 2 * t)
    result = np.zeros((len(v), knots))
    result[np.arange(len(v)), index] = 1 - weight
    result[np.arange(len(v)), index + 1] = weight
    return result


def _angle_basis(angle):
    knots = np.array([0., 45., 75., 90.])
    index = np.minimum(np.searchsorted(knots, angle, side='right') - 1, 2)
    t = (angle - knots[index]) / (knots[index + 1] - knots[index])
    weight = t * t * (3 - 2 * t)
    result = np.zeros((len(angle), 4))
    result[np.arange(len(angle)), index] = 1 - weight
    result[np.arange(len(angle)), index + 1] = weight
    return result


def _encode_unclamped(linear):
    # Extended nonnegative sRGB before clipping/quantization. HDR predictions are
    # retained so a white endpoint imposes a lower bound, not equality to one.
    return 255 * np.where(linear <= .0031308, 12.92 * linear, 1.055 * np.maximum(linear, 0) ** (1 / 2.4) - .055)


def _interval_residual(prediction, code):
    lower, upper = code - .5, code + .5
    lower = np.where(code == 0, -np.inf, lower)
    upper = np.where(code == 255, np.inf, upper)
    return np.where(prediction < lower, prediction - lower, np.where(prediction > upper, prediction - upper, 0.))


def _robust_residual(residual):
    return residual * np.sqrt(2 / (np.sqrt(1 + residual * residual) + 1))


class _Model:
    def __init__(self, branch, family, lighting, rear_mode, policy, appearance_priors=None, group_id=None, *, shared_rear_response=False,
                 rear_response_hypothesis='free_rear_reflection'):
        self.branch, self.family, self.lighting, self.rear_mode, self.policy = branch, family, lighting, rear_mode, policy
        self.appearance_priors = appearance_priors
        if rear_response_hypothesis not in ('free_rear_reflection','weak_rear_reflection'):
            raise ValueError('Unknown rear-response hypothesis')
        self.rear_response_hypothesis=rear_response_hypothesis
        self.photo_priors = (appearance_priors or {}).get('photos', {})
        group_priors = (appearance_priors or {}).get('groups', {}).get(group_id, {})
        self.transmission_anchors=group_priors.get('transmission_anchors',[])
        self.density_anchors = [a for a in group_priors.get('density_anchors', [])
                               if family in a.get('applicable_families', ('uniform_tint', 'gradient_tint'))
                               and a.get('weight', 1.) > 0]
        self.photos = sorted({v['photo_id'] for v in branch})
        self.slices, self.lower, self.upper = {}, [], []
        self.gradient = family in ('gradient_tint', 'gradient_angular_mirror')
        self.angular = family in ('angular_mirror', 'gradient_angular_mirror')
        self.mirror = family not in ('uniform_tint', 'gradient_tint')
        self.rear_evidence=[] if self.gradient else [r for r in group_priors.get('rear_image_constraints',[]) if r['photo_id'] not in self.photos]
        self.knots = policy.gradient_density_keyframes if self.gradient else 1
        self._add('density', 3 * self.knots, 0, policy.maximum_density)
        if self.mirror:
            self._add('reflection', 9 if self.angular else 3, 0, 1)
        if shared_rear_response or self.rear_evidence or any(self.photo_priors.get(photo,{}).get('interface')=='rear' for photo in self.photos):
            self._add('rear_reflection_fraction',3,0,.15 if rear_response_hypothesis=='weak_rear_reflection' else 1)
        for photo in sorted({r['photo_id'] for r in self.rear_evidence}):
            rows=[r for r in self.rear_evidence if r['photo_id']==photo]
            key='rear-evidence:'+photo
            self._add((key,'rear-incidence'),1,max(r['incidence_range_degrees'][0] for r in rows),min(r['incidence_range_degrees'][1] for r in rows))
            self._add((key,'rear-light'),1,0,min(r['maximum_reflected_radiance'] for r in rows))
            bound=min(r['maximum_exposure_ratio'] for r in rows)
            self._add((key,'rear-gain'),1,-np.log(bound),np.log(bound))
        for p, photo in enumerate(self.photos):
            if lighting == 'semantic_softbox':
                if photo not in self.photo_priors:
                    raise ValueError('semantic_softbox requires bound appearance priors for every photo')
                self._add((photo, 'environment'), 1, 0, policy.maximum_environment_base_radiance)
                count = len(self.photo_priors[photo]['reflection_regions'])
                if count:
                    self._add((photo, 'softbox'), count, 0, policy.maximum_environment_base_radiance)
            else:
                self._add((photo, 'environment'), 3, 0 if lighting == 'constant' else 1e-5, policy.maximum_environment_base_radiance)
            if lighting == 'smooth':
                self._add((photo, 'shape'), 12, -policy.smooth_environment_log_bound, policy.smooth_environment_log_bound)
            if p:
                self._add((photo, 'exposure'), 1, -policy.maximum_exposure_stops * np.log(2), policy.maximum_exposure_stops * np.log(2))
                self._add((photo, 'wb'), 2, -policy.maximum_white_balance_log, policy.maximum_white_balance_log)
            if rear_mode == 'unknown_rear_color' and any(v['photo_id'] == photo and np.any(v['arrays']['rear_weight'][v['eligible']] > 0) for v in branch):
                self._add((photo, 'rear'), 3, 0, policy.maximum_unknown_rear_radiance)
        self.data = []
        for row in branch:
            eligible = row['eligible']; arrays = {k: v[eligible] for k, v in row['arrays'].items()}
            angle = arrays['angle']; rad = np.deg2rad(angle)
            d = arrays['direction']
            features = np.column_stack((d, d[:, 0] ** 2 - d[:, 1] ** 2)) if lighting == 'smooth' else None
            self.data.append({'observation': row, 'arrays': arrays, 'train': row['train'][eligible], 'validation': row['validation'][eligible],
                              'basis': _basis(arrays['v'], self.knots) if self.gradient else None, 'angle_basis': _angle_basis(angle),
                              'schlick': ((1 - np.cos(rad)) ** 5)[:, None],
                              'path': (1 / np.sqrt(1 - (np.sin(rad) / 1.5) ** 2))[:, None], 'features': features,
                              'softboxes': (_semantic_softbox_basis(arrays['xy'], row['image_size'],
                                            self.photo_priors[row['photo_id']]['reflection_regions'])
                                            if lighting == 'semantic_softbox' else None)})
        # Overlapping regions may reuse a photograph pixel. Its total training
        # weight stays one within the photo; semantic partitions cannot multiply
        # that same observation. Individual-region diagnostics remain separate.
        self.unique_training_pixels = {}
        for photo in self.photos:
            multiplicity = {}
            for d in self.data:
                if d['observation']['photo_id'] == photo:
                    for xy in d['arrays']['xy'][d['train']]:
                        key = tuple(xy);multiplicity[key] = multiplicity.get(key, 0) + 1
            self.unique_training_pixels[photo] = len(multiplicity)
            for d in self.data:
                if d['observation']['photo_id'] == photo:
                    d['train_weights'] = np.asarray([1 / np.sqrt(3 * len(multiplicity) * multiplicity[tuple(xy)])
                                                    for xy in d['arrays']['xy'][d['train']]])

    def _add(self, key, count, lower, upper):
        self.slices[key] = slice(len(self.lower), len(self.lower) + count)
        self.lower.extend([lower] * count); self.upper.extend([upper] * count)

    def initial(self, index):
        x = np.zeros(len(self.lower))
        starts = ((0., .04), (.7, .5), (2., .999999))[index % 3]
        x[self.slices['density']] = min(starts[0], self.policy.maximum_density)
        if self.mirror:
            x[self.slices['reflection']] = starts[1]
        if 'rear_reflection_fraction' in self.slices:
            x[self.slices['rear_reflection_fraction']]=.1
        for photo in self.photos:
            x[self.slices[(photo, 'environment')]] = min(1., self.policy.maximum_environment_base_radiance)
            if (photo, 'rear') in self.slices:
                x[self.slices[(photo, 'rear')]] = min(.3, self.policy.maximum_unknown_rear_radiance)
        if index >= 3:
            x[self.slices['density']] = self.policy.maximum_density * (index - 2) / (self.policy.starts - 1)
        for key,sl in self.slices.items():
            if isinstance(key,tuple) and key[1]=='rear-incidence':
                x[sl]=30.
            elif isinstance(key,tuple) and key[1]=='rear-light':
                x[sl]=min(.1,self.upper[sl.start])
        if self.rear_evidence and index==0:
            # A conditional starting point, not an equality or a measurement:
            # assume neutral unit exposure and weak reflected rear illumination.
            # The untouched starts still test contrary opaque explanations.
            color=np.median([np.asarray(r['linear_rgb_interval']).mean(axis=1) for r in self.rear_evidence],axis=0)
            backdrop=np.median([np.asarray(r['backdrop_linear_rgb_interval']).mean(axis=1) for r in self.rear_evidence],axis=0)
            estimate=np.clip(color/np.maximum(backdrop,.05),.002,.95)
            normal=x[self.slices['reflection']].reshape(-1,3)[0] if self.mirror else np.full(3,.04)
            x[self.slices['density']]=-np.log(np.clip(estimate/(1-normal),.002,1))
        if self.density_anchors and index == 0:
            # Only the first start uses measured anchors. The existing contrary
            # starts remain, so a wrong semantic interpretation is recoverable.
            positions = np.asarray([a['v'] for a in self.density_anchors])
            values = np.asarray([a['density_rgb'] for a in self.density_anchors])
            weights = np.asarray([a.get('weight', 1.) for a in self.density_anchors])[:, None]
            precision = weights / np.asarray([a['sigma'] for a in self.density_anchors])**2
            unique = np.unique(positions)
            means = np.asarray([(values[positions == v]*precision[positions == v]).sum(axis=0)
                                / precision[positions == v].sum(axis=0) for v in unique])
            if self.gradient:
                knots = np.linspace(0, 1, self.knots)
                initial_density = np.column_stack([np.interp(knots, unique, means[:, c]) for c in range(3)])
            else:
                initial_density = (values*precision).sum(axis=0) / precision.sum(axis=0)
            x[self.slices['density']] = initial_density.ravel()
        return np.clip(x, self.lower, self.upper)

    def predict(self, x, data):
        arrays, photo = data['arrays'], data['observation']['photo_id']
        knots = x[self.slices['density']].reshape(-1, 3)
        density = data['basis'] @ knots if self.gradient else np.broadcast_to(knots[0], arrays['code'].shape)
        if self.angular:
            reflection = data['angle_basis'] @ np.vstack((x[self.slices['reflection']].reshape(3, 3), np.ones(3)))
        else:
            normal = x[self.slices['reflection']] if self.mirror else np.full(3, .04)
            reflection = normal + (1 - normal) * data['schlick']
        transmission = (1 - reflection) * np.exp(-density * data['path'])
        if self.photo_priors.get(photo,{}).get('interface')=='rear':
            reflection=(1-transmission)*x[self.slices['rear_reflection_fraction']]
        background = arrays['background']
        if self.rear_mode == 'geometry_conditioned_rear':
            background = background * (1 - arrays['rear_weight'][:, None]) + np.nan_to_num(arrays['rear']) * arrays['rear_weight'][:, None]
        elif (photo, 'rear') in self.slices:
            background = background * (1 - arrays['rear_weight'][:, None]) + x[self.slices[(photo, 'rear')]] * arrays['rear_weight'][:, None]
        if self.lighting == 'semantic_softbox':
            scalar = np.full(len(arrays['code']), x[self.slices[(photo, 'environment')]][0])
            if (photo, 'softbox') in self.slices:
                scalar += data['softboxes'] @ x[self.slices[(photo, 'softbox')]]
            environment = scalar[:, None] * np.asarray(self.photo_priors[photo]['illuminant_rgb'])
        else:
            environment = np.broadcast_to(x[self.slices[(photo, 'environment')]], arrays['code'].shape)
        if self.lighting == 'smooth':
            environment = environment * np.exp(data['features'] @ x[self.slices[(photo, 'shape')]].reshape(4, 3))
        radiance = transmission * background + reflection * environment
        if (photo, 'exposure') in self.slices:
            a, b = x[self.slices[(photo, 'wb')]]
            radiance = radiance * np.exp(x[self.slices[(photo, 'exposure')]][0] + np.array([a, -a-b, b]))
        return _encode_unclamped(radiance)

    def material_regularization(self, x):
        parts = []
        if self.gradient:
            parts.extend(self.policy.density_curvature_penalty * np.diff(x[self.slices['density']].reshape(-1, 3), n=2, axis=0).ravel())
        if self.angular:
            values = np.vstack((x[self.slices['reflection']].reshape(3, 3), np.ones(3)))
            parts.extend(self.policy.reflectance_curvature_penalty * np.diff(values, n=2, axis=0).ravel())
        return np.asarray(parts)

    def material_anchor_penalties(self, x):
        parts = []
        if self.density_anchors:
            coordinates = np.asarray([a['v'] for a in self.density_anchors])
            values = np.asarray([a['density_rgb'] for a in self.density_anchors])
            sigma = np.asarray([a['sigma'] for a in self.density_anchors])
            weights = np.asarray([a.get('weight', 1.) for a in self.density_anchors])
            knots = x[self.slices['density']].reshape(-1, 3)
            predicted = _basis(coordinates, self.knots) @ knots if self.gradient else np.broadcast_to(knots[0], values.shape)
            error = (predicted-values) / sigma
            # Anchors are uncertain hypotheses. A robust soft residual limits a
            # contradictory anchor's influence; no family or RGB is forced.
            parts.extend((_robust_residual(error) * np.sqrt(weights[:, None] / (3*len(weights)))).ravel())
        for row in self.rear_evidence:
            key='rear-evidence:'+row['photo_id']
            angle=float(x[self.slices[(key,'rear-incidence')]][0])
            transmission=self.transmission(x,np.asarray([.5]),np.asarray([angle]))[0]
            # Reverse reflectance may differ, but cannot consume energy already
            # transmitted. It is shared rear material response, not a copied or
            # scaled front coating; the same fraction survives GLB export.
            rear_reflection=(1-transmission)*x[self.slices['rear_reflection_fraction']]
            backdrop=np.asarray(row['backdrop_linear_rgb_interval']).mean(axis=1)
            predicted=(transmission*backdrop+rear_reflection*x[self.slices[(key,'rear-light')]][0])*np.exp(x[self.slices[(key,'rear-gain')]][0])
            interval=np.asarray(row['linear_rgb_interval'])
            error=np.maximum(interval[:,0]-predicted,0)-np.maximum(predicted-interval[:,1],0)
            parts.extend((_robust_residual(error/row['sigma_linear_rgb'])*np.sqrt(row['weight']*.15/3)).ravel())
        for anchor in self.transmission_anchors:
            predicted=self.transmission(x,np.asarray([anchor['v']]),np.asarray([anchor['incidence_degrees']]))[0]
            interval=np.asarray(anchor['transmission_interval_rgb'])
            error=np.maximum(interval[:,0]-predicted,0)-np.maximum(predicted-interval[:,1],0)
            parts.extend((_robust_residual(error/anchor['sigma'])*np.sqrt(anchor['weight']/3)).ravel())
        return np.asarray(parts)

    def transmission(self,x,v,angle):
        knots=x[self.slices['density']].reshape(-1,3)
        density=_basis(v,self.knots)@knots if self.gradient else np.broadcast_to(knots[0],(len(v),3))
        if self.angular:
            reflection=_angle_basis(angle)@np.vstack((x[self.slices['reflection']].reshape(3,3),np.ones(3)))
        else:
            normal=x[self.slices['reflection']] if self.mirror else np.full(3,.04)
            reflection=normal+(1-normal)*(1-np.cos(np.deg2rad(angle)))[:,None]**5
        path=(1/np.sqrt(1-(np.sin(np.deg2rad(angle))/1.5)**2))[:,None]
        return (1-reflection)*np.exp(-density*path)

    def material_penalties(self, x):
        return np.concatenate((self.material_regularization(x), self.material_anchor_penalties(x)))

    def penalties(self, x):
        parts = list(self.material_penalties(x))
        for key, sl in self.slices.items():
            if isinstance(key, tuple) and key[1] in ('shape', 'exposure', 'wb', 'softbox'):
                parts.extend(self.policy.nuisance_penalty * x[sl])
        return np.asarray(parts)

    def residual(self, x):
        pieces = []
        for d in self.data:
            error = _interval_residual(self.predict(x, d), d['arrays']['code'])[d['train']] / self.policy.code_robust_scale
            pieces.append((_robust_residual(error) * d['train_weights'][:, None]).ravel())
        return np.concatenate([*pieces, self.penalties(x)])

    def appearance(self, x, roughness):
        # An optimizer resting on its zero bound leaves values like 1e-40 that a
        # float32 renderer rounds to zero while refusing nonzero inputs that do;
        # exactly zero is the honest value for them.
        x = np.where(np.abs(x) < GPU_ZERO, 0., np.asarray(x, dtype=float))
        density = x[self.slices['density']].reshape(-1, 3)
        positions = tuple(float(p) for p in np.linspace(0., 1., self.knots)) if self.gradient else (0.,)
        if self.angular:
            values = np.vstack((x[self.slices['reflection']].reshape(3, 3), np.ones(3)))
            angular = tuple(ReflectanceKeyframe(a, tuple(c)) for a, c in zip((0., 45., 75., 90.), values))
            normal = tuple(values[0])
        else:
            angular = None
            normal = tuple(x[self.slices['reflection']]) if self.mirror else (.04, .04, .04)
        rear=tuple(x[self.slices['rear_reflection_fraction']]) if 'rear_reflection_fraction' in self.slices else None
        return LensAppearance(tuple(DensityKeyframe(v, tuple(c)) for v, c in zip(positions, density)), normal, 1.5, roughness, angular,rear)

    def measurements(self, x):
        result = []
        for d in self.data:
            prediction = self.predict(x, d)
            error = np.abs(_interval_residual(prediction, d['arrays']['code']))
            row = {'observation_id': d['observation']['id'], 'photo_id': d['observation']['photo_id'], 'region_id': d['observation']['region_id']}
            for split in ('train', 'validation'):
                e = error[d[split]]
                row[split] = {'points': len(e), 'mean_absolute_interval_error_codes': float(e.mean()) if len(e) else None,
                              'p90_absolute_interval_error_codes': float(np.quantile(e, .9)) if len(e) else None,
                              'maximum_absolute_interval_error_codes': float(e.max()) if len(e) else None,
                              'fraction_channels_within_policy': float(np.mean(e <= self.policy.photo_tolerance_codes)) if len(e) else None}
            result.append(row)
        return result

    def nuisance(self, x):
        result = []
        for photo in self.photos:
            row = {'photo_id': photo, 'environment_rgb': x[self.slices[(photo, 'environment')]].tolist(),
                   'exposure_multiplier': 1., 'white_balance_rgb': [1., 1., 1.]}
            if self.lighting == 'semantic_softbox':
                prior = self.photo_priors[photo]
                scalar = float(x[self.slices[(photo, 'environment')]][0])
                row.update(environment_rgb=(scalar*np.asarray(prior['illuminant_rgb'])).tolist(),
                           environment_scalar=scalar, illuminant_rgb=prior['illuminant_rgb'],
                           illumination_hypothesis=prior['illumination_hypothesis'],
                           softbox_amplitudes=(x[self.slices[(photo, 'softbox')]].tolist()
                                              if (photo, 'softbox') in self.slices else []),
                           softbox_regions=prior['reflection_regions'],
                           environment_basis='neutral_chromatic_scalar_plus_feathered_image_rectangles',
                           nuisance_exported_to_material=False,
                           maximum_field_channel_radiance=self.policy.maximum_environment_base_radiance*(1+len(prior['reflection_regions'])))
            if (photo, 'shape') in self.slices:
                row['log_environment_coefficients'] = x[self.slices[(photo, 'shape')]].reshape(4, 3).tolist()
                row['environment_basis'] = ['reflected_x', 'reflected_y', 'reflected_z', 'reflected_x_squared_minus_y_squared']
            if (photo, 'exposure') in self.slices:
                a, b = x[self.slices[(photo, 'wb')]]
                row['exposure_multiplier'] = float(np.exp(x[self.slices[(photo, 'exposure')]][0]))
                row['white_balance_rgb'] = np.exp([a, -a-b, b]).tolist()
            if (photo, 'rear') in self.slices:
                row['unknown_rear_rgb'] = x[self.slices[(photo, 'rear')]].tolist()
            if self.photo_priors.get(photo,{}).get('interface')=='rear':
                row['rear_reflection_fraction']=x[self.slices['rear_reflection_fraction']].tolist()
                row['rear_interface_model']='reciprocal_transmission_independent_energy_bounded_reflection'
            result.append(row)
        for photo in sorted({r['photo_id'] for r in self.rear_evidence}):
            key='rear-evidence:'+photo
            result.append({'photo_id':key,'source_photo_id':photo,'nuisance_kind':'rear_radiance_constraint',
                'incidence_hypothesis_degrees':float(x[self.slices[(key,'rear-incidence')]][0]),
                'reflection_fraction_of_nontransmitted_energy':x[self.slices['rear_reflection_fraction']].tolist(),
                'illumination_linear':float(x[self.slices[(key,'rear-light')]][0]),
                'exposure_multiplier':float(np.exp(x[self.slices[(key,'rear-gain')]][0])),
                'rear_reflection_exported_as':'rear_reflection_fraction_rgb'})
        return result


def candidate_ar_probe_predictions(appearance: LensAppearance | dict) -> dict:
    """Canonical local R/T probes; roughness/environment convolution is unmeasured.

    These fixed probes expose explored prediction disagreement, not a certified
    confidence interval, real face rendering, or production-renderer acceptance.
    """
    if isinstance(appearance, dict):
        appearance = LensAppearance.from_dict(appearance)
    if not isinstance(appearance, LensAppearance):
        raise ValueError('Expected a canonical LensAppearance')
    coords = np.array(list(itertools.product((0., .25, .5, .75, 1.), (0., 30., 60., 75.))))
    sample = appearance.evaluate(coords[:, 0], coords[:, 1])
    backgrounds = np.array([[0., 0., 0.], [.18, .18, .18], [.45, .25, .18], [1., 1., 1.]])
    environments = np.array([[0., 0., 0.], [1., 1., 1.], [.8, .2, .05]])
    values = sample.transmission_rgb[:, None, None, :] * backgrounds[None, :, None, :] + sample.reflectance_rgb[:, None, None, :] * environments[None, None, :, :]
    return {'coordinates_v_angle': coords.tolist(), 'background_rgb': backgrounds.tolist(), 'environment_rgb': environments.tolist(),
            'reflectance_rgb': sample.reflectance_rgb.tolist(), 'transmission_rgb': sample.transmission_rgb.tolist(),
            'composed_linear_rgb': values.reshape(-1, 3).tolist(), 'roughness_blur_effect': 'unmeasured_by_local_response_probe'}


def rear_mode_options(rows, policy) -> tuple[str, ...]:
    """Rear explanations to explore for one observation branch under the policy.

    Under 'excluded', rear-content samples are ineligible, so the single mode
    names that exclusion whenever any row carried rear content; otherwise the
    branch has no rear geometry at all. Under 'explained', the earlier modes.
    """
    has_rear = any(np.any(r['arrays']['rear_weight'][r['eligible']] > 0) for r in rows)
    if policy.rear_content == 'excluded':
        return (REAR_CONTENT_EXCLUDED,) if any(r.get('rear_content') for r in rows) else ('rear_geometry_unknown_backdrop_only',)
    if not has_rear:
        return ('rear_geometry_unknown_backdrop_only',)
    known_rear = all(np.isfinite(r['arrays']['rear'][r['eligible'] & (r['arrays']['rear_weight'] > 0)]).all() for r in rows)
    return ('geometry_conditioned_rear', 'unknown_rear_color') if known_rear else ('unknown_rear_color',)


def fit_photo_lens_candidates(observations: list[dict], *, surface_binding: dict,
                            policy: PhotoLensFitPolicy = PhotoLensFitPolicy(), appearance_priors: dict | None = None) -> dict:
    """Return finite canonical material candidates without selecting or identifying one.

    Budgets fail explicitly before optimization. Every successful numerical start
    remains represented, including unresolved/high-error fits for diagnostic asset
    previews. Prediction envelopes prefer converged, photo-policy-compatible fits;
    when none exist, they are explicitly only diagnostic failed/unresolved fits.
    A fit-policy match is neither physical identification nor AR acceptance.
    """
    if not isinstance(policy, PhotoLensFitPolicy):
        raise ValueError('policy must be PhotoLensFitPolicy')
    from .grounded_fit_support import apply_grounded_fit_support
    observations=apply_grounded_fit_support([{'surface_binding':surface_binding,'observations':observations}],appearance_priors)[0]['observations']
    binding, records, branches, aliases, split_ledger = _prepare(observations, surface_binding, policy)
    appearance_priors = validate_appearance_priors(appearance_priors, records, {binding['material_group_id']:binding})
    policy = _prior_policy(policy, appearance_priors)
    if 'semantic_softbox' in policy.lighting_families and appearance_priors is None:
        raise ValueError('semantic_softbox requires appearance_priors')
    coverage = []
    for r in records:
        eligible = r['eligible']
        coverage.append({'observation_id': r['id'], 'samples': len(eligible), 'eligible': int(eligible.sum()),
                         'excluded_nonopaque': int(np.count_nonzero(r['arrays']['alpha'] != 255)),
                         'excluded_unknown_coordinates': int(np.count_nonzero(~np.isfinite(r['arrays']['v']) | ~np.isfinite(r['arrays']['angle']))),
                         'excluded_back_interface': int(np.count_nonzero(r['arrays']['angle'] >= 90)),
                         'excluded_image_grounding':int(np.count_nonzero(r['arrays'].get('image_eligible',np.ones(len(r['eligible'])))==0)),
                         'rear_content_samples': int(np.count_nonzero(r['arrays']['rear_weight'] > 0)),
                         'excluded_rear_content': int(np.count_nonzero(r['arrays']['rear_weight'] > 0)) if policy.rear_content == 'excluded' else 0,
                         'train': int(r['train'].sum()), 'validation': int(r['validation'].sum()),
                         'numeric_sha256': r['numeric_sha256'], 'source_sha256': r['source_sha256'], 'provenance': r['provenance']})
    input_identity = {'surface_binding': binding, 'policy': asdict(policy), 'observations': [
        {key: r[key] for key in ('id', 'photo_id', 'region_id', 'hypothesis_id', 'source_sha256', 'provenance', 'numeric_sha256')}
        for r in records]}
    if appearance_priors is not None:
        input_identity['appearance_priors'] = appearance_priors
    input_sha = _hash(input_identity)
    configurations, unsupported = [], []
    for branch in branches:
        ids = [r['id'] for r in branch]
        photos = sorted({r['photo_id'] for r in branch})
        if any(len({tuple(xy) for r in branch if r['photo_id'] == p for xy in r['arrays']['xy'][r['train']]}) < policy.minimum_training_points_per_photo for p in photos):
            unsupported.append({'observations': ids, 'reason': 'insufficient_training_support'})
            continue
        has_rear = any(np.any(r['arrays']['rear_weight'][r['eligible']] > 0) for r in branch)
        known_rear = all(np.isfinite(r['arrays']['rear'][r['eligible'] & (r['arrays']['rear_weight'] > 0)]).all() for r in branch)
        rear_modes = rear_mode_options(branch, policy)
        if has_rear and not known_rear:
            unsupported.append({'observations': ids, 'rear': 'geometry_conditioned_rear', 'reason': 'rear_color_unknown_at_positive_geometry_weight'})
        for lighting in policy.lighting_families:
            if lighting == 'semantic_softbox' and any(r['image_size'] is None for r in branch):
                unsupported.append({'observations': ids, 'lighting': lighting, 'reason': 'explicit_source_image_size_unavailable'})
                continue
            if lighting == 'smooth' and any(not np.isfinite(r['arrays']['direction'][r['eligible']]).all() for r in branch):
                unsupported.append({'observations': ids, 'lighting': lighting, 'reason': 'complete_reflected_directions_unavailable'})
                continue
            for rear, family in itertools.product(rear_modes, policy.families):
                configurations.append((branch, family, lighting, rear))
    runs = len(configurations) * policy.starts
    if runs > policy.maximum_optimization_runs:
        raise ValueError(f'Optimization budget exceeded: {runs} > {policy.maximum_optimization_runs}; exploration would be incomplete')
    candidates, failures = [], []
    environment_bounds = _environment_bounds(policy)
    for branch, family, lighting, rear in configurations:
        model = _Model(branch, family, lighting, rear, policy, appearance_priors, binding['material_group_id'])
        for start in range(policy.starts):
            try:
                fitted = least_squares(model.residual, model.initial(start), bounds=(model.lower, model.upper),
                                       max_nfev=policy.max_nfev, ftol=1e-7, xtol=1e-7, gtol=1e-7)
                x = fitted.x
                if not np.isfinite(x).all() or not np.isfinite(model.residual(x)).all():
                    raise FloatingPointError('Nonfinite optimization result')
                measurements = model.measurements(x)
                validation_supported = all(len({tuple(xy) for r in branch if r['photo_id'] == p for xy in r['arrays']['xy'][r['validation']]}) >= policy.minimum_validation_points_per_photo for p in model.photos)
                # Every observed region with support must satisfy the same policy;
                # a large easy lens cannot conceal a small incompatible region.
                compared = [m[s] for m in measurements for s in ('train', 'validation') if m[s]['points']]
                within = validation_supported and all(m['fraction_channels_within_policy'] >= policy.required_within_tolerance_fraction for m in compared)
                bound_indices = np.flatnonzero((x - np.asarray(model.lower) < 1e-5) | (np.asarray(model.upper) - x < 1e-5)).tolist()
                for roughness in policy.roughness_values:
                    appearance = model.appearance(x, roughness)
                    assumptions = {'family': family, 'lighting': lighting, 'rear': rear, 'observations': [r['id'] for r in branch],
                                   'roughness': roughness, 'start': start,
                                   'maximum_environment_base_radiance': policy.maximum_environment_base_radiance,
                                   'maximum_effective_environment_channel_radiance': (policy.maximum_environment_base_radiance*(1+MAXIMUM_SEMANTIC_SOFTBOXES)
                                       if lighting == 'semantic_softbox' else environment_bounds[
                                       'smooth_field_maximum_channel_radiance' if lighting == 'smooth' else 'constant_field_maximum_channel_radiance']),
                                   'environment_radiance_bound_scope': environment_bounds['scope']}
                    candidates.append({'candidate_id': _hash({'input': input_sha, 'assumptions': assumptions, 'appearance': appearance.to_dict()})[:24],
                        'appearance': appearance.to_dict(), 'assumptions': assumptions,
                        'material_group_id': binding['material_group_id'], 'parameter_identification': 'unmeasured',
                        'roughness_source': 'fixed_prior_reuses_photo_objective_without_refit',
                        'photo_policy_status': 'within_declared_policy' if within else 'outside_declared_policy' if validation_supported else 'validation_unmeasured',
                        'optimizer': {'converged': bool(fitted.success), 'status': int(fitted.status), 'evaluations': int(fitted.nfev),
                                      'objective_including_priors': float(fitted.cost), 'parameter_count': len(x), 'bound_active_parameter_indices': bound_indices},
                        'nuisance': model.nuisance(x), 'photo_measurements': measurements,
                        'unique_training_pixels_by_photo': model.unique_training_pixels,
                        'ar_probes': candidate_ar_probe_predictions(appearance)})
            except (ValueError, FloatingPointError, OverflowError, np.linalg.LinAlgError) as error:
                failures.append({'family': family, 'lighting': lighting, 'rear': rear, 'observations': [r['id'] for r in branch], 'start': start,
                                 'reason': type(error).__name__ + ': ' + str(error)})
    compatible = [c for c in candidates if c['photo_policy_status'] == 'within_declared_policy' and c['optimizer']['converged']]
    envelope_candidates = compatible or candidates
    if envelope_candidates:
        values = np.asarray([c['ar_probes']['composed_linear_rgb'] for c in envelope_candidates])
        lower, upper = values.min(axis=0), values.max(axis=0)
        spread = float((upper-lower).max())
        envelope = {'scope': 'explored_converged_photo_policy_candidates' if compatible else 'diagnostic_candidates_no_converged_policy_match',
                    'candidate_ids': [c['candidate_id'] for c in envelope_candidates], 'minimum_linear_rgb': lower.tolist(), 'maximum_linear_rgb': upper.tolist(),
                    'maximum_channel_spread': spread, 'response_status': 'explored_responses_disagree' if spread > policy.probe_agreement_tolerance_linear else 'explored_responses_agree_within_policy',
                    'tolerance_linear': policy.probe_agreement_tolerance_linear, 'not_a_certified_uncertainty_bound': True}
    else:
        envelope = None
    unresolved = bool(failures) or any(not c['optimizer']['converged'] for c in candidates)
    diagnosis = ('photo_compatible_explanations_available' if compatible else 'optimization_unresolved' if unresolved else
                 'validation_unmeasured' if any(c['photo_policy_status'] == 'validation_unmeasured' for c in candidates) else
                 'model_mismatch_under_declared_policy' if candidates else 'no_supported_fit')
    result = {'schema_version': 1, 'method': 'uncalibrated_photo_lens_ensemble_v1', 'status': 'candidates_available' if candidates else 'no_supported_candidates',
              'diagnosis': diagnosis, 'parameter_identification': 'unmeasured', 'surface_binding': binding, 'input_sha256': input_sha,
              'policy': asdict(policy), 'coverage': coverage, 'duplicate_hypothesis_aliases': aliases, 'spatial_split': split_ledger,
              'environment_bounds': environment_bounds,
              'validation_parameters_frozen': True, 'photometric_calibration': 'unmeasured',
              'exploration': {'mask_branches': len(branches), 'optimization_runs': runs, 'roughness_does_not_multiply_fit_runs': True,
                              'unresolved_optimizer_runs': len(failures) + sum(not c['optimizer']['converged'] for c in candidates) // len(policy.roughness_values),
                              'unsupported_configurations': unsupported, 'failed_runs': failures},
              'candidates': candidates, 'ar_prediction_envelope': envelope,
              'limitations': ['Source bytes and correspondence are caller-bound, not verified from supplied arrays.',
                  'Shared per-photo lighting exists only within this material-group call.',
                  'Background, rear visibility/color, exposure and white balance remain conditional hypotheses.',
                  'The first photo fixes the exposure/white-balance gauge; this is not a calibration.',
                  'Unknown rear geometry uses only the supplied backdrop and can produce structured mismatch.',
                  'Rear-content samples are excluded from fitting and policy under rear_content=excluded; the fitted lens is unmeasured over rear geometry.',
                  'The smooth environment is a bounded effective field, not recovered physical illumination.',
                  'No automatic hypothesis/material selection, parameter identification or AR acceptance is made.',
                  'AR probes measure local canonical response; roughness blur, face composition and full renderer appearance require actual asset previews.',
                  'A finite ensemble and local optimizers do not prove every admissible explanation was found.']}
    if appearance_priors is not None:
        result['appearance_priors'] = appearance_priors
        result['appearance_priors_sha256'] = _hash(appearance_priors)
        result['validation_scope'] = 'conditional_on_full_photo_semantic_and_numeric_priors_not_independent_holdout'
    return _json(result, 'result')
