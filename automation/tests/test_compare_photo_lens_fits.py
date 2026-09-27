"""Saved-artifact comparison controls; no product-specific thresholds."""
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from qa.compare_photo_lens_fits import compare_photo_lens_fits
from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy, fit_joint_photo_lens_candidates
from reconstruction.photo_lens_fit import PhotoLensFitPolicy, fit_photo_lens_candidates
from reconstruction.photo_lens_stage import preview_representatives, save_group_observations
from reconstruction.joint_photo_lens_stage import joint_preview_representatives


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, allow_nan=False), encoding='utf-8')


def seal_joint(root, report):
    report['artifacts'] = {p.relative_to(root).as_posix(): sha(p) for p in root.rglob('*')
                           if p.is_file() and p.name not in ('report.json', 'receipt.json')}
    write(root/'report.json', report)
    write(root/'receipt.json', {'report_sha256': sha(root/'report.json')})


def fixtures(folder):
    folder = Path(folder); old = folder/'old'; new = folder/'joint'; old.mkdir(); new.mkdir()
    source = folder/'source.glb'; source.write_bytes(b'controlled model fixture')
    photo = folder/'front.png'; photo.write_bytes(b'controlled photo identity fixture')
    pins = {str(source): sha(source), str(photo): sha(photo)}
    p = PhotoLensFitPolicy(families=('uniform_tint', 'colored_mirror'), lighting_families=('constant',), max_nfev=8, minimum_validation_points_per_photo=1)
    groups = []
    for gid in range(2):
        y, x = np.mgrid[:8, :8]
        obs = {'id': 'front/region/h0', 'photo_id': 'front', 'region_id': 'region', 'hypothesis_id': 'h0',
               'source_sha256': sha(photo), 'provenance': {'method': 'synthetic_fixture'},
               'xy': np.column_stack((x.ravel()+8*gid, y.ravel())), 'code_rgb': np.full((64, 3), 130+40*gid, np.uint8),
               'intrinsic_v': y.ravel()/7, 'incidence_degrees': np.zeros(64), 'background_rgb': np.ones((64, 3)), 'image_size': [16, 8]}
        groups.append({'surface_binding': {'schema_version': 1, 'prepared_glb_sha256': sha(source), 'material_group_id': f'g{gid}',
                       'coordinate_method': 'fixture_uv', 'uv_semantics': 'lens_local_bottom_0_top_1'}, 'observations': [obs]})
    obs_report = {'method': 'fixture_camera_receipt', 'photos': [{'id': 'front', 'camera': {'yaw': 0}}]}
    write(old/'observation-report.json', obs_report)
    old_report = {'method': 'photo_lens_candidate_stage_v1', 'status': 'diagnostic_previews_available', 'input_sha256': pins,
                  'groups': [], 'previews': [], 'policy': asdict(p)}
    for index, group in enumerate(groups):
        part = old/f'part-{index:03d}'; save_group_observations(part, group)
        fit = fit_photo_lens_candidates(group['observations'], surface_binding=group['surface_binding'], policy=p)
        write(part/'fit.json', fit)
        old_report['groups'].append({'source_part_index': index, 'report': {'path': f'{part.name}/fit.json', 'sha256': sha(part/'fit.json')},
            'preview_representative_ids': {family: c['candidate_id'] for family, c in preview_representatives(fit).items()}})
    write(old/'report.json', old_report)
    inp = new/'inputs'; inp.mkdir(); group_paths = {}
    for index, group in enumerate(groups):
        part = inp/f'part-{index:03d}'; save_group_observations(part, group)
        group_paths[str(index)] = (part/'observations.json').relative_to(new).as_posix()
    write(inp/'observation-report.json', obs_report)
    fitted = fit_joint_photo_lens_candidates(groups, policy=JointPhotoLensFitPolicy(photo_policy=p))
    fit_path = new/'joint-fit.json'; write(fit_path, fitted)
    new_report = {'method': 'joint_photo_lens_candidate_stage_v1', 'status': 'diagnostic_previews_available', 'input_sha256': pins,
                  'inputs': {'groups': group_paths, 'observation_report': 'inputs/observation-report.json'},
                  'fit': {'path': fit_path.name, 'sha256': sha(fit_path)}, 'previews': [
                      {'family_assignment': dict(assignment), 'candidate_id': c['candidate_id']}
                      for assignment, c in joint_preview_representatives(fitted).items()]}
    seal_joint(new, new_report)
    return old, new, old_report, new_report


class PhotoLensFitComparisonTests(unittest.TestCase):
    def test_matches_every_configuration_collapses_roughness_and_preserves_source_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new, _, _ = fixtures(folder)
            before = {str(p): (sha(p), p.stat().st_mtime_ns) for root in (old, new) for p in root.rglob('*') if p.is_file()}
            result = compare_photo_lens_fits(old, new, Path(folder)/'comparison')
            self.assertTrue(result['comparability']['exact_saved_arrays_provenance_bindings_and_splits'])
            self.assertFalse(result['comparability']['saved_objectives_comparable'])
            self.assertEqual(result['counts']['matched_configurations'], 2)
            self.assertEqual(result['counts']['unmatched_joint_configurations'], 0)
            for row in result['counts']['independent'].values():
                self.assertEqual(row['roughness_expanded_records'], 12)
                self.assertEqual(row['roughness_collapsed']['optimizer_candidate_records'], 6)
            self.assertEqual(result['counts']['joint']['optimizer_candidate_records'], 6)
            for c in result['matched_configurations']:
                self.assertEqual(c['independent_start_pairing_cardinality'], 9)
                self.assertEqual(c['lighting_disagreement'][0]['joint_disagreement'], 0)
                for group in c['groups'].values():
                    self.assertEqual(group['independent']['counts']['optimizer_candidate_records'], 3)
                    self.assertEqual(group['joint']['counts']['optimizer_candidate_records'], 3)
                    self.assertEqual(group['joint']['region_metrics'][0]['validation']['all_starts']['mean_absolute_interval_error_codes']['values'], 3)
            self.assertEqual(len(result['existing_display_representatives']), 2)
            self.assertNotIn('accepted', result)
            after = {str(p): (sha(p), p.stat().st_mtime_ns) for root in (old, new) for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            self.assertTrue((Path(folder)/'comparison'/'README.md').is_file())

    def test_coherently_resealed_array_or_split_substitution_is_not_a_fixed_input_comparison(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new, _, new_report = fixtures(folder)
            manifest_path = new/'inputs/part-000/observations.json'
            manifest = json.loads(manifest_path.read_text())
            array_path = manifest_path.parent/manifest['observations'][0]['arrays']['path']
            with np.load(array_path) as archive:
                arrays = {k: archive[k].copy() for k in archive.files}
            arrays['intrinsic_v'][0] = .2
            np.savez_compressed(array_path, **arrays)
            manifest['observations'][0]['arrays']['sha256'] = sha(array_path)
            write(manifest_path, manifest); seal_joint(new, new_report)
            with self.assertRaisesRegex(ValueError, 'arrays/provenance/source/support differs'):
                compare_photo_lens_fits(old, new)
        with tempfile.TemporaryDirectory() as folder:
            old, new, _, new_report = fixtures(folder)
            fit_path = new/'joint-fit.json'; fitted = json.loads(fit_path.read_text())
            fitted['spatial_split'][0]['span_xy'] = [32, 8]
            write(fit_path, fitted); new_report['fit']['sha256'] = sha(fit_path); seal_joint(new, new_report)
            with self.assertRaisesRegex(ValueError, 'split differs'):
                compare_photo_lens_fits(old, new)

    def test_nonmatching_configuration_is_exposed_not_replaced_with_best_mask_or_light(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new, old_report, _ = fixtures(folder)
            # Remove every mirror explanation from one independent fit while
            # preserving its immutable pointer. Comparison must mark unmatched.
            fit_path = old/'part-000/fit.json'; fit = json.loads(fit_path.read_text())
            fit['candidates'] = [c for c in fit['candidates'] if c['assumptions']['family'] == 'uniform_tint']
            fit['ar_prediction_envelope'] = None
            write(fit_path, fit); old_report['groups'][0]['report']['sha256'] = sha(fit_path)
            old_report['groups'][0]['preview_representative_ids'].pop('colored_mirror')
            write(old/'report.json', old_report)
            result = compare_photo_lens_fits(old, new)
            self.assertEqual(result['counts']['matched_configurations'], 1)
            self.assertEqual(result['counts']['unmatched_joint_configurations'], 1)
            self.assertEqual(result['unmatched_joint_configurations'][0]['groups'], ['g0'])

    def test_missing_originals_explicit_but_changed_existing_source_and_output_inside_stage_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            old, new, _, _ = fixtures(folder)
            photo = Path(folder)/'front.png'; photo.unlink()
            report = compare_photo_lens_fits(old, new)
            self.assertEqual(len(report['unavailable_original_sources']), 2)
            self.assertTrue(report['comparability']['exact_saved_arrays_provenance_bindings_and_splits'])
            with self.assertRaisesRegex(ValueError, 'outside'):
                compare_photo_lens_fits(old, new, old/'comparison')
            photo.write_bytes(b'changed source')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                compare_photo_lens_fits(old, new)


if __name__ == '__main__':
    unittest.main()
