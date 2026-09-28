"""Independent frozen-evidence fixtures: no neural runtime or private corpus."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from reconstruction.aperture_evidence import load_aperture_evidence, REFINEMENT_POLICY
from reconstruction.input_bundle import _normalize


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def receipt(path, folder):
    return {'path': path.relative_to(folder).as_posix(), 'sha256': sha(path.read_bytes())}


def save_mask(path, array, folder):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array.astype('uint8')*255).save(path)
    yy, xx = np.nonzero(array)
    return {**receipt(path, folder), 'pixels': int(array.sum()),
            'bbox_xyxy_exclusive': [int(xx.min()), int(yy.min()), int(xx.max())+1, int(yy.max())+1] if len(xx) else None,
            'touches_image_border': bool(array[0].any() or array[-1].any() or array[:, 0].any() or array[:, -1].any())}


class EvidenceFixture:
    def __init__(self, root, *, empty=False):
        self.root = root
        self.bp, self.rp = root/'baseline/report.json', root/'refinement/report.json'
        self.bf, self.rf = self.bp.parent/'photo', self.rp.parent/'photo'
        self.bf.mkdir(parents=True); self.rf.mkdir(parents=True)
        self.source = root/'source.png'
        image = Image.new('RGB', (512, 256), (240, 245, 250))
        image.putpixel((100, 100), (0, 0, 0)); image.save(self.source)
        raw = self.source.read_bytes()
        meta, normalized = _normalize(raw, 'photo', 'front', self.source)
        meta['original']['path'] = 'original.source'; meta['normalized']['path'] = 'normalized.png'
        (self.bf/'original.source').write_bytes(raw)
        (self.bf/'normalized.png').write_bytes(normalized)
        (self.rf/'normalized.png').write_bytes(normalized)
        self.item = {'id': 'photo', 'source': str(self.source), 'source_sha256': sha(raw),
                     'status': 'predictions_recorded', 'accepted': False, 'quality_verdict': 'unmeasured',
                     'normalization': meta, 'variants': []}
        self.refined = {'id': 'photo', 'source': str(self.source), 'source_sha256': sha(raw),
                        'size_xy': [512, 256], 'accepted': False, 'semantic_identity': 'unverified',
                        'status': 'no_semantic_proposals' if empty else 'refinement_hypotheses_recorded',
                        'normalized_photo': receipt(self.rf/'normalized.png', self.rf),
                        'variants': [], 'proposals': [], 'prompt_count': 0 if empty else 2,
                        'coverage': {'decoder_masks': 0 if empty else 6}}
        for name, box in (('full', [0, 0, 512, 256]), ('contrast_crop', [128, 64, 384, 192])):
            x0, y0, x1, y1 = box; sx, sy = (x1-x0)/256, (y1-y0)/256
            affine = [[sx, 0, x0+sx/2-.5], [0, sy, y0+sy/2-.5], [0, 0, 1]]
            labels = np.zeros((256, 256), 'int32')
            if not empty:
                labels[20:24, 40:44] = 1; labels[200, 230] = 2
            logits = np.where(labels > 0, 1., -1.).astype('float32')
            # All other heads disagree. Only the original lens grid may enter.
            others = np.ones_like(logits)
            path = self.bf/(name+'-logits.npz')
            np.savez_compressed(path, lenses_logits=logits, lenses_flip_aligned_logits=others,
                                frames_logits=others, frames_flip_aligned_logits=others)
            variant = {'name': name, 'crop_xyxy_exclusive': box, 'size_xy': [512, 256],
                       'grid_to_native_pixel_center': affine, 'arrays': receipt(path, self.bf),
                       'heads': {'lenses': {'grid_positive_pixels': 0 if empty else 17}}}
            self.item['variants'].append(variant)
            path = self.rf/(name+'-components.npz'); np.savez_compressed(path, labels=labels)
            components = []
            if not empty:
                for label, gridbox, point, pixels, distance in (
                        (1, [40, 20, 44, 24], [41, 21], 16, 2.),
                        (2, [230, 200, 231, 201], [230, 200], 1, 1.)):
                    component = {'id': f'{name}-component-{label:03d}', 'component_label': label,
                                 'grid_pixels': pixels, 'grid_bbox_xyxy_exclusive': gridbox,
                                 'grid_positive_point_xy': point, 'interior_distance_grid_pixels': distance,
                                 'point_was_clamped': False,
                                 'status': 'retained_prompt' if label == 1 else 'omitted_below_minimum_grid_support',
                                 'semantic_identity': 'unverified_photo_semantic_proposal'}
                    if label == 1:
                        low = np.array([x0+40*sx, y0+20*sy]); high = np.array([x0+44*sx, y0+24*sy])
                        pad = .1*(high-low)
                        component['prompt'] = {'bbox_xyxy': [*(low-pad).tolist(), *(high+pad).tolist()],
                                               'points_xy': [[x0+(41+.5)*sx-.5, y0+(21+.5)*sy-.5]],
                                               'point_labels': [1]}
                    components.append(component)
            self.refined['variants'].append({'name': name, 'crop_xyxy_exclusive': box,
                'grid_to_native_pixel_center': affine, 'upstream_arrays': variant['arrays'],
                'labels': receipt(path, self.rf), 'status': 'proposals_recorded',
                'grid_positive_pixels': 0 if empty else 17, 'component_count': 0 if empty else 2,
                'eligible_components': 0 if empty else 1, 'retained_prompts': 0 if empty else 1,
                'omitted_small_components': 0 if empty else 1, 'omitted_small_grid_pixels': 0 if empty else 1,
                'components': components})
            if not empty:
                coarse = np.zeros((256, 512), bool)
                if name == 'full':
                    coarse[20:24, 80:88] = True
                else:
                    coarse[74:76, 168:172] = True
                pdir = self.rf/components[0]['id']
                proposal = {**deepcopy(components[0]), 'variant': name, 'source_crop_xyxy_exclusive': box,
                            'coarse_native_component': save_mask(pdir/'coarse.png', coarse, self.rf), 'alternatives': []}
                for decoder in range(3):
                    mask = coarse.copy()
                    mask[decoder, decoder] = True  # Out-of-crop SAM positives survive.
                    proposal['alternatives'].append({'decoder_index': decoder, 'predicted_quality': [.9, .1, .8][decoder],
                        'quality_scope': 'uncalibrated SAM mask quality, not semantic lens confidence',
                        'mask': save_mask(pdir/f'{decoder}.png', mask, self.rf)})
                self.refined['proposals'].append(proposal)
        self.baseline = {'schema_version': 1, 'method': 'photo_only_glasses_detector_medium_baseline_v1',
                         'status': 'prediction_experiment_complete', 'accepted': False, 'quality_verdict': 'unmeasured',
                         'policy': {'input_size': [256, 256], 'variants': ['full', 'contrast_crop'],
                                    'threshold': 'logit > 0; uncalibrated class prediction',
                                    'native_logit_interpolation': 'bilinear within crop only; outside crop is unknown'},
                         'images': [self.item]}
        self.refinement = {'schema_version': 1, 'method': 'photo_semantic_to_offline_sam2_refinement_v1',
                           'status': 'refinement_experiment_complete', 'accepted': False, 'quality_verdict': 'unmeasured',
                           'policy': deepcopy(REFINEMENT_POLICY), 'baseline_report': str(self.bp),
                           'images': [self.refined]}
        self.save()

    def save(self):
        write_json(self.bf/'report.json', self.item)
        write_json(self.bp, self.baseline)
        self.refinement['baseline_report_sha256'] = sha(self.bp.read_bytes())
        case = {k: v for k, v in self.refined.items() if k != 'case_report'}
        write_json(self.rf/'report.json', case)
        self.refined['case_report'] = receipt(self.rf/'report.json', self.rp.parent)
        write_json(self.rp, self.refinement)

    def load(self, **overrides):
        args = {'baseline_report_sha256': sha(self.bp.read_bytes()),
                'refinement_report_sha256': sha(self.rp.read_bytes()),
                'normalized_image_sha256': self.item['normalization']['normalized']['sha256']}
        args.update(overrides)
        return load_aperture_evidence(self.bp, self.rp, **args)


class ApertureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.fixture = EvidenceFixture(Path(self.temp.name))

    def test_native_components_crop_exterior_and_every_decoder_remain_separate(self):
        result = self.fixture.load()
        rows = result['hypotheses']; by_id = {h['local_id']: h for h in rows}
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({h['id'] for h in rows}), 10)
        expected = np.zeros((256, 512), bool); expected[74:76, 168:172] = True
        crop = by_id['contrast_crop-component-001/coarse']
        np.testing.assert_array_equal(crop['mask'], expected)
        domain = np.zeros_like(expected); domain[64:192, 128:384] = True
        np.testing.assert_array_equal(crop['known_domain'], domain)
        self.assertTrue(by_id['full-component-001/coarse']['known_domain'].all())
        for decoder in range(3):
            alternative = by_id[f'contrast_crop-component-001/sam-decoder-{decoder}']
            self.assertTrue(alternative['known_domain'].all())
            self.assertTrue(alternative['mask'][decoder, decoder])
            self.assertEqual(alternative['decoder_index'], decoder)
            self.assertFalse(alternative['accepted'])
            self.assertFalse(alternative['mask'].flags.writeable)
        self.assertEqual(result['report']['coarse_component_count'], 4)
        self.assertEqual(result['report']['sam_alternative_count'], 6)

    def test_tiny_component_survives_even_if_native_sampling_has_zero_positive_pixels(self):
        result = self.fixture.load()
        tiny = next(h for h in result['hypotheses'] if h['local_id'] == 'contrast_crop-component-002/coarse')
        self.assertEqual(tiny['provenance']['component']['grid_pixels'], 1)
        self.assertEqual(tiny['provenance']['component']['status'], 'omitted_below_minimum_grid_support')
        self.assertEqual(int(tiny['mask'].sum()), 0)
        self.assertEqual(len(result['report']['component_ledger']), 4)

    def test_empty_image_is_supported_without_inventing_a_negative_semantic_label(self):
        with tempfile.TemporaryDirectory() as root:
            result = EvidenceFixture(Path(root), empty=True).load()
        self.assertEqual(result['hypotheses'], [])
        self.assertEqual(result['report']['component_ledger'], [])
        self.assertEqual(result['report']['semantic_identity'], 'unverified')

    def test_report_and_request_identity_mismatch_are_rejected(self):
        for overrides in ({'baseline_report_sha256': '0'*64}, {'refinement_report_sha256': '0'*64},
                          {'normalized_image_sha256': '0'*64}, {'source_sha256': '0'*64}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.fixture.load(**overrides)

    def test_each_input_path_is_captured_once_and_snapshots_are_complete(self):
        real_open = Path.open; counts = {}
        def counted(path, *args, **kwargs):
            if args and args[0] == 'rb':
                counts[str(path.resolve())] = counts.get(str(path.resolve()), 0)+1
            return real_open(path, *args, **kwargs)
        # Prepare caller pins before instrumenting adapter-only reads.
        f = self.fixture
        pins = {'baseline_report_sha256': sha(f.bp.read_bytes()), 'refinement_report_sha256': sha(f.rp.read_bytes()),
                'normalized_image_sha256': f.item['normalization']['normalized']['sha256']}
        with patch.object(Path, 'open', counted):
            result = load_aperture_evidence(f.bp, f.rp, **pins)
        self.assertTrue(counts)
        self.assertTrue(all(n == 1 for n in counts.values()))
        self.assertEqual(set(counts), {r['path'] for r in result['report']['input_snapshots']})
        self.assertTrue(all(len(r['sha256']) == 64 for r in result['report']['input_snapshots']))

    def test_changed_source_and_normalized_files_are_rejected(self):
        f = self.fixture
        for path in (f.source, f.bf/'original.source', f.bf/'normalized.png', f.rf/'normalized.png'):
            old = path.read_bytes(); path.write_bytes(old+b'changed')
            with self.subTest(path=path), self.assertRaises(ValueError):
                f.load()
            path.write_bytes(old)

    def test_rehashed_wrong_normalization_transform_is_rejected(self):
        self.fixture.item['normalization']['transform']['original_to_normalized_xy'][0][2] = 1
        self.fixture.save()
        with self.assertRaises(ValueError):
            self.fixture.load()

    def test_changed_mask_and_label_npz_are_rejected(self):
        f = self.fixture
        for ref in (f.refined['proposals'][0]['alternatives'][0]['mask'], f.refined['variants'][0]['labels']):
            path = f.rf/ref['path']; old = path.read_bytes(); path.write_bytes(old+b'changed')
            with self.subTest(path=path), self.assertRaises(ValueError):
                f.load()
            path.write_bytes(old)

    def test_rehashed_component_npz_must_equal_unflipped_lens_logits(self):
        f = self.fixture; ref = f.refined['variants'][0]['labels']; path = f.rf/ref['path']
        np.savez_compressed(path, labels=np.zeros((256, 256), 'int32'))
        ref['sha256'] = sha(path.read_bytes()); f.save()
        with self.assertRaisesRegex(ValueError, 'components do not match'):
            f.load()

    def test_rehashed_mask_shape_nonbinary_pixels_and_counts_are_rejected(self):
        f = self.fixture; ref = f.refined['proposals'][0]['alternatives'][0]['mask']; path = f.rf/ref['path']
        original = path.read_bytes(); saved = deepcopy(ref)
        for mutation in ('shape', 'nonbinary', 'count'):
            path.write_bytes(original); ref.clear(); ref.update(saved)
            if mutation == 'shape':
                Image.new('L', (1, 1)).save(path)
            elif mutation == 'nonbinary':
                Image.new('L', (512, 256), 127).save(path)
            else:
                ref['pixels'] += 1
            ref['sha256'] = sha(path.read_bytes()); f.save()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                f.load()

    def test_no_missing_repeated_or_selected_decoder_is_accepted(self):
        f = self.fixture; saved = deepcopy(f.refined['proposals'][0]['alternatives'])
        for alternatives in (saved[:1], [saved[0], saved[0], saved[2]], list(reversed(saved))):
            f.refined['proposals'][0]['alternatives'] = alternatives; f.save()
            with self.subTest(decoders=[a['decoder_index'] for a in alternatives]), self.assertRaises(ValueError):
                f.load()

    def test_missing_tiny_ledger_component_and_shifted_crop_affine_are_rejected(self):
        f = self.fixture; saved = deepcopy(f.refined['variants'][0])
        for mutation in ('tiny', 'affine'):
            f.refined['variants'][0] = deepcopy(saved)
            if mutation == 'tiny':
                f.refined['variants'][0]['components'].pop()
            else:
                f.refined['variants'][0]['grid_to_native_pixel_center'][0][2] += .5
            f.save()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                f.load()

    def test_resource_capacity_rejects_before_native_mask_allocation(self):
        with patch.dict('reconstruction.aperture_evidence.LIMITS', {'hypotheses': 9}), \
                patch('reconstruction.aperture_evidence._coarse_mask', side_effect=AssertionError('Allocated mask')) as allocate:
            with self.assertRaisesRegex(ValueError, 'hypothesis capacity'):
                self.fixture.load()
            allocate.assert_not_called()

    def test_supported_schema_and_policy_are_exact_not_boolean_coercions(self):
        f = self.fixture
        for key, value in (('schema_version', True), ('accepted', 0)):
            saved = f.refinement[key]; f.refinement[key] = value; f.save()
            with self.subTest(key=key), self.assertRaises(ValueError):
                f.load()
            f.refinement[key] = saved
        f.refinement['policy']['minimum_component_grid_pixels'] = 8; f.save()
        with self.assertRaises(ValueError):
            f.load()

    def test_stable_ids_repeat_without_mutating_evidence(self):
        before = {str(p): sha(p.read_bytes()) for p in Path(self.temp.name).rglob('*') if p.is_file()}
        first, second = self.fixture.load(), self.fixture.load()
        self.assertEqual([h['id'] for h in first['hypotheses']], [h['id'] for h in second['hypotheses']])
        after = {str(p): sha(p.read_bytes()) for p in Path(self.temp.name).rglob('*') if p.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
