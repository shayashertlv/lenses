"""Read frozen, image-only aperture hypotheses without running neural models.

Known-domain masks describe where a model made a prediction, not where its
semantics are correct. Components, variants and SAM decoders stay separate.
The mandatory report digests bind the caller's chosen evidence; they are not a
certificate of lens identity or of the quality of the upstream experiments.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

import numpy as np
from PIL import Image
from scipy import ndimage

from reconstruction.input_bundle import _normalize


METHOD = 'frozen_photo_aperture_hypotheses_v1'
LIMITS = {'report_bytes': 16_000_000, 'artifact_bytes': 64_000_000,
          'captured_bytes': 256_000_000, 'npz_expanded_bytes': 4_000_000,
          'native_pixels': 16_000_000, 'photos': 32,
          'hypotheses': 512, 'returned_boolean_cells': 256_000_000}
VARIANTS = ['full', 'contrast_crop']
BASELINE_POLICY = {
    'input_size': [256, 256], 'variants': VARIANTS,
    'threshold': 'logit > 0; uncalibrated class prediction',
    'native_logit_interpolation': 'bilinear within crop only; outside crop is unknown',
}
REFINEMENT_POLICY = {
    'upstream_variants': VARIANTS,
    'upstream_head': 'lenses_logits; original unflipped prediction only',
    'threshold': 'logit > 0; uncalibrated semantic proposal',
    'component_connectivity': 4, 'minimum_component_grid_pixels': 16,
    'maximum_retained_components_per_variant': 32,
    'over_capacity_behavior': 'entire variant unsupported; no top-N truncation',
    'prompt_bbox_padding_fraction_each_side_per_axis': .10,
    'prompt_bbox_minimum_native_span': 1.,
    'positive_point': 'farthest interior 256-grid pixel under Euclidean distance to padded zero exterior; ties first row then column',
    'point_mapping': 'recorded grid_to_native_pixel_center; clamp to normalized native pixel-center domain',
    'bbox_mapping': 'component grid-cell bounds via recorded center transform plus half-pixel boundary conversion; expand 10 percent each side and clamp',
    'negative_points': 0,
    'sam_call_scope': 'one predict call containing every retained component from both variants per nonempty photo; one image embedding',
    'sam_decoder_alternatives': 3,
    'coarse_component_native_mapping': 'nearest source grid pixel under the recorded crop cell mapping; outside source crop unknown',
    'crossproposal_overlap': 'exact native pixels for every pair of SAM masks belonging to distinct proposals, all decoder combinations',
    'maximum_native_pixels_per_photo': 16_000_000, 'maximum_photos': 32,
    'display': 'per-variant unions by decoder index are composites only; decoder index does not establish cross-prompt identity',
}
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _equal(left, right, message):
    if _json(left) != _json(right):
        raise ValueError(message)


def _digest(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError('Expected a lowercase SHA-256 digest')
    return value


def _child(folder, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Artifact path must be relative')
    path = (folder / relative).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError('Artifact path escapes its evidence directory')
    return path


class _Capture:
    """One immutable byte snapshot per resolved input path, with bounded reads."""
    def __init__(self):
        self.raw = {}
        self.records = {}
        self.total = 0

    def read(self, path, expected=None, *, report=False):
        path = Path(path).resolve()
        if expected is not None:
            _digest(expected)
        key = str(path)
        if key not in self.raw:
            bound = LIMITS['report_bytes' if report else 'artifact_bytes']
            bound = min(bound, LIMITS['captured_bytes'] - self.total)
            with path.open('rb') as stream:
                data = stream.read(bound + 1)
            if len(data) > bound:
                raise ValueError('Evidence input byte capacity exceeded')
            self.raw[key] = data
            self.total += len(data)
            self.records[key] = {'path': key, 'sha256': _sha(data), 'bytes': len(data)}
        data = self.raw[key]
        if expected is not None and self.records[key]['sha256'] != expected:
            raise ValueError('Evidence input SHA-256 mismatch: ' + key)
        return data

    def artifact(self, folder, record):
        return self.read(_child(folder, record['path']), record['sha256'])


def _parse(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('Duplicate JSON key')
            value[key] = item
        return value
    def constant(_):
        raise ValueError('Nonfinite JSON number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _npz_array(raw, name, *, labels=False):
    """Validate the NPY header before permitting an ndarray allocation."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        expected = {'labels.npy'} if labels else {
            'lenses_logits.npy', 'lenses_flip_aligned_logits.npy',
            'frames_logits.npy', 'frames_flip_aligned_logits.npy'}
        if len(entries) != len(expected) or {e.filename for e in entries} != expected:
            raise ValueError('Unsupported NPZ inventory')
        if sum(e.file_size for e in entries) > LIMITS['npz_expanded_bytes']:
            raise ValueError('Expanded NPZ capacity exceeded')
        data = archive.read(name + '.npy')
    stream = io.BytesIO(data)
    version = np.lib.format.read_magic(stream)
    if version == (1, 0):
        shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream)
    elif version == (2, 0):
        shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream)
    else:
        raise ValueError('Unsupported NPY version')
    if (shape != (256, 256) or fortran or dtype.kind not in ('iu' if labels else 'f')
            or dtype.itemsize not in (4, 8) or len(data)-stream.tell() != 256*256*dtype.itemsize):
        raise ValueError('Unsupported saved grid shape or dtype')
    result = np.frombuffer(data, dtype=dtype, offset=stream.tell()).reshape(shape).copy()
    if not np.isfinite(result).all():
        raise ValueError('Nonfinite saved grid')
    return result


def _image(raw, size=None, *, mask=False):
    with Image.open(io.BytesIO(raw)) as image:
        if (getattr(image, 'n_frames', 1) != 1 or image.width*image.height > LIMITS['native_pixels']
                or min(image.size) < 1 or (size is not None and image.size != tuple(size))):
            raise ValueError('Unsupported image dimensions or frame count')
        if mask:
            if image.format != 'PNG' or image.mode != 'L':
                raise ValueError('Expected saved 8-bit grayscale PNG mask')
            values = np.array(image)
            if not np.isin(values, [0, 255]).all():
                raise ValueError('Mask has nonbinary pixels')
            return values != 0
        if image.mode not in ('RGB', 'RGBA') or image.getexif().get(274, 1) != 1:
            raise ValueError('Expected upright normalized RGB/RGBA image')
        image.load()
        if image.convert('RGBA').getchannel('A').getextrema() != (255, 255):
            raise ValueError('Transparent normalized images are unsupported')
        return image.size


def _components(logits, variant, size):
    box = variant['crop_xyxy_exclusive']
    width, height = size
    if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
            or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
        raise ValueError('Invalid semantic crop')
    x0, y0, x1, y1 = box
    sx, sy = (x1-x0)/256, (y1-y0)/256
    matrix = np.array([[sx, 0, x0+sx/2-.5], [0, sy, y0+sy/2-.5], [0, 0, 1]])
    if not np.array_equal(np.asarray(variant['grid_to_native_pixel_center']), matrix):
        raise ValueError('Semantic crop affine mismatch')
    if variant['name'] == 'full' and box != [0, 0, width, height]:
        raise ValueError('Full-image proposal has a cropped domain')
    labels, count = ndimage.label(logits > 0, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
    sizes = np.bincount(labels.ravel(), minlength=count+1)[1:]
    eligible = int(np.count_nonzero(sizes >= 16))
    if eligible > 32:
        raise ValueError('Unsupported upstream component capacity; no truncation')
    ledger = []
    for component, (ys, xs) in enumerate(ndimage.find_objects(labels), 1):
        inside = labels[ys, xs] == component
        distance = ndimage.distance_transform_edt(np.pad(inside, 1))[1:-1, 1:-1]
        local_y, local_x = np.unravel_index(np.argmax(distance), inside.shape)
        point_grid = [int(xs.start+local_x), int(ys.start+local_y)]
        point = (matrix @ [*point_grid, 1])[:2]
        clipped = np.clip(point, [0., 0.], [width-1., height-1.])
        lower = (matrix @ [xs.start-.5, ys.start-.5, 1])[:2]+.5
        upper = (matrix @ [xs.stop-.5, ys.stop-.5, 1])[:2]+.5
        padding = .10*(upper-lower)
        lower, upper = np.maximum(0., lower-padding), np.minimum([width, height], upper+padding)
        center, span = (lower+upper)/2, np.maximum(1., upper-lower)
        lower = np.minimum(np.maximum(0., center-span/2), np.array([width, height])-span)
        upper = lower+span
        pixels = int(sizes[component-1])
        row = {'id': f"{variant['name']}-component-{component:03d}", 'component_label': component,
               'grid_pixels': pixels, 'grid_bbox_xyxy_exclusive': [xs.start, ys.start, xs.stop, ys.stop],
               'grid_positive_point_xy': point_grid, 'interior_distance_grid_pixels': float(distance[local_y, local_x]),
               'point_was_clamped': bool(np.any(point != clipped)),
               'status': 'retained_prompt' if pixels >= 16 else 'omitted_below_minimum_grid_support',
               'semantic_identity': 'unverified_photo_semantic_proposal'}
        if pixels >= 16:
            row['prompt'] = {'bbox_xyxy': [*lower.tolist(), *upper.tolist()],
                             'points_xy': [clipped.tolist()], 'point_labels': [1]}
        ledger.append(row)
    summary = {'status': 'proposals_recorded', 'grid_positive_pixels': int(sizes.sum()),
               'component_count': count, 'eligible_components': eligible, 'retained_prompts': eligible,
               'omitted_small_components': int(np.count_nonzero(sizes < 16)),
               'omitted_small_grid_pixels': int(sizes[sizes < 16].sum()), 'components': ledger}
    return labels, summary


def _mask_stats(mask):
    rows = np.flatnonzero(mask.any(axis=1)); cols = np.flatnonzero(mask.any(axis=0))
    return {'pixels': int(mask.sum()),
            'bbox_xyxy_exclusive': [int(cols[0]), int(rows[0]), int(cols[-1])+1, int(rows[-1])+1] if len(rows) else None,
            'touches_image_border': bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())}


def _read_mask(capture, folder, receipt, size):
    mask = _image(capture.artifact(folder, receipt), size, mask=True)
    _equal({k: receipt[k] for k in ('pixels', 'bbox_xyxy_exclusive', 'touches_image_border')},
           _mask_stats(mask), 'Saved mask pixel statistics mismatch')
    return mask


def _coarse_mask(labels, label, box, size):
    x0, y0, x1, y1 = box
    rows = np.floor((np.arange(y1-y0)+.5)*256/(y1-y0)).astype(int)
    cols = np.floor((np.arange(x1-x0)+.5)*256/(x1-x0)).astype(int)
    mask = np.zeros((size[1], size[0]), bool)
    mask[y0:y1, x0:x1] = labels[np.ix_(rows, cols)] == label
    return mask


def load_aperture_evidence(baseline_report_path, refinement_report_path, *,
                           baseline_report_sha256, refinement_report_sha256,
                           normalized_image_sha256, source_sha256=None):
    """Return ``{'report': JSON receipt, 'hypotheses': native bool masks}``.

    Every hypothesis has ``mask`` and ``known_domain`` arrays. They are read-only;
    known-domain arrays are shared per variant. Even tiny coarse components with
    zero sampled native pixels survive with their complete grid-support ledger.
    Excess capacity rejects the whole request before native mask allocation.
    """
    _digest(normalized_image_sha256)
    if source_sha256 is not None:
        _digest(source_sha256)
    capture = _Capture()
    bp, rp = Path(baseline_report_path).resolve(), Path(refinement_report_path).resolve()
    baseline = _parse(capture.read(bp, _digest(baseline_report_sha256), report=True))
    refinement = _parse(capture.read(rp, _digest(refinement_report_sha256), report=True))
    for report, method, status in ((baseline, 'photo_only_glasses_detector_medium_baseline_v1', 'prediction_experiment_complete'),
                                   (refinement, 'photo_semantic_to_offline_sam2_refinement_v1', 'refinement_experiment_complete')):
        if (type(report.get('schema_version')) is not int or report['schema_version'] != 1
                or report.get('method') != method or report.get('status') != status
                or report.get('accepted') is not False or report.get('quality_verdict') != 'unmeasured'):
            raise ValueError('Unsupported frozen evidence report schema or claim')
        rows = report['images']
        if (not isinstance(rows, list) or not 1 <= len(rows) <= LIMITS['photos']
                or any(not isinstance(r.get('id'), str) or not _ID.fullmatch(r['id']) for r in rows)
                or len({r['id'] for r in rows}) != len(rows)):
            raise ValueError('Invalid photo inventory')
    _equal({key: baseline['policy'].get(key) for key in BASELINE_POLICY}, BASELINE_POLICY, 'Unsupported baseline policy')
    _equal(refinement['policy'], REFINEMENT_POLICY, 'Unsupported refinement policy')
    if (refinement['baseline_report_sha256'] != baseline_report_sha256
            or Path(refinement['baseline_report']).resolve() != bp):
        raise ValueError('Refinement refers to a different baseline snapshot')
    candidates = [r for r in baseline['images'] if r.get('normalization', {}).get('normalized', {}).get('sha256') == normalized_image_sha256]
    if len(candidates) != 1:
        raise ValueError('Normalized image SHA must identify exactly one baseline photo')
    item = candidates[0]
    refined = next((r for r in refinement['images'] if r['id'] == item['id']), None)
    if (refined is None or item['status'] != 'predictions_recorded' or item.get('accepted') is not False
            or refined['status'] not in ('refinement_hypotheses_recorded', 'no_semantic_proposals')
            or refined.get('accepted') is not False or refined.get('semantic_identity') != 'unverified'):
        raise ValueError('Requested image has no supported complete evidence')
    for key in ('source', 'source_sha256'):
        _equal(refined[key], item[key], 'Source identity differs between reports')
    if source_sha256 is not None and source_sha256 != item['source_sha256']:
        raise ValueError('Requested source SHA differs from evidence source')
    folder, rfolder = bp.parent/item['id'], rp.parent/item['id']
    source = capture.read(item['source'], item['source_sha256'])
    meta = item['normalization']
    original = capture.artifact(folder, meta['original'])
    if original != source:
        raise ValueError('Copied original differs from source')
    # Guard source image dimensions before normalization loads its pixels.
    with Image.open(io.BytesIO(source)) as image:
        if image.width*image.height > LIMITS['native_pixels']:
            raise ValueError('Source image pixel capacity exceeded')
    computed, normalized = _normalize(source, item['id'], meta['view'], Path(item['source']))
    computed['original']['path'] = meta['original']['path']
    computed['normalized']['path'] = meta['normalized']['path']
    _equal(computed, meta, 'Source-to-normalized identity or transform mismatch')
    saved = capture.artifact(folder, meta['normalized'])
    refined_saved = capture.artifact(rfolder, refined['normalized_photo'])
    if saved != normalized or saved != refined_saved or _sha(saved) != normalized_image_sha256:
        raise ValueError('Normalized photo snapshots disagree')
    size = list(_image(saved))
    _equal(size, refined['size_xy'], 'Refinement native dimensions mismatch')
    # The baseline per-image JSON is compared to the pinned aggregate; the
    # refinement per-image report additionally has its own explicit byte pin.
    _equal(_parse(capture.read(folder/'report.json', report=True)), item, 'Baseline case report mismatch')
    case = _parse(capture.artifact(rp.parent, refined['case_report']))
    _equal(case, {k: v for k, v in refined.items() if k != 'case_report'}, 'Refinement case report mismatch')
    _equal([v['name'] for v in item['variants']], VARIANTS, 'Unexpected baseline variants')
    _equal([v['name'] for v in refined['variants']], VARIANTS, 'Unexpected refinement variants')
    prepared, ledger, retained = [], [], {}
    for variant, later in zip(item['variants'], refined['variants'], strict=True):
        _equal(variant['size_xy'], size, 'Baseline native dimensions mismatch')
        for key in ('name', 'crop_xyxy_exclusive', 'grid_to_native_pixel_center'):
            _equal(later[key], variant[key], 'Refinement crop mapping differs from baseline')
        _equal(later['upstream_arrays'], variant['arrays'], 'Upstream array identity mismatch')
        logits = _npz_array(capture.artifact(folder, variant['arrays']), 'lenses_logits')
        labels, summary = _components(logits, variant, size)
        saved_labels = _npz_array(capture.artifact(rfolder, later['labels']), 'labels', labels=True)
        if not np.array_equal(labels, saved_labels):
            raise ValueError('Saved components do not match unflipped lens logits')
        _equal({key: later[key] for key in summary}, summary, 'Component ledger or prompt mapping mismatch')
        _equal(variant['heads']['lenses']['grid_positive_pixels'], summary['grid_positive_pixels'], 'Lens grid count mismatch')
        prepared.append((variant, labels, summary))
        for component in summary['components']:
            ledger.append({'variant': variant['name'], **deepcopy(component)})
            if component['status'] == 'retained_prompt':
                retained[component['id']] = component
    proposals = refined['proposals']
    _equal([p['id'] for p in proposals], list(retained), 'Missing, duplicate or reordered retained proposals')
    _equal(refined['prompt_count'], len(retained), 'Prompt count mismatch')
    _equal(refined['coverage']['decoder_masks'], 3*len(retained), 'Decoder count mismatch')
    expected_status = 'refinement_hypotheses_recorded' if retained else 'no_semantic_proposals'
    _equal(refined['status'], expected_status, 'Proposal status contradicts component inventory')
    count = len(ledger)+3*len(retained)
    # Three shared domain masks plus every returned hypothesis. Temporary PNG
    # decode and coarse validation arrays are bounded separately by native size.
    cells = size[0]*size[1]*(count+3)
    if count > LIMITS['hypotheses'] or cells > LIMITS['returned_boolean_cells']:
        raise ValueError('Native hypothesis capacity exceeded; no subset selected')
    known_full = np.ones((size[1], size[0]), bool); known_full.setflags(write=False)
    hypotheses = []
    proposal_map = {p['id']: p for p in proposals}
    identity = {'source_sha256': item['source_sha256'], 'normalized_image_sha256': normalized_image_sha256,
                'baseline_report_sha256': baseline_report_sha256, 'refinement_report_sha256': refinement_report_sha256}
    def append(kind, variant, component, mask, known, *, decoder=None, evidence=None):
        suffix = 'coarse' if decoder is None else f'sam-decoder-{decoder}'
        token = _sha(_json({**identity, 'component_id': component['id'], 'kind': kind, 'decoder_index': decoder}))
        mask.setflags(write=False)
        provenance = {**identity, 'image_id': item['id'], 'upstream_head': 'lenses_logits', 'flipped': False,
                      'crop_xyxy_exclusive': deepcopy(variant['crop_xyxy_exclusive']),
                      'grid_to_native_pixel_center': deepcopy(variant['grid_to_native_pixel_center']),
                      'component': deepcopy(component), 'artifact_evidence': deepcopy(evidence),
                      'domain_scope': 'prediction availability only; semantic correctness unverified'}
        hypotheses.append({'id': f'aperture-{token}', 'local_id': component['id']+'/'+suffix,
                           'kind': kind, 'variant': variant['name'], 'component_id': component['id'],
                           'decoder_index': decoder, 'mask': mask, 'known_domain': known,
                           'semantic_identity': 'unverified', 'accepted': False, 'provenance': provenance})
    for variant, labels, summary in prepared:
        box = variant['crop_xyxy_exclusive']; x0, y0, x1, y1 = box
        known = np.zeros(known_full.shape, bool); known[y0:y1, x0:x1] = True; known.setflags(write=False)
        for component in summary['components']:
            coarse = _coarse_mask(labels, component['component_label'], box, size)
            append('coarse_component', variant, component, coarse, known,
                   evidence={'upstream_arrays': variant['arrays'], 'labels': refined['variants'][VARIANTS.index(variant['name'])]['labels']})
            if component['id'] not in retained:
                continue
            proposal = proposal_map[component['id']]
            _equal({key: proposal[key] for key in component}, component, 'SAM prompt differs from image component')
            _equal(proposal['variant'], variant['name'], 'SAM variant mismatch')
            _equal(proposal['source_crop_xyxy_exclusive'], box, 'SAM source crop mismatch')
            saved_coarse = _read_mask(capture, rfolder, proposal['coarse_native_component'], size)
            if not np.array_equal(saved_coarse, coarse):
                raise ValueError('Saved coarse mask disagrees with nearest cell mapping')
            alternatives = proposal['alternatives']
            _equal([a['decoder_index'] for a in alternatives], [0, 1, 2], 'All three SAM alternatives must survive separately')
            for alternative in alternatives:
                mask = _read_mask(capture, rfolder, alternative['mask'], size)
                quality = alternative['predicted_quality']
                if isinstance(quality, bool) or not isinstance(quality, (int, float)) or not np.isfinite(quality):
                    raise ValueError('Invalid uncalibrated SAM score')
                append('sam_alternative', variant, component, mask, known_full,
                       decoder=alternative['decoder_index'], evidence=alternative)
    report = {'schema_version': 1, 'method': METHOD, 'status': 'image_hypotheses_loaded',
              'accepted': False, 'semantic_identity': 'unverified', 'quality_verdict': 'unmeasured',
              **identity, 'image_id': item['id'], 'size_xy': size, 'normalization': deepcopy(meta),
              'component_ledger': ledger, 'hypothesis_count': count, 'coarse_component_count': len(ledger),
              'sam_alternative_count': 3*len(retained), 'limits': deepcopy(LIMITS),
              'resources': {'captured_bytes': capture.total, 'returned_boolean_cells_upper_bound': cells},
              'input_snapshots': list(capture.records.values()),
              'upstream_provenance': {'source_model': deepcopy(baseline.get('source_model')),
                  'engine': deepcopy(refinement.get('engine')),
                  'baseline_implementation_sha256': deepcopy(baseline.get('implementation_sha256')),
                  'refinement_implementation_sha256': deepcopy(refinement.get('implementation_sha256')),
                  'scope': 'pinned report provenance retained; upstream model binaries are not loaded or revalidated'},
              'limitations': ['All hypotheses may be incorrect, duplicated, fragmented or unrelated to lenses.',
                  'Known domains describe model prediction coverage, not certified semantic labels.',
                  'Tiny grid components remain hypotheses even when no native pixel samples their grid cells.',
                  'No frame predictions, flipped controls, display composites, mesh prompts or decoder selection enter inference.',
                  'Report and artifact integrity does not verify the upstream neural execution or lens identity.']}
    return {'report': report, 'hypotheses': hypotheses}
