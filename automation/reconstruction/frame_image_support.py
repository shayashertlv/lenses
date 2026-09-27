"""Image-only membership guard for conditional opaque-frame color sampling.

Class 1 is corroborated foreground support, never a semantic frame label.
Unknown pixels retain their original texture and cannot earn surface coverage.
"""
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from .observations import observe_image
from .region_proposals import _prompts


METHOD = 'independent_photo_frame_sampling_support_v1'
UNKNOWN, SUPPORTED_FOREGROUND, AUTHORED_EXTERIOR = 0, 1, 2


@dataclass(frozen=True)
class FrameImageSupportPolicy:
    erosion_pixels: int = 2
    maximum_proposal_fraction: float = .70

    def __post_init__(self):
        if type(self.erosion_pixels) is not int or not 1 <= self.erosion_pixels <= 8:
            raise ValueError('Frame support erosion must be 1 through 8 native pixels')
        if not np.isfinite(self.maximum_proposal_fraction) or not 0 < self.maximum_proposal_fraction <= .70:
            raise ValueError('Frame support proposal fraction must be positive and at most .70')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, expected, pins):
    path = Path(path).resolve(); raw = path.read_bytes()
    if _sha(raw) != expected:
        raise ValueError('Frame image support source or mask hash changed')
    pins[str(path)] = expected
    return raw


def _child(folder, relative):
    folder = Path(folder).resolve(); path = (folder / relative).resolve()
    if not path.is_relative_to(folder):
        raise ValueError('Frame support artifact path escapes its source directory')
    return path


def _mask(folder, reference, shape, pins):
    raw = _read(_child(folder, reference['path']), reference['sha256'], pins)
    with Image.open(io.BytesIO(raw)) as image:
        if image.mode != 'L':
            raise ValueError('Frame support masks require lossless binary native-grid images')
        pixels = np.asarray(image).copy()
    if pixels.shape != shape or not np.isin(pixels, [0, 255]).all():
        raise ValueError('Frame support mask grid or binary values changed')
    return pixels == 255


def build_frame_image_support(photo, region_directory, output=None, *, pins=None,
                              policy=FrameImageSupportPolicy()):
    """Replay independent contrast-object masks; never use projected positives.

    ``region_directory`` is the directory containing the main region report.
    No model, camera, role, AI confidence, mask selection or network call is used.
    Authored alpha supports image membership only, not physical opacity or T.
    """
    if not isinstance(policy, FrameImageSupportPolicy):
        raise ValueError('Supply a FrameImageSupportPolicy')
    pins = {} if pins is None else pins
    raw = _read(photo['source'], photo['source_sha256'], pins)
    with Image.open(io.BytesIO(raw)) as image:
        if image.info.get('icc_profile') or image.getexif().get(274, 1) != 1:
            raise ValueError('Frame support needs normalized source photographs')
        rgba = np.asarray(image.convert('RGBA')).copy()
    shape = rgba.shape[:2]
    if photo['image_size'] != [shape[1], shape[0]]:
        raise ValueError('Frame support source pixel grid changed')
    alpha = rgba[:, :, 3]
    membership = np.zeros(shape, np.uint8)
    foreground = np.zeros(shape, bool)
    reasons, alternatives = [], []
    basis = 'unavailable_independent_image_support'
    observation = observe_image(rgba)
    regions = photo.get('regions', [])
    folder = _child(region_directory, photo['id'])
    if np.any(alpha != 255):
        # Pixels outside an authored cutout are a genuine exterior; a white
        # opaque logo inside can remain supported without a contrast test.
        membership[alpha == 0] = AUTHORED_EXTERIOR
        if observation.status == 'measured':
            foreground = alpha == 255
            basis = 'authored_alpha_interior_geometry_support_only'
        else:
            reasons += ['authored_alpha_support_unreliable', *observation.reasons]
    elif observation.status != 'measured':
        reasons += ['studio_contrast_unreliable', *observation.reasons]
    else:
        independent = [row for row in regions if row.get('kind') == 'contrast_object_region'
                       and row.get('id') == 'contrast-object']
        if len(independent) > 1:
            raise ValueError('Duplicate image-only contrast-object support')
        if not independent:
            reasons.append('no_independent_object_region_alternatives')
        else:
            row = independent[0]
            region_folder = _child(folder, row.get('directory', row['id']))
            prior = _mask(region_folder, row['prior_mask'], shape, pins)
            if (row.get('prior', {}).get('basis') != observation.method
                    or not np.array_equal(prior, observation.mask)
                    or row.get('coordinate_fields') is not None
                    or row.get('prompts') != _prompts(observation.mask)):
                raise ValueError('Frame support prior/prompts are not reproducible image-only evidence')
            variants = row.get('alternatives', [])
            if not variants:
                reasons.append('independent_object_engine_unavailable')
            elif len(variants) != 5 or any(len(items) != 3 for items in variants):
                raise ValueError('Frame support requires all five prompts and three alternatives')
            else:
                masks = []
                for prompt_index, items in enumerate(variants):
                    if sorted(i.get('decoder_index', -1) for i in items) != [0, 1, 2]:
                        raise ValueError('Frame support alternatives were selected or duplicated')
                    for item in items:
                        mask = _mask(region_folder, item['mask'], shape, pins)
                        usable = bool(mask.any() and mask.mean() <= policy.maximum_proposal_fraction
                                      and not (mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any()))
                        alternatives.append({'prompt_index': prompt_index, 'decoder_index': item['decoder_index'],
                                             'mask': item['mask'], 'usable': usable})
                        masks.append(mask)
                # Do not discard inconvenient alternatives to increase support.
                if all(item['usable'] for item in alternatives):
                    contrast = np.sqrt(np.mean((rgba[:, :, :3].astype(float)
                                               - np.asarray(observation.metrics['background_rgb'])) ** 2, axis=2))
                    strong = contrast > observation.metrics['strong_threshold_rgb_rms']
                    foreground = np.logical_and.reduce(masks) & strong
                    basis = 'all_image_only_prompt_alternatives_intersect_strong_source_contrast'
                else:
                    reasons.append('empty_border_or_excessive_object_alternative')
    # Candidate optical regions may only subtract support. They never establish
    # photographed frame membership. All ambiguity thus makes this guard stricter.
    excluded = np.zeros(shape, bool)
    for row in regions:
        if row.get('kind') == 'candidate_optical_region':
            region_folder = _child(folder, row.get('directory', row['id']))
            excluded |= _mask(region_folder, row['prior_mask'], shape, pins)
    before_exclusion = foreground.copy()
    foreground &= ~ndimage.binary_dilation(excluded, iterations=policy.erosion_pixels)
    foreground &= alpha == 255
    foreground = ndimage.binary_erosion(foreground, iterations=policy.erosion_pixels)
    membership[foreground] = SUPPORTED_FOREGROUND
    report = {'schema_version': 1, 'method': METHOD, 'photo_id': photo['id'],
        'source': str(Path(photo['source']).resolve()),
        'source_sha256': photo['source_sha256'], 'decoded_rgba_sha256': _sha(rgba.tobytes()),
        'image_size': photo['image_size'], 'policy': asdict(policy), 'basis': basis,
        'status': 'conditional_foreground_support_available' if foreground.any() else 'unmeasured',
        'membership_values': {'unknown': UNKNOWN, 'supported_foreground': SUPPORTED_FOREGROUND,
                              'authored_exterior': AUTHORED_EXTERIOR},
        'counts': {'supported_foreground': int(foreground.sum()), 'unknown': int((membership == UNKNOWN).sum()),
                   'authored_exterior': int((membership == AUTHORED_EXTERIOR).sum()),
                   'nonopaque_alpha': int((alpha != 255).sum()),
                   'optical_prior_exclusion': int((before_exclusion & excluded).sum())},
        'reasons': reasons, 'alternatives': alternatives,
        'scope': 'Independent image membership guard for conditional opaque-frame sampling; no semantic frame identity or physical opacity is verified.',
        'limitations': ['All proposal alternatives can share an error; support is corroboration, not semantic truth.',
                       'Opaque low-contrast and transparent frame regions remain unknown without authored image support.',
                       'Unsupported texture is preserved and receives no correction or observed-area credit.']}
    if output is not None:
        destination = Path(output).resolve()
        if destination.exists() and any(destination.iterdir()):
            raise ValueError('Frame support output must be fresh')
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / 'membership.png'; Image.fromarray(membership).save(path)
        report['membership'] = {'path': str(path), 'sha256': _sha(path.read_bytes())}
        (destination / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return {'membership': membership, 'report': report}
