"""Resumable reconstruction job; current stage completion is not quality acceptance.

python -m reconstruction.job --request request.json --output data/jobs/example
No provider or credentials are discovered implicitly. The CLI can refine a
supplied GLB or prepare a photo-only initializer request for an injected backend.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

from .atomic_files import replace_with_retry
from .input_bundle import prepare_input_bundle
from .initializer import resolve_initial_model
from .refine_photos import PhotoInput, implementation_manifest, run as refine
from .region_proposals import run_region_stage


FRONT_SHEET_PROFILE = 'front_sheet_v1'
GROUP_PROFILE = 'effective_optical_group_v1_experiment'
INFERRED_GROUPING = 'physical_group_inference'
GROUPING_MODES = ('explicit_declarations', 'source_part_hypotheses', INFERRED_GROUPING)
_DECLARATION_SNAPSHOT = 'optical-group-declarations.json'
APPEARANCE_CANDIDATE = 'candidate-appearance.glb'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return _decode(Path(path).read_bytes())


def _decode(raw):
    def reject_constant(value):
        raise ValueError(f'Nonfinite JSON value: {value}')
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8-sig'), parse_constant=reject_constant,
                      object_pairs_hook=unique_pairs)


def _write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.next')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    replace_with_retry(temporary, path)


def _now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _job_lock(output):
    """OS-owned lock releases on process death; a leftover file is not a live job."""
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.lock').open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            lock = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        try:
            lock()
        except OSError as error:
            raise RuntimeError('This reconstruction job is already running') from error
        try:
            yield
        finally:
            stream.seek(0)
            unlock()


def _inside(output, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('Artifact path must be job-relative')
    path = (output / relative).resolve()
    if not path.is_relative_to(output.resolve()) or path == output.resolve():
        raise ValueError('Artifact path escapes the job')
    return path


def _inventory(output, folder):
    return {path.relative_to(output).as_posix(): _sha(path)
            for path in sorted(folder.rglob('*')) if path.is_file()}


def _verify(output, pins):
    if not isinstance(pins, dict) or not pins:
        raise ValueError('Missing artifact integrity evidence')
    for relative, expected in pins.items():
        path = _inside(output, relative)
        if not path.is_file() or _sha(path) != expected:
            raise ValueError(f'Artifact integrity mismatch: {relative}')


def _verify_stage(output, stage):
    _verify(output, stage['artifacts'])
    actual = _inventory(output, _inside(output, stage['directory']))
    if actual != stage['artifacts']:
        raise ValueError('Stage contents differ from its immutable artifact inventory')


def _settings_sha256(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _verify_lens_fit_settings(stage, settings):
    if (settings is None or stage.get('fit_mode') != settings['mode'] or
            stage.get('fit_settings') != settings or
            stage.get('fit_settings_sha256') != _settings_sha256(settings)):
        raise ValueError('Completed lens fit mode/settings differ from the pinned job settings')


def _validate_optical_options(profile, grouping, declarations, lens_candidates, lens_fit_mode):
    """Reject unsupported combinations before a job directory or client is opened."""
    if profile not in (FRONT_SHEET_PROFILE, GROUP_PROFILE):
        raise ValueError('Unknown optical_profile')
    if profile == FRONT_SHEET_PROFILE:
        if grouping is not None or declarations is not None:
            raise ValueError('Optical grouping/declarations require the effective group profile')
        return
    if lens_candidates is not True or lens_fit_mode != 'joint':
        raise ValueError('Effective optical groups require lens_candidates and joint fitting')
    if grouping not in GROUPING_MODES:
        raise ValueError('Effective optical groups require an explicit optical_grouping mode')
    if grouping == 'explicit_declarations':
        if not isinstance(declarations, (str, Path)) or not str(declarations):
            raise ValueError('Explicit grouping requires optical_group_declarations')
    elif declarations is not None:
        raise ValueError('Inferred or source-part grouping cannot also supply declarations')


def _capture_group_settings(output, profile, grouping, declarations):
    """Hash and parse the same captured bytes; missing originals use pinned copies.

    Only opt-in grouped jobs gain these settings. The legacy front-sheet settings
    dictionary remains byte-compatible. The preparer, not this orchestration
    layer, checks declarations against the actually retained source candidate.
    """
    if profile == FRONT_SHEET_PROFILE:
        return None, None
    settings = {'profile': profile, 'grouping_mode': grouping, 'declarations': None}
    if declarations is None:
        return settings, None
    from .prepare_optical_groups import _ordinary_path, validate_group_declarations
    source = _ordinary_path(declarations)
    if source.exists():
        raw = source.read_bytes()
    else:
        journal_path = _ordinary_path(output / 'job.json')
        if not journal_path.is_file():
            raise ValueError('Optical group declarations do not exist')
        previous = _read(journal_path).get('settings', {}).get('optical_groups', {}).get('declarations')
        if (not isinstance(previous, dict) or previous.get('source_path') != str(source)
                or previous.get('snapshot') != _DECLARATION_SNAPSHOT):
            raise ValueError('Missing declaration original has no matching pinned snapshot')
        raw = _ordinary_path(output / _DECLARATION_SNAPSHOT).read_bytes()
        if hashlib.sha256(raw).hexdigest() != previous.get('sha256'):
            raise ValueError('Optical declaration snapshot integrity mismatch')
    # Match the preparer's UTF-8 JSON contract before the job creates artifacts.
    # The generic request reader separately accepts UTF-8 BOMs for old jobs.
    if raw.startswith(b'\xef\xbb\xbf'):
        raise ValueError('Optical declarations must use UTF-8 without a byte-order mark')
    declaration = validate_group_declarations(_decode(raw))
    settings['declarations'] = {'source_path': str(source), 'sha256': hashlib.sha256(raw).hexdigest(),
        'source_sha256': declaration['source_sha256'], 'snapshot': _DECLARATION_SNAPSHOT}
    return settings, raw


def _verify_group_inputs(output, settings):
    if not settings or not settings['declarations']:
        return
    declaration = settings['declarations']
    if declaration['snapshot'] != _DECLARATION_SNAPSHOT:
        raise ValueError('Unexpected optical declaration snapshot path')
    from .prepare_optical_groups import _ordinary_path
    _ordinary_path(output / _DECLARATION_SNAPSHOT)
    _verify(output, {_DECLARATION_SNAPSHOT: declaration['sha256']})
    source = _ordinary_path(declaration['source_path'])
    if source.exists() and (not source.is_file() or _sha(source) != declaration['sha256']):
        raise ValueError('Original optical declarations changed; use a new job directory')


def _bind_group_stage(output, stage, settings, chosen):
    source = {'path': chosen.relative_to(output).as_posix(), 'sha256': _sha(chosen)}
    inputs = {source['path']: source['sha256']}
    if settings['declarations']:
        inputs[_DECLARATION_SNAPSHOT] = settings['declarations']['sha256']
    stage.update(optical_settings=settings, optical_settings_sha256=_settings_sha256(settings),
                 optical_source=source, optical_input_artifacts=inputs)


def _verify_group_stage(output, stage, settings, chosen=None):
    if settings is None:
        return
    if (stage.get('optical_settings') != settings or
            stage.get('optical_settings_sha256') != _settings_sha256(settings)):
        raise ValueError('Completed optical stage differs from pinned group settings')
    _verify(output, stage.get('optical_input_artifacts'))
    source = stage.get('optical_source')
    if (not isinstance(source, dict) or set(source) != {'path', 'sha256'} or
            stage['optical_input_artifacts'].get(source['path']) != source['sha256']):
        raise ValueError('Optical stage lacks its retained geometry input')
    if chosen is not None and source != {'path': chosen.relative_to(output).as_posix(), 'sha256': _sha(chosen)}:
        raise ValueError('Optical stage belongs to a different retained geometry input')
    if settings['declarations'] and stage['optical_input_artifacts'].get(_DECLARATION_SNAPSHOT) != settings['declarations']['sha256']:
        raise ValueError('Optical stage lacks the pinned declaration input')


def _save(output, journal):
    journal['updated_at'] = _now()
    _write(output / 'job.json', journal)


MIRROR_FAMILIES = ('colored_mirror', 'angular_mirror', 'gradient_angular_mirror')


def lens_policy_for_facts(policy, lens_facts):
    """Restrict the fitted families to what the declared lens facts allow; the policy is otherwise unchanged.

    A stated ``mirror_coating`` keeps only the mirror families (True) or only the tints
    (False). The photographs alone cannot decide this: a plain lens reflecting a bright
    studio fits a mirror with a dim environment just as well, and the AR runtime then
    renders the coating. The fit report records the families it ran with.
    """
    from dataclasses import replace
    facts = lens_facts or {}
    if 'mirror_coating' not in facts:
        return policy, None
    photo = getattr(policy, 'photo_policy', policy)
    keep = tuple(f for f in photo.families if (f in MIRROR_FAMILIES) == bool(facts['mirror_coating']))
    if not keep:
        raise ValueError('The declared lens facts leave no appearance family in the fit policy')
    note = {'declared': dict(facts), 'families_before': list(photo.families), 'families': list(keep)}
    if hasattr(policy, 'photo_policy'):
        return replace(policy, photo_policy=replace(photo, families=keep)), note
    return replace(policy, families=keep), note


def _run_inferred_grouping(output, journal, settings, group_settings, chosen, refined, photos, ledger, *,
                           region_engine, aperture_engine, lens_policy, lens_maximum_samples, lens_fit_mode,
                           appearance_tolerance_codes, lens_facts=None, appearance_mode='photo',
                           semantic_report=None, semantic_client=None, appearance_renderer=None, semantic_photos=None,
                           evaluation_context=None, refinement_reference=None):
    """Physical grouping, per-hypothesis bridge, regions on the bridged candidate, joint fit, selection.

    Every stage is resumable through the journal. The selected hypothesis and
    the selected family are explicit rules recorded in the reports. Semantic
    mode additionally measures delivery gates; missing evidence blocks acceptance.
    """
    from .physical_group_stage import run_physical_group_stage
    summary = {'optical_profile': GROUP_PROFILE, 'grouping_mode': INFERRED_GROUPING,
               'optical_settings_sha256': _settings_sha256(group_settings), 'preparation_status': 'not_prepared',
               'fit_status': 'not_prepared', 'fit_mode': lens_fit_mode,
               'fit_settings_sha256': _settings_sha256(settings['lens_candidates']), 'previews': [],
               'preview_export_status': 'not_attempted', 'appearance_selection': None, 'appearance_candidate': None,
               'selected_material': None, 'quality_verdict': 'unmeasured', 'accepted': False}
    region_summary = {'status': 'not_run', 'semantic_identity': 'unverified', 'quality_verdict': 'unmeasured',
                      'material_identification': 'unmeasured'}
    usable = [item for item, record in zip(photos, ledger) if record['status'] == 'refinement_input']
    completed = _completed(output, journal, 'physical_groups')
    if completed:
        groups_stage, groups_folder = completed
        _verify_group_stage(output, groups_stage, group_settings, chosen)
        groups_report = _read(groups_folder / 'report.json')
    else:
        stage, groups_folder = _attempt(output, journal, 'physical_groups')
        _bind_group_stage(output, stage, group_settings, chosen)
        _save(output, journal)
        groups_report = run_physical_group_stage(chosen, refined, usable, groups_folder, aperture_engine=aperture_engine)
        _finish(output, journal, stage, groups_folder)
    summary['physical_groups'] = {'report': (groups_folder / 'report.json').relative_to(output).as_posix(),
                                  'sha256': _sha(groups_folder / 'report.json'), 'status': groups_report['status'],
                                  'hypothesis_count': groups_report.get('hypothesis_count'),
                                  'bridges_executed': groups_report.get('bridges_executed'),
                                  'selected_hypothesis': groups_report.get('selected_hypothesis'),
                                  'ranking': groups_report.get('ranking')}
    selected = groups_report.get('selected_hypothesis')
    if not selected:
        summary['preparation_status'] = 'no_bridged_hypothesis'
        return region_summary, summary
    partitioned = _inside(groups_folder, selected['partitioned_model']['path'])
    if _sha(partitioned) != selected['partitioned_model']['sha256']:
        raise ValueError('Selected partitioned candidate differs from the physical-group report')
    preparation_report = _inside(groups_folder, selected['preparation_report'])
    prepared = _read(preparation_report)
    if prepared.get('source_sha256') != selected['partitioned_model']['sha256']:
        raise ValueError('Bridged preparation belongs to a different partitioned candidate')
    if appearance_mode == 'semantic_ar_v1':
        from .smooth_optical_geometry import run_smooth_optical_preparation
        completed = _completed(output, journal, 'smooth_optical_geometry')
        if completed:
            _, smooth_folder = completed
            prepared = _read(smooth_folder/'report.json')
            if prepared['smooth_optical_geometry']['source_preparation_sha256'] != _sha(preparation_report):
                raise ValueError('Smooth optical preparation belongs to a different group preparation')
        else:
            stage, smooth_folder = _attempt(output, journal, 'smooth_optical_geometry')
            prepared = run_smooth_optical_preparation(preparation_report, smooth_folder)
            _finish(output, journal, stage, smooth_folder)
        preparation_report = smooth_folder/'report.json'
        summary['smooth_optical_geometry'] = prepared['smooth_optical_geometry']
    summary.update(preparation_status=prepared['status'], preparation_report=preparation_report.relative_to(output).as_posix(),
                   preparation_sha256=_sha(preparation_report))
    cameras_path = _inside(groups_folder, selected['cameras']['path'])
    if _sha(cameras_path) != selected['cameras']['sha256']:
        raise ValueError('Transferred cameras differ from the physical-group report')
    transferred = _read(cameras_path)
    completed = _completed(output, journal, 'hypothesis_regions')
    if completed:
        _, regions_folder = completed
        regions = _read(regions_folder / 'report.json')
    else:
        stage, regions_folder = _attempt(output, journal, 'hypothesis_regions')
        regions = run_region_stage(usable, regions_folder, engine=region_engine, model=partitioned, refinement_report=transferred)
        _finish(output, journal, stage, regions_folder)
    if regions.get('candidate_sha256') != selected['partitioned_model']['sha256']:
        raise ValueError('Hypothesis regions belong to a different partitioned candidate')
    region_summary = {'status': regions['status'], 'report': (regions_folder / 'report.json').relative_to(output).as_posix(),
                      'sha256': _sha(regions_folder / 'report.json'), 'candidate_sha256': regions['candidate_sha256'],
                      'candidate_scope': 'bridged partitioned candidate of the selected hypothesis',
                      'semantic_identity': 'unverified', 'quality_verdict': 'unmeasured', 'material_identification': 'unmeasured'}
    if prepared['status'] != 'prepared_optical_group_candidate':
        return region_summary, summary
    from .joint_photo_lens_stage import run_joint_photo_lens_stage
    exclusions = [{'group_id': gid, 'photo_id': view, 'reason': f"registration: inside share {row['inside_fraction']:.3f} under {group_settings['physical_group_policy']['optical_inside_fraction']}"}
                  for gid, views in (selected.get('view_registration') or {}).items()
                  for view, row in views.items() if not row['fit_eligible']]
    semantic_priors_path = None
    if appearance_mode == 'semantic_ar_v1':
        from .semantic_appearance_stage import prepare_semantic_appearance_inputs
        from .evaluation_stage import capture_job_usage
        capture_job_usage(output, journal, evaluation_context, 'semantics', semantic_photos or photos)
        completed = _completed(output, journal, 'appearance_evidence')
        if completed:
            _, evidence_folder = completed
        else:
            stage, evidence_folder = _attempt(output, journal, 'appearance_evidence')
            prepare_semantic_appearance_inputs(preparation_report, regions_folder / 'report.json', evidence_folder,
                semantic_report=semantic_report, client=semantic_client, declared_facts=lens_facts,
                maximum_samples=lens_maximum_samples, group_photo_exclusions=exclusions or None, photos=semantic_photos,
                aperture_engine=aperture_engine)
            _finish(output, journal, stage, evidence_folder)
        semantic_priors_path = evidence_folder / 'priors.json'
    completed = _completed(output, journal, 'photo_lens_fit')
    if completed:
        fit_stage, fit_folder = completed
        _verify_lens_fit_settings(fit_stage, settings['lens_candidates'])
        _verify_group_stage(output, fit_stage, group_settings, partitioned)
        fitted = _read(fit_folder / 'report.json')
    else:
        stage, fit_folder = _attempt(output, journal, 'photo_lens_fit')
        stage.update(fit_mode=lens_fit_mode, fit_settings=settings['lens_candidates'],
                     fit_settings_sha256=_settings_sha256(settings['lens_candidates']),
                     recovery='Completed stage reused after integrity checks; interruption starts a new job-stage attempt without cross-attempt optimizer checkpoint reuse.')
        _bind_group_stage(output, stage, group_settings, partitioned)
        _save(output, journal)
        exclusions = [{'group_id': gid, 'photo_id': view, 'reason': f"registration: inside share {row['inside_fraction']:.3f} under {group_settings['physical_group_policy']['optical_inside_fraction']}"}
                      for gid, views in (selected.get('view_registration') or {}).items()
                      for view, row in views.items() if not row['fit_eligible']]
        stage['group_photo_exclusions'] = exclusions
        effective_policy, facts_note = lens_policy_for_facts(lens_policy, lens_facts)
        stage['lens_facts'] = facts_note
        _save(output, journal)
        fitted = run_joint_photo_lens_stage(preparation_report, regions_folder / 'report.json', fit_folder,
                                            policy=effective_policy, maximum_samples_per_hypothesis=lens_maximum_samples,
                                            group_photo_exclusions=exclusions or None,
                                            **({'appearance_prior_report': semantic_priors_path} if semantic_priors_path else {}))
        _finish(output, journal, stage, fit_folder)
    previews = [{**row, 'path': _inside(fit_folder, row['path']).relative_to(output).as_posix(),
                 'export': {**row['export'], 'path': _inside(fit_folder, row['export']['path']).relative_to(output).as_posix()}}
                for row in fitted['previews'] if row['status'] == 'diagnostic_preview_exported']
    summary.update(fit_status=fitted['status'], fit_report=(fit_folder / 'report.json').relative_to(output).as_posix(),
                   fit_sha256=_sha(fit_folder / 'report.json'), previews=previews,
                   preview_export_status='diagnostic_previews_available' if previews else 'no_diagnostic_preview')
    if not previews:
        return region_summary, summary
    from .appearance_selection import select_appearance
    completed = _completed(output, journal, 'appearance_selection')
    if completed:
        _, selection_folder = completed
        selection = _read(selection_folder / 'selection.json')
    else:
        stage, selection_folder = _attempt(output, journal, 'appearance_selection')
        selection_folder.mkdir(parents=True)
        fit = _read(_inside(fit_folder, fitted['fit']['path']))
        if _sha(_inside(fit_folder, fitted['fit']['path'])) != fitted['fit']['sha256']:
            raise ValueError('Joint fit artifact differs from its stage receipt')
        selection = select_appearance(fit, fitted, tolerance_codes=appearance_tolerance_codes)
        selection['fit_report_sha256'] = _sha(fit_folder / 'report.json')
        selection['hypothesis'] = {'index': selected['index'], 'composition_rank': selected['composition_rank'],
                                   'composition_score': selected['composition_score']}
        _write(selection_folder / 'selection.json', selection)
        _finish(output, journal, stage, selection_folder)
    if selection.get('fit_report_sha256') != summary['fit_sha256']:
        raise ValueError('Appearance selection belongs to a different fit')
    summary['appearance_selection'] = {'report': (selection_folder / 'selection.json').relative_to(output).as_posix(),
                                       'sha256': _sha(selection_folder / 'selection.json'), 'status': selection['status'],
                                       'method': selection['method'], 'identifiability': selection.get('identifiability'),
                                       'photo_policy_pass': selection.get('photo_policy_pass'), 'policy_verdict': selection.get('policy_verdict'),
                                       'any_candidate_within_policy': selection.get('any_candidate_within_policy'),
                                       'equally_supported_alternatives': selection.get('equally_supported_alternatives')}
    if selection.get('selected'):
        chosen_preview = next(p for p in previews if p['candidate_id'] == selection['selected']['candidate_id'])
        summary['appearance_candidate'] = {'source_artifact': chosen_preview['path'], 'sha256': chosen_preview['sha256'],
                                           'export': chosen_preview['export'], 'candidate_id': chosen_preview['candidate_id'],
                                           'family_assignment': chosen_preview['family_assignment'],
                                           'selection': {'method': selection['method'], 'score_basis': selection['selected']['score_basis'],
                                                         'score_codes': selection['selected']['score_codes'],
                                                         'identifiability': selection.get('identifiability'),
                                                         'photo_policy_pass': selection.get('photo_policy_pass'),
                                                         'policy_verdict': selection.get('policy_verdict')},
                                           'accepted': False, 'quality_verdict': 'unmeasured'}
    if appearance_mode == 'semantic_ar_v1':
        from .semantic_appearance_stage import run_semantic_appearance_stage, render_material_cards
        from .evaluation_stage import capture_job_usage
        capture_job_usage(output, journal, evaluation_context, 'selection', semantic_photos or photos)
        completed = _completed(output, journal, 'semantic_appearance')
        if completed:
            _, semantic_folder = completed
            semantic_result = _read(semantic_folder / 'report.json')
        else:
            stage, semantic_folder = _attempt(output, journal, 'semantic_appearance')
            semantic_result = run_semantic_appearance_stage(preparation_report, regions_folder / 'report.json',
                fit_folder / 'report.json', semantic_folder, semantic_report=evidence_folder / 'semantics.json',
                client=semantic_client, declared_facts=lens_facts, maximum_samples=lens_maximum_samples,
                renderer=appearance_renderer or render_material_cards, group_photo_exclusions=exclusions or None,
                aperture_engine=aperture_engine, photos=semantic_photos)
            _finish(output, journal, stage, semantic_folder)
        choice = semantic_result['selection']['selected']
        summary['semantic_appearance'] = {'report': (semantic_folder / 'report.json').relative_to(output).as_posix(),
                                         'sha256': _sha(semantic_folder / 'report.json'),
                                         'status': semantic_result['status'], 'method': 'semantic_ar_v1'}
        summary['photo_appearance_baseline'] = summary['appearance_selection']
        summary['appearance_selection'] = {**summary['semantic_appearance'],
            'identifiability': 'uncalibrated_visual_preference', 'photo_policy_pass': None,
            'policy_verdict': 'semantic_selection_not_photo_policy', 'any_candidate_within_policy': None,
            'equally_supported_alternatives': []}
        summary['appearance_candidate'] = {
            'source_artifact': (semantic_folder / 'candidate-appearance.glb').relative_to(output).as_posix(),
            'sha256': choice['sha256'], 'candidate_id': choice['candidate_id'],
            'family_assignment': choice['family_assignment'],
            'export': {'path': Path(choice['export']['path']).relative_to(output).as_posix(),
                       'sha256': choice['export']['sha256']},
            'selection': {'method': 'semantic_ar_v1', 'score_basis': semantic_result['selection']['selection_basis'],
                          'score_codes': choice.get('score_codes'), 'identifiability': 'uncalibrated_visual_preference',
                          'photo_policy_pass': None, 'policy_verdict': 'semantic_selection_not_photo_policy'},
            'accepted': False, 'quality_verdict': 'unmeasured'}
        from .delivery_stage import run_delivery_stage
        completed = _completed(output, journal, 'delivery')
        if completed:
            _, delivery_folder = completed
            delivery = _read(delivery_folder / 'report.json')
        else:
            stage, delivery_folder = _attempt(output, journal, 'delivery')
            history_stage = _completed(output, journal, 'multiview_intake')
            history_receipts = []
            if history_stage:
                history_report = history_stage[1] / 'report.json'
                history_receipts.append({'path': str(history_report.resolve()), 'sha256': _sha(history_report)})
            delivery = run_delivery_stage(semantic_folder / 'candidate-appearance.glb', choice['export']['path'],
                regions_folder / 'report.json', delivery_folder, renderer=appearance_renderer or render_material_cards,
                physical_report=groups_folder / 'report.json', selection=choice,
                reconstruction_history_receipts=history_receipts, evaluation_context=evaluation_context,
                refinement_reference=refinement_reference)
            _finish(output, journal, stage, delivery_folder)
        summary['delivery'] = {'report': (delivery_folder / 'report.json').relative_to(output).as_posix(),
                               'sha256': _sha(delivery_folder / 'report.json'), 'quality': delivery['quality']}
        summary['appearance_candidate'].update(
            source_artifact=Path(delivery['model']['path']).relative_to(output).as_posix(),
            sha256=delivery['model']['sha256'],
            export={'path': Path(delivery['export']['path']).relative_to(output).as_posix(),
                    'sha256': delivery['export']['sha256']},
            accepted=delivery['accepted'], quality_verdict=delivery['quality_verdict'])
    return region_summary, summary


def _attempt(output, journal, stage_name):
    history = journal['stages'].setdefault(stage_name, [])
    attempt = {'attempt': len(history) + 1, 'status': 'running',
               'directory': f'stages/{stage_name}/attempt_{len(history)+1}', 'started_at': _now()}
    history.append(attempt)
    _save(output, journal)
    return attempt, _inside(output, attempt['directory'])


def _finish(output, journal, stage, folder, status='complete'):
    stage.update(status=status, finished_at=_now(), artifacts=_inventory(output, folder))
    _save(output, journal)


def _completed(output, journal, name):
    for stage in journal['stages'].get(name, []):
        if stage['status'] == 'complete':
            _verify_stage(output, stage)
            return stage, _inside(output, stage['directory'])
    return None


def _verified_refinement_proposal(folder, refined):
    """Return only proposals checked against their actual exported triangles."""
    if refined.get('status') != 'proposal_exported' or refined.get('rerender_nonregression') is not True:
        return None
    chosen = _inside(folder, refined['proposal_artifact'])
    if _sha(chosen) != refined.get('export', {}).get('output_sha256'):
        raise ValueError('Refined output differs from the exported-geometry receipt')
    scored = [view['rerender'] for view in refined['views'] if 'rerender' in view]
    if len(scored) < 2 or any(row.get('nonregression') is not True
                             or row.get('geometry_source') != 'exported_glb_float32' for row in scored):
        raise ValueError('Retained proposal lacks actual exported-mesh multiview checks')
    return chosen


def _quality(bundle, refinement=None, scale=None):
    refined = bool(refinement and refinement.get('status') == 'proposal_exported'
                   and refinement.get('rerender_nonregression') is True)
    gates = [
        {'id': 'input_snapshot_integrity', 'status': 'pass', 'scope': 'Pinned decoded photos, not input adequacy or product identity.'},
        {'id': 'candidate_reprojection_nonregression', 'status': 'pass' if refined else 'unmeasured',
         'scope': 'Selected candidate-dependent edges only; not an independent shape score.'},
        {'id': 'semantic_component_coverage', 'status': 'unmeasured'},
        {'id': 'camera_and_articulation_identification', 'status': 'unmeasured'},
        {'id': 'component_contact_and_global_intersections', 'status': 'unmeasured'},
        {'id': 'physical_dimensions', 'status': 'unmeasured', 'supplied_mm': bundle['dimensions_mm'],
         'application': scale['application'] if scale else _dimension_application(bundle['dimensions_mm']),
         'scale_factor_source_units_to_meters': (scale.get('receipt') or {}).get('factor_source_units_to_meters') if scale else None},
        {'id': 'photo_lens_material_inference', 'status': 'unmeasured'},
        {'id': 'optical_surface_and_layer_compatibility', 'status': 'unmeasured'},
        {'id': 'actual_candidate_ar_appearance', 'status': 'unmeasured'},
        {'id': 'independent_unseen_product_validation', 'status': 'unmeasured'},
    ]
    return {'verdict': 'unmeasured', 'accepted': False, 'gates': gates,
            'reason': 'This path has not supplied the measured delivery-stage evidence required for acceptance.'}


def _dimension_application(dimensions):
    """The label the scale stage will report for these dimensions (see reconstruction.scale)."""
    if not dimensions:
        return 'not_supplied'
    return 'frame_width_applied_as_uniform_scale_to_meters' if 'frame_width' in dimensions else 'supplied_without_frame_width_unapplied'


def ar_load_parameters(dimensions):
    """What the AR page needs beside the GLB: the stated frame width it scales the model to.

    The AR external-eyewear loader takes ``width`` in millimetres and scales the
    model across its front at load time, so the width is offered as a load
    parameter rather than baked into the bytes. It is the stated dimension only;
    the model's own orientation and extent are not verified against it.
    """
    width = (dimensions or {}).get('frame_width')
    return {'width_mm': width, 'basis': 'stated_dimensions_only' if width is not None else 'not_supplied',
            'consumer': 'ar external eyewear width parameter (millimetres across the front)',
            'verified_against_model': False}


def _verify_originals(bundle):
    # Missing originals are permitted because the job owns complete snapshots.
    for photo in bundle['photos']:
        original = Path(photo['source_path'])
        if original.is_file() and _sha(original) != photo['original']['sha256']:
            raise ValueError('Original photo changed; use a new job directory')


def _verify_initializer_origin(folder):
    source = _read(folder / 'request.json').get('source')
    if source:
        path = Path(source['path'])
        if path.is_file() and _sha(path) != source['sha256']:
            raise ValueError('Original initializer model changed; use a new job directory')


def _report(output, journal, bundle, initializer, *, refinement=None, regions=None, optics=None, candidate=None, status, view_ledger=None, scale=None, transfer=None):
    quality = (optics or {}).get('delivery', {}).get('quality') or _quality(bundle, refinement, scale)
    value = {'schema_version': 1, 'status': status, 'quality_verdict': quality['verdict'],
             'quality': quality, 'request_sha256': journal['request_sha256'],
             'physical_scale': scale,
             'surface_transfer': ({k: transfer.get(k) for k in ('status', 'model', 'generation', 'reason')}
                                  | {'receipt': {k: v for k, v in (transfer.get('receipt') or {}).items() if k != 'primitives'}}) if transfer else None,
             'input_sha256': bundle['input_sha256'], 'implementation': journal['implementation'],
             'settings': journal['settings'], 'initializer': initializer,
             'refinement_status': refinement.get('status') if refinement else None,
             'region_observations': regions,
             'optical_candidates': optics,
             'photos': view_ledger if view_ledger is not None else [
                 {'id': item['id'], 'view_prior': item['view'], 'status': 'awaiting_initializer'} for item in bundle['photos']],
             'candidate': candidate, 'ar_load_parameters': ar_load_parameters(bundle['dimensions_mm']),
             'lens_facts': bundle.get('lens_facts', {}),
             'scope': 'Automation job for current reconstruction stages; existing initial models are supplied evidence, not photos-only generation.',
             'limitations': ['Automatic region hypotheses have unverified identity; product-photo optical inference is unfinished.',
                             'Candidate-dependent image edges do not provide independent validation.',
                             'A stated frame width scales the initial model to metres across its X extent and is offered as the AR load width; neither is verified against the product.',
                             'Actual AR compatibility is recorded by the delivery stage when present; it alone does not establish product appearance.']}
    _write(output / 'report.json', value)
    journal['report_sha256'] = _sha(output / 'report.json')
    journal['status'] = status
    _save(output, journal)
    return value


def run_job(request_path: Path, output: Path, *, resolution=320, camera_evaluations=160,
            backend=None, allow_submit=False, region_engine=None, lens_candidates=False,
            lens_fit_mode='independent', lens_maximum_optimization_runs=None, lens_maximum_samples=512,
            optical_profile=FRONT_SHEET_PROFILE, optical_grouping=None, optical_group_declarations=None,
            aperture_engine=None, appearance_tolerance_codes=1.0, lens_jacobian_mode='finite_difference',
            appearance_mode='photo', semantic_report=None, semantic_client=None, appearance_renderer=None):
    # Inspect grouped paths before resolution can erase a symlink/junction.
    # Legacy jobs keep their established path handling.
    if optical_profile == GROUP_PROFILE:
        from .prepare_optical_groups import _ordinary_path
        output = _ordinary_path(output)
        _ordinary_path(output / '.lock')
        _ordinary_path(output / 'job.json')
    request_path, output = Path(request_path).resolve(), Path(output).resolve()
    if type(resolution) is not int or not 128 <= resolution <= 768 or type(camera_evaluations) is not int or not 20 <= camera_evaluations <= 1000:
        raise ValueError('Use resolution 128–768 and camera evaluations 20–1000')
    request_raw = request_path.read_bytes()
    request = _decode(request_raw)
    has_evaluation = isinstance(request, dict) and bool({'evaluation_photos', 'reserved_photo_ids'} & set(request))
    if appearance_mode not in ('photo', 'semantic_ar_v1'):
        raise ValueError('Unknown appearance mode')
    if has_evaluation and appearance_mode != 'semantic_ar_v1':
        raise ValueError('Reserved evaluation currently requires the complete semantic_ar_v1 delivery path')
    if appearance_mode == 'semantic_ar_v1':
        if optical_grouping != INFERRED_GROUPING or semantic_client is None:
            raise ValueError('semantic_ar_v1 requires physical group inference and an explicit comparison client')
        if lens_jacobian_mode != 'finite_difference':
            raise ValueError('Semantic priors currently require finite_difference')
        # Every declared image is retained by the semantic path. Reject its
        # capacity mismatch before an initializer can reserve a paid task.
        if isinstance(request, dict):
            fit_photos = request.get('photos', [])
            initializer_options = request.get('initializer', {})
            provider_photos = (initializer_options.get('provider_views') or []) if isinstance(initializer_options, dict) else []
            if not has_evaluation and isinstance(fit_photos, list) and isinstance(provider_photos, list) and len(fit_photos) + len(provider_photos) > 12:
                raise ValueError('Semantic interpretation supports at most twelve photos, including provider-only views')
    elif semantic_report is not None or semantic_client is not None or appearance_renderer is not None:
        raise ValueError('Semantic options require appearance_mode=semantic_ar_v1')
    request_hash = hashlib.sha256(request_raw).hexdigest()
    refinement_settings = {'resolution': resolution, 'camera_evaluations': camera_evaluations}
    if type(lens_candidates) is not bool:
        raise ValueError('lens_candidates must be boolean')
    if lens_fit_mode not in ('independent', 'joint'):
        raise ValueError('lens_fit_mode must be independent or joint')
    _validate_optical_options(optical_profile, optical_grouping, optical_group_declarations,
                              lens_candidates, lens_fit_mode)
    if optical_grouping == INFERRED_GROUPING:
        if aperture_engine is None or not callable(getattr(aperture_engine, 'propose', None)) or not callable(getattr(aperture_engine, 'describe', None)):
            raise ValueError('Physical group inference requires an explicitly configured aperture engine')
        if isinstance(appearance_tolerance_codes, bool) or not isinstance(appearance_tolerance_codes, (int, float)) or not 0 <= appearance_tolerance_codes <= 64:
            raise ValueError('appearance_tolerance_codes must lie in 0..64')
    elif aperture_engine is not None:
        raise ValueError('An aperture engine is only used by physical group inference')
    lens_policy = None
    if lens_candidates:
        if region_engine is None:
            raise ValueError('Lens candidate fitting requires an explicitly configured region engine')
        if lens_maximum_optimization_runs is None:
            lens_maximum_optimization_runs = 540 if lens_fit_mode == 'independent' else 2430
        if lens_fit_mode == 'joint':
            from .joint_photo_lens_fit import JointPhotoLensFitPolicy
            lens_policy = JointPhotoLensFitPolicy(maximum_optimization_runs=lens_maximum_optimization_runs,
                                                  jacobian_mode=lens_jacobian_mode)
            if appearance_mode == 'semantic_ar_v1':
                from dataclasses import replace
                lens_policy = replace(lens_policy, mask_search_mode='conditional_seed_beam',
                                      conditional_beam_width=8, maximum_joint_mask_branches=16,
                                      photo_policy=replace(lens_policy.photo_policy,
                                                                        lighting_families=('constant', 'semantic_softbox')))
        else:
            if lens_jacobian_mode != 'finite_difference':
                raise ValueError('The analytic Jacobian is only available for joint fitting')
            from .photo_lens_fit import PhotoLensFitPolicy
            lens_policy = PhotoLensFitPolicy(maximum_optimization_runs=lens_maximum_optimization_runs)
        if type(lens_maximum_samples) is not int or not 16 <= lens_maximum_samples <= 4096:
            raise ValueError('Lens sample capacity must be 16..4096')
    settings = json.loads(json.dumps({**refinement_settings,
        'region_engine': region_engine.describe() if region_engine is not None else None,
        'lens_candidates': {'mode': lens_fit_mode, 'policy': asdict(lens_policy),
                            'maximum_samples': lens_maximum_samples} if lens_policy else None}, allow_nan=False))
    if appearance_mode == 'semantic_ar_v1':
        settings['semantic_appearance'] = {'mode': appearance_mode,
            'semantic_report': str(Path(semantic_report).resolve()) if semantic_report else None,
            'semantic_report_sha256': _sha(semantic_report) if semantic_report else None,
            'client': semantic_client.describe(), 'renderer': 'actual_ar_material_cards_v1',
            'optical_normals': 'source_preserving_smooth_optical_normals_v1', 'grounded_image_evidence': True}
    group_settings, declaration_raw = _capture_group_settings(output, optical_profile, optical_grouping, optical_group_declarations)
    if group_settings is not None:
        if optical_grouping == INFERRED_GROUPING:
            from .appearance_selection import METHOD as SELECTION_METHOD
            from .physical_groups import PhysicalGroupPolicy
            group_settings.update(aperture_engine=aperture_engine.describe(),
                                  physical_group_policy=asdict(PhysicalGroupPolicy()),
                                  appearance_selection={'method': SELECTION_METHOD, 'tolerance_codes': float(appearance_tolerance_codes)})
            group_settings = json.loads(json.dumps(group_settings, allow_nan=False))
        settings['optical_groups'] = group_settings
    implementation = implementation_manifest()
    with _job_lock(output):
        journal_path = output / 'job.json'
        if journal_path.exists():
            journal = _read(journal_path)
            if journal['request_sha256'] != request_hash or journal['request_base'] != str(request_path.parent):
                raise ValueError('Request changed; use a new job directory')
            if journal['settings'] != settings or journal['implementation'] != implementation:
                raise ValueError('Settings or implementation changed; use a new job directory')
            _verify(output, {'request.json': journal['request_sha256']})
            _verify_group_inputs(output, group_settings)
            for name, stages in journal['stages'].items():
                for stage in stages:
                    if stage['status'] == 'complete':
                        _verify_stage(output, stage)
                        if name == 'photo_lens_fit':
                            _verify_lens_fit_settings(stage, settings['lens_candidates'])
                        if name in ('optical_preparation', 'photo_lens_fit', 'physical_groups'):
                            _verify_group_stage(output, stage, group_settings)
            completed_initializer = _completed(output, journal, 'initializer')
            if completed_initializer:
                _verify_initializer_origin(completed_initializer[1])
            if journal.get('terminal'):
                completed_input = _completed(output, journal, 'input')
                if completed_input is None or journal['status'] != 'candidate_available':
                    raise ValueError('Invalid terminal job state')
                _verify_originals(_read(completed_input[1] / 'manifest.json'))
                _verify(output, {'report.json': journal['report_sha256'], **journal['final_artifacts']})
                result = _read(output / 'report.json')
                if result.get('quality_verdict') != 'unmeasured':
                    delivery = result.get('optical_candidates', {}).get('delivery', {})
                    if not delivery or result.get('quality') != delivery.get('quality'):
                        raise ValueError('Quality verdict requires a pinned delivery gate')
                return result
        else:
            if any(path.name != '.lock' for path in output.iterdir()):
                raise ValueError('New job directory must be empty; existing evidence is preserved')
            (output / 'request.json').write_bytes(request_raw)
            if _sha(output / 'request.json') != request_hash:
                raise ValueError('Request changed while being snapshotted')
            if declaration_raw is not None:
                (output / _DECLARATION_SNAPSHOT).write_bytes(declaration_raw)
                _verify_group_inputs(output, group_settings)
            journal = {'schema_version': 1, 'created_at': _now(), 'request_sha256': request_hash,
                       'request_base': str(request_path.parent), 'settings': settings, 'implementation': implementation,
                       'status': 'running', 'terminal': False, 'stages': {}}
            _save(output, journal)

        try:
            from .evaluation_stage import reserve_job_evaluation, capture_job_usage, capture_cached_semantics
            reconstruction_request, evaluation_context = reserve_job_evaluation(request, request_path.parent, output, journal)
            if evaluation_context is not None:
                offered = reconstruction_request.get('photos', []) + reconstruction_request.get('initializer', {}).get('provider_views', [])
                if len(offered) > 12:
                    raise ValueError('Semantic interpretation supports at most twelve reconstruction photos')
                if semantic_report is not None:
                    semantic_report = capture_cached_semantics(output, journal, evaluation_context, semantic_report)
            completed = _completed(output, journal, 'input')
            if completed:
                _, input_folder = completed
                bundle = _read(input_folder / 'manifest.json')
            else:
                stage, input_folder = _attempt(output, journal, 'input')
                bundle = prepare_input_bundle(reconstruction_request, request_path.parent, input_folder)
                _finish(output, journal, stage, input_folder)
            # A resumed job runs immutable copies. If original paths still exist,
            # changed content signals different intent rather than silent reuse.
            _verify_originals(bundle)
            photos = [{'id': item['id'], 'view': item['view'],
                       'path': str(_inside(input_folder, item['normalized']['path'])),
                       'sha256': item['normalized']['sha256'], 'color_space': item['normalized']['color_space'],
                       'normalization_provenance': {'original': item['original'], 'normalized': item['normalized']}}
                      for item in bundle['photos']]
            completed = _completed(output, journal, 'initializer')
            if completed:
                _, initial_folder = completed
                initial = _read(initial_folder / 'initializer-report.json')
                provider_photos = [{**p, 'path': str((request_path.parent / p['path']).resolve())}
                                   for p in bundle.get('initializer', {}).get('provider_views', [])]
                capture_job_usage(output, journal, evaluation_context, 'initializer', photos + provider_photos,
                    external_history={'initializer_folder': str(initial_folder), 'model_path': str(initial_folder / 'initial.glb')})
            else:
                history = journal['stages'].get('initializer', [])
                if history:
                    stage = history[-1]
                    initial_folder = _inside(output, stage['directory'])
                else:
                    stage, initial_folder = _attempt(output, journal, 'initializer')
                provider_photos = [{**p, 'path': str((request_path.parent / p['path']).resolve())}
                                   for p in bundle.get('initializer', {}).get('provider_views', [])]
                capture_job_usage(output, journal, evaluation_context, 'initializer', photos + provider_photos,
                    external_history={'initializer_folder': str(initial_folder), 'model_path': str(initial_folder / 'initial.glb')})
                initial = resolve_initial_model(bundle.get('initializer') or {'kind': 'meshy'}, request_path.parent,
                    initial_folder, photos, backend=backend, allow_submit=allow_submit)
                if initial.get('model'):
                    _finish(output, journal, stage, initial_folder)
                else:
                    stage['status'] = initial['status']
                    _save(output, journal)
                    status = 'initializer_failed' if initial['status'] in ('provider_failed', 'artifact_invalid') else 'awaiting_initializer'
                    return _report(output, journal, bundle, initial, status=status)

            model = _inside(initial_folder, initial['model'])
            if _sha(model) != initial['sha256']:
                raise ValueError('Initializer model hash differs from its report')
            if appearance_mode == 'semantic_ar_v1':
                from .multiview_intake import run_multiview_intake
                completed = _completed(output, journal, 'multiview_intake')
                if completed:
                    _, multiview_folder = completed
                    multiview = _read(multiview_folder / 'report.json')
                else:
                    stage, multiview_folder = _attempt(output, journal, 'multiview_intake')
                    multiview = run_multiview_intake(photos, initial, multiview_folder)
                    _finish(output, journal, stage, multiview_folder)
                photos = multiview['photos']
            capture_job_usage(output, journal, evaluation_context, 'geometry', photos)
            # The textured generation's surface onto the split parts, in the
            # provider's own frame, before anything rescales or exports geometry.
            from .surface_transfer import run_surface_transfer_stage
            generation_model = None
            if isinstance(initial.get('generation'), dict) and initial['generation'].get('model'):
                generation_model = _inside(initial_folder, initial['generation']['model'])
                if _sha(generation_model) != initial['generation']['sha256']:
                    raise ValueError('Retained generation hash differs from the initializer report')
            completed = _completed(output, journal, 'surface_transfer')
            if completed:
                _, transfer_folder = completed
                transfer_report = _read(transfer_folder / 'report.json')
            else:
                stage, transfer_folder = _attempt(output, journal, 'surface_transfer')
                transfer_report = run_surface_transfer_stage(model, generation_model, transfer_folder)
                _finish(output, journal, stage, transfer_folder)
            if transfer_report['status'] == 'transferred':
                if transfer_report['split']['sha256'] != initial['sha256']:
                    raise ValueError('Surface transfer was applied to a different initial model')
                model = _inside(transfer_folder, transfer_report['model']['path'])
                if _sha(model) != transfer_report['model']['sha256']:
                    raise ValueError('Transferred model hash differs from its report')
            # Physical scale from the stated frame width, before any stage that
            # exports geometry: the provider's units are arbitrary and the AR
            # runtime fits the wearer from the model's own metres.
            from .scale import run_scale_stage
            completed = _completed(output, journal, 'scale')
            if completed:
                _, scale_folder = completed
                scale_report = _read(scale_folder / 'report.json')
            else:
                stage, scale_folder = _attempt(output, journal, 'scale')
                scale_report = run_scale_stage(model, bundle['dimensions_mm'], scale_folder)
                _finish(output, journal, stage, scale_folder)
            if scale_report['status'] == 'scaled':
                expected_source = transfer_report['model']['sha256'] if transfer_report['status'] == 'transferred' else initial['sha256']
                if scale_report['source']['sha256'] != expected_source:
                    raise ValueError('Scale stage was applied to a different model')
                model = _inside(scale_folder, scale_report['model']['path'])
                if _sha(model) != scale_report['model']['sha256']:
                    raise ValueError('Scaled model hash differs from its report')
            ledger = [{'id': item['id'], 'view_prior': item['view'], 'status': 'refinement_input',
                       'reason': 'Alpha supplies silhouette evidence only; hidden RGB is excluded from appearance.'
                       if item.get('normalization_provenance',{}).get('normalized',{}).get('has_transparency') else None}
                      for item in photos]
            usable = [PhotoInput(item['id'], Path(item['path']), item['view']) for item, record in zip(photos, ledger)
                      if record['status'] == 'refinement_input']
            completed = _completed(output, journal, 'refinement')
            if completed:
                _, refined_folder = completed
                refined = _read(refined_folder / 'report.json')
            elif len(usable) >= 2:
                stage, refined_folder = _attempt(output, journal, 'refinement')
                refined = refine(model, usable, refined_folder, **refinement_settings)
                _finish(output, journal, stage, refined_folder)
            else:
                refined_folder = None
                refined = {'status': 'insufficient_supported_photos', 'quality_verdict': 'unmeasured'}
            chosen, reason = model, 'initializer_retained_no_supported_refinement'
            proposal = _verified_refinement_proposal(refined_folder, refined)
            if proposal is not None:
                chosen = proposal
                reason = 'photo_guided_geometry_proposal'
            if appearance_mode == 'semantic_ar_v1' and refined.get('normalization'):
                from .geometry_completion import run_geometry_completion
                completed = _completed(output, journal, 'geometry_completion')
                if completed:
                    _, completion_folder = completed
                    completion = _read(completion_folder / 'report.json')
                else:
                    stage, completion_folder = _attempt(output, journal, 'geometry_completion')
                    completion = run_geometry_completion(chosen, refined, photos, completion_folder,
                                                         aperture_engine=aperture_engine)
                    _finish(output, journal, stage, completion_folder)
                if completion.get('candidate'):
                    constructed = completion_folder / completion['candidate']['path']
                    if _sha(constructed) != completion['candidate']['sha256']:
                        raise ValueError('Constructed geometry artifact changed')
                    completed = _completed(output, journal, 'completed_geometry_refinement')
                    if completed:
                        _, completed_folder = completed
                        completed_refined = _read(completed_folder / 'report.json')
                    else:
                        stage, completed_folder = _attempt(output, journal, 'completed_geometry_refinement')
                        completed_refined = refine(constructed, usable, completed_folder, **refinement_settings)
                        _finish(output, journal, stage, completed_folder)
                    chosen, refined, refined_folder = constructed, completed_refined, completed_folder
                    reason = 'multiview_supported_geometry_completion'
                    proposal = _verified_refinement_proposal(refined_folder, refined)
                    if proposal is not None:
                        chosen = proposal
            optical_summary = None
            if optical_grouping == INFERRED_GROUPING:
                capture_job_usage(output, journal, evaluation_context, 'material', photos)
                semantic_photos = None
                if appearance_mode == 'semantic_ar_v1':
                    from .semantic_appearance_stage import collect_semantic_photo_inputs
                    semantic_photos = collect_semantic_photo_inputs(photos, initial)
                region_summary, optical_summary = _run_inferred_grouping(
                    output, journal, settings, group_settings, chosen, refined, photos, ledger,
                    region_engine=region_engine, aperture_engine=aperture_engine, lens_policy=lens_policy,
                    lens_maximum_samples=lens_maximum_samples, lens_fit_mode=lens_fit_mode,
                    appearance_tolerance_codes=appearance_tolerance_codes, lens_facts=bundle.get('lens_facts'),
                    appearance_mode=appearance_mode, semantic_report=semantic_report, semantic_client=semantic_client,
                    appearance_renderer=appearance_renderer, semantic_photos=semantic_photos,
                    evaluation_context=evaluation_context,
                    refinement_reference=({'path': str((refined_folder / 'report.json').resolve()),
                                           'sha256': _sha(refined_folder / 'report.json')} if refined_folder else None))
                if aperture_engine.describe() != group_settings['aperture_engine']:
                    raise ValueError('Aperture engine changed during the job')
            else:
                completed = _completed(output, journal, 'regions')
                if completed:
                    _, regions_folder = completed
                    regions = _read(regions_folder / 'report.json')
                else:
                    stage, regions_folder = _attempt(output, journal, 'regions')
                    regions = run_region_stage(photos, regions_folder, engine=region_engine,
                                               model=chosen, refinement_report=refined)
                    _finish(output, journal, stage, regions_folder)
                region_summary = {'status': regions['status'], 'report': (regions_folder / 'report.json').relative_to(output).as_posix(),
                                  'sha256': _sha(regions_folder / 'report.json'),
                                  'candidate_sha256': regions['candidate_sha256'], 'semantic_identity': 'unverified',
                                  'quality_verdict': 'unmeasured', 'material_identification': 'unmeasured'}
            if region_engine is not None and region_engine.describe() != settings['region_engine']:
                raise ValueError('Region engine changed during the job')
            if lens_candidates and optical_grouping != INFERRED_GROUPING:
                if group_settings is not None:
                    from .prepare_optical_groups import run_optical_group_preparation
                else:
                    from .prepare_optics import run_optical_preparation
                if lens_fit_mode == 'joint':
                    from .joint_photo_lens_stage import run_joint_photo_lens_stage as run_lens_stage
                else:
                    from .photo_lens_stage import run_photo_lens_stage as run_lens_stage
                completed = _completed(output, journal, 'optical_preparation')
                if completed:
                    optical_stage, optical_folder = completed
                    _verify_group_stage(output, optical_stage, group_settings, chosen)
                    prepared = _read(optical_folder / 'report.json')
                else:
                    stage, optical_folder = _attempt(output, journal, 'optical_preparation')
                    if group_settings is not None:
                        _bind_group_stage(output, stage, group_settings, chosen)
                        _save(output, journal)
                        declaration_path = (_inside(output, group_settings['declarations']['snapshot'])
                                            if group_settings['declarations'] else None)
                        _verify_group_inputs(output, group_settings)
                        prepared = run_optical_group_preparation(chosen, optical_folder,
                            grouping_mode=optical_grouping, declarations=declaration_path)
                        _verify_group_inputs(output, group_settings)
                    else:
                        prepared = run_optical_preparation(chosen, optical_folder)
                    _finish(output, journal, stage, optical_folder)
                if prepared['source_sha256'] != _sha(chosen):
                    raise ValueError('Optical preparation belongs to a different retained candidate')
                if group_settings is not None and prepared.get('optical_profile') != optical_profile:
                    raise ValueError('Optical preparation profile differs from pinned group settings')
                optical_summary = {'preparation_status': prepared['status'],
                    'preparation_report': (optical_folder / 'report.json').relative_to(output).as_posix(),
                    'preparation_sha256': _sha(optical_folder / 'report.json'), 'fit_status': 'not_prepared',
                    'fit_mode': lens_fit_mode, 'fit_settings_sha256': _settings_sha256(settings['lens_candidates']),
                    'previews': [], 'selected_material': None, 'quality_verdict': 'unmeasured', 'accepted': False}
                if group_settings is not None:
                    optical_summary.update(optical_profile=optical_profile, grouping_mode=optical_grouping,
                        optical_settings_sha256=_settings_sha256(group_settings),
                        preview_export_status='not_attempted')
                expected_prepared = 'prepared_optical_group_candidate' if group_settings is not None else 'prepared_optical_candidate'
                if prepared['status'] == expected_prepared:
                    completed = _completed(output, journal, 'photo_lens_fit')
                    if completed:
                        fit_stage, fit_folder = completed
                        _verify_lens_fit_settings(fit_stage, settings['lens_candidates'])
                        _verify_group_stage(output, fit_stage, group_settings, chosen)
                        fitted = _read(fit_folder / 'report.json')
                    else:
                        stage, fit_folder = _attempt(output, journal, 'photo_lens_fit')
                        stage.update(fit_mode=lens_fit_mode, fit_settings=settings['lens_candidates'],
                                     fit_settings_sha256=_settings_sha256(settings['lens_candidates']),
                                     recovery='Completed stage reused after integrity checks; interruption starts a new job-stage attempt without cross-attempt optimizer checkpoint reuse.')
                        if group_settings is not None:
                            _bind_group_stage(output, stage, group_settings, chosen)
                        _save(output, journal)
                        effective_policy, facts_note = lens_policy_for_facts(lens_policy, bundle.get('lens_facts'))
                        stage['lens_facts'] = facts_note
                        fitted = run_lens_stage(optical_folder / 'report.json', regions_folder / 'report.json', fit_folder,
                            policy=effective_policy, maximum_samples_per_hypothesis=lens_maximum_samples)
                        _finish(output, journal, stage, fit_folder)
                    optical_summary.update(fit_status=fitted['status'],
                        fit_report=(fit_folder / 'report.json').relative_to(output).as_posix(),
                        fit_sha256=_sha(fit_folder / 'report.json'),
                        previews=[{**row, 'path': (_inside(fit_folder, row['path'])).relative_to(output).as_posix(),
                                   'export': {**row['export'], 'path': _inside(fit_folder, row['export']['path']).relative_to(output).as_posix()}}
                                  for row in fitted['previews'] if row['status'] == 'diagnostic_preview_exported'])
                    if group_settings is not None:
                        optical_summary['preview_export_status'] = ('diagnostic_previews_available'
                            if optical_summary['previews'] else 'no_diagnostic_preview')
            _verify_group_inputs(output, group_settings)
            if implementation_manifest() != implementation:
                raise ValueError('Implementation changed during the job; do not use this result')
            # Verify every completed stage again before producing the final view.
            for name, stages in journal['stages'].items():
                for stage in stages:
                    if stage['status'] == 'complete':
                        _verify_stage(output, stage)
                        if name == 'photo_lens_fit':
                            _verify_lens_fit_settings(stage, settings['lens_candidates'])
                        if name in ('optical_preparation', 'photo_lens_fit', 'physical_groups'):
                            _verify_group_stage(output, stage, group_settings)
            final = output / 'candidate.glb'
            if final.exists() and _sha(final) != _sha(chosen):
                raise ValueError('Uncommitted candidate differs; preserve it and use a new job directory')
            if not final.exists():
                shutil.copyfile(chosen, final)
            candidate = {'path': 'candidate.glb', 'sha256': _sha(final), 'selection': reason,
                         'source_artifact': chosen.relative_to(output).as_posix(), 'quality_verdict': 'unmeasured'}
            final_artifacts = {'candidate.glb': candidate['sha256']}
            appearance = (optical_summary or {}).get('appearance_candidate')
            if appearance:
                target = output / APPEARANCE_CANDIDATE
                source_preview = _inside(output, appearance['source_artifact'])
                if target.exists() and _sha(target) != appearance['sha256']:
                    raise ValueError('Uncommitted appearance candidate differs; preserve it and use a new job directory')
                if not target.exists():
                    shutil.copyfile(source_preview, target)
                if _sha(target) != appearance['sha256']:
                    raise ValueError('Appearance candidate bytes differ from the selected preview')
                candidate['appearance'] = {'path': APPEARANCE_CANDIDATE, 'sha256': appearance['sha256'],
                                           'selection': appearance['selection'], 'accepted': appearance.get('accepted',False),
                                           'quality_verdict': appearance.get('quality_verdict','unmeasured')}
                final_artifacts[APPEARANCE_CANDIDATE] = appearance['sha256']
            value = _report(output, journal, bundle, initial, refinement=refined, regions=region_summary, optics=optical_summary, candidate=candidate, scale=scale_report, transfer=transfer_report,
                            status='candidate_available', view_ledger=ledger)
            journal.update(terminal=True, final_artifacts=final_artifacts)
            _save(output, journal)
            return value
        except Exception as error:
            journal.update(status='execution_failed', error={'type': type(error).__name__, 'message': str(error)})
            _save(output, journal)
            raise


def main(argv=None):
    # The new route uses its own stage contract while sharing the public job
    # entry point. Existing requests and their CLI defaults are unchanged.
    route_parser = argparse.ArgumentParser(add_help=False)
    route_parser.add_argument('--request', type=Path)
    route_args, _ = route_parser.parse_known_args(argv)
    if route_args.request and route_args.request.is_file() and _read(route_args.request).get('pipeline') == 'segmented_ar_v1':
        from .segmented_job import main as segmented_main
        return segmented_main(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resolution', type=int, default=320)
    parser.add_argument('--camera-evaluations', type=int, default=160)
    parser.add_argument('--region-weights', type=Path, help='Explicit local SAM2.1 Base Plus checkpoint; never downloaded')
    parser.add_argument('--region-weights-sha256', help='Optional expected checkpoint SHA-256')
    parser.add_argument('--lens-candidates', action='store_true', help='Prepare optical surfaces and fit diagnostic photo-material alternatives; no accepted material is selected')
    parser.add_argument('--lens-fit-mode', choices=('independent', 'joint'), default='independent',
                        help='Fit groups independently or share photographic illumination across groups (default: independent)')
    parser.add_argument('--optical-profile', choices=(FRONT_SHEET_PROFILE, GROUP_PROFILE), default=FRONT_SHEET_PROFILE,
                        help='Explicit optical representation; effective groups require lens candidates and joint fitting')
    parser.add_argument('--optical-grouping', choices=GROUPING_MODES,
                        help='Required grouping hypothesis for the effective group profile; physical_group_inference infers groups from photos and geometry')
    parser.add_argument('--aperture-weights', type=Path,
                        help='Explicit local Glasses Detector v1 weights folder for physical group inference; never downloaded')
    parser.add_argument('--appearance-tolerance-codes', type=float, default=1.0,
                        help='Family selection keeps every preview within this many interval-error codes of the best and picks the least complex')
    parser.add_argument('--appearance-mode', choices=('photo', 'semantic_ar_v1'), default='photo')
    parser.add_argument('--semantic-report', type=Path, help='Optional pinned semantic report; otherwise infer from job photos')
    parser.add_argument('--semantic-api-key-env', help='Explicit environment variable for image interpretation and blind AR review')
    parser.add_argument('--semantic-model', default='gemini-3.8-flash')
    parser.add_argument('--semantic-cache', type=Path, help='Durable bounded API response cache')
    parser.add_argument('--semantic-maximum-calls', type=int, default=3)
    parser.add_argument('--optical-group-declarations', type=Path,
                        help='Versioned source-bound whole-part group declaration JSON, required only for explicit grouping')
    parser.add_argument('--lens-maximum-optimization-runs', type=int, default=None,
                        help='Optimization cap; omitted defaults to 540 independent or 2430 joint')
    parser.add_argument('--lens-maximum-samples', type=int, default=512,
                        help='Samples per observation; 512 keeps a small far lens above the 24 held-out samples its policy row needs')
    parser.add_argument('--lens-jacobian-mode', choices=('finite_difference', 'analytic'), default='finite_difference',
                        help='Joint-fit derivative mode; analytic is faster and pinned in the policy')
    parser.add_argument('--meshy-api-key-env', help='Explicit environment variable name containing the API key; no automatic discovery')
    parser.add_argument('--allow-meshy-submit', action='store_true', help='Authorize new provider tasks within the explicit aggregate allowance')
    parser.add_argument('--meshy-max-new-tasks', type=int, choices=(0, 1, 2), default=0,
                        help='0 resumes known tasks only; 1 authorizes the generation; 2 also authorizes its split into parts')
    parser.add_argument('--meshy-max-estimated-credits', type=int,
                        help='Aggregate local estimate for new generation/split POSTs in this command; NOT a provider-enforced spending ceiling')
    parser.add_argument('--meshy-wait-seconds', type=float, default=0,
                        help='Keep polling a known pending task for up to this interval (maximum 3600); local cancellation never deletes it')
    parser.add_argument('--meshy-poll-interval', type=float, default=5)
    args = parser.parse_args(argv)
    semantic_client = None
    if args.appearance_mode == 'semantic_ar_v1':
        if not args.semantic_api_key_env or not args.semantic_cache:
            parser.error('semantic_ar_v1 requires --semantic-api-key-env and --semantic-cache')
        key = os.environ.get(args.semantic_api_key_env)
        if not key:
            parser.error('The explicit semantic credential environment variable is missing or empty')
        from .semantic_transport import GeminiSemanticClient
        semantic_client = GeminiSemanticClient(api_key=key, model=args.semantic_model,
            cache_dir=args.semantic_cache, maximum_calls=args.semantic_maximum_calls)
    elif args.semantic_api_key_env or args.semantic_report or args.semantic_cache:
        parser.error('Semantic options require --appearance-mode semantic_ar_v1')
    if args.region_weights_sha256 and not args.region_weights:
        parser.error('--region-weights-sha256 requires --region-weights')
    if args.lens_candidates and not args.region_weights:
        parser.error('--lens-candidates requires --region-weights')
    try:
        _validate_optical_options(args.optical_profile, args.optical_grouping, args.optical_group_declarations,
                                  args.lens_candidates, args.lens_fit_mode)
    except ValueError as error:
        parser.error(str(error))
    if (args.optical_grouping == INFERRED_GROUPING) != (args.aperture_weights is not None):
        parser.error('--aperture-weights is required for, and only for, --optical-grouping physical_group_inference')
    import math
    import re
    import time
    if not math.isfinite(args.meshy_wait_seconds) or not 0 <= args.meshy_wait_seconds <= 3600:
        parser.error('--meshy-wait-seconds must be between 0 and 3600')
    if not math.isfinite(args.meshy_poll_interval) or not 1 <= args.meshy_poll_interval <= 60:
        parser.error('--meshy-poll-interval must be between 1 and 60')
    provider_options = (args.allow_meshy_submit or args.meshy_max_new_tasks or
                        args.meshy_max_estimated_credits is not None or args.meshy_wait_seconds)
    if provider_options and not args.meshy_api_key_env:
        parser.error('Provider options require --meshy-api-key-env with an explicit variable name')
    if args.allow_meshy_submit and (args.meshy_max_new_tasks < 1 or args.meshy_max_estimated_credits is None):
        parser.error('Submission requires --meshy-max-new-tasks 1 or 2 and --meshy-max-estimated-credits')
    if args.meshy_max_estimated_credits is not None and args.meshy_max_estimated_credits < 0:
        parser.error('--meshy-max-estimated-credits must be nonnegative')
    backend = None
    if args.meshy_api_key_env:
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', args.meshy_api_key_env):
            parser.error('--meshy-api-key-env must be an environment variable name, not a credential')
        key = os.environ.get(args.meshy_api_key_env)
        if not key:
            parser.error('The explicitly named API-key environment variable is empty or missing')
        from .initializer import MeshyBackend
        from .meshy_transport import BoundedMeshyTransport, MeshySubmissionBudget
        credits = args.meshy_max_estimated_credits or 0
        # Each transport instance carries at most one POST; the split, when
        # authorized, is a second paid task with its own instance and receipts.
        budget = MeshySubmissionBudget(max_new_tasks=args.meshy_max_new_tasks, max_estimated_credits=credits)
        backend = MeshyBackend(BoundedMeshyTransport(key, max_new_tasks=min(1, args.meshy_max_new_tasks),
                                                   max_estimated_credits=credits, submission_budget=budget),
                               BoundedMeshyTransport(key, max_new_tasks=int(args.meshy_max_new_tasks == 2),
                                                    max_estimated_credits=credits, submission_budget=budget))
    engine = None
    if args.region_weights:
        from .region_engine import OfflineSAM2RegionEngine
        engine = OfflineSAM2RegionEngine(args.region_weights, expected_sha256=args.region_weights_sha256)
    apertures = None
    if args.aperture_weights:
        from .photo_apertures import OfflineLensApertureEngine
        apertures = OfflineLensApertureEngine(args.aperture_weights)
    try:
        result = run_job(args.request, args.output, resolution=args.resolution, camera_evaluations=args.camera_evaluations,
                         region_engine=engine, backend=backend, allow_submit=args.allow_meshy_submit,
                         lens_candidates=args.lens_candidates, lens_fit_mode=args.lens_fit_mode,
                         lens_maximum_optimization_runs=args.lens_maximum_optimization_runs,
                         lens_maximum_samples=args.lens_maximum_samples,
                         optical_profile=args.optical_profile, optical_grouping=args.optical_grouping,
                         optical_group_declarations=args.optical_group_declarations,
                         aperture_engine=apertures, appearance_tolerance_codes=args.appearance_tolerance_codes,
                         lens_jacobian_mode=args.lens_jacobian_mode, appearance_mode=args.appearance_mode,
                         semantic_report=args.semantic_report, semantic_client=semantic_client)
        deadline = time.monotonic() + args.meshy_wait_seconds
        while backend is not None and result.get('initializer', {}).get('status') == 'pending' and time.monotonic() < deadline:
            time.sleep(min(args.meshy_poll_interval, max(0, deadline - time.monotonic())))
            if time.monotonic() >= deadline:
                break
            result = run_job(args.request, args.output, resolution=args.resolution, camera_evaluations=args.camera_evaluations,
                             region_engine=engine, backend=backend, allow_submit=args.allow_meshy_submit,
                             lens_candidates=args.lens_candidates, lens_fit_mode=args.lens_fit_mode,
                             lens_maximum_optimization_runs=args.lens_maximum_optimization_runs,
                             lens_maximum_samples=args.lens_maximum_samples,
                             optical_profile=args.optical_profile, optical_grouping=args.optical_grouping,
                             optical_group_declarations=args.optical_group_declarations,
                             aperture_engine=apertures, appearance_tolerance_codes=args.appearance_tolerance_codes,
                             lens_jacobian_mode=args.lens_jacobian_mode, appearance_mode=args.appearance_mode,
                             semantic_report=args.semantic_report, semantic_client=semantic_client)
    except KeyboardInterrupt:
        if backend is not None:
            backend.transport.cancel_event.set()
        print(json.dumps({'status': 'local_cancelled', 'provider_task_cancelled': False,
                          'resume': 'Use the same request/output to retrieve the existing task; never create a replacement for an uncertain POST.'}))
        raise SystemExit(130) from None
    print(json.dumps({key: result[key] for key in ('status', 'quality_verdict', 'candidate')}))


if __name__ == '__main__':
    main()
