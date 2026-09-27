"""Transactional canonical authoring state, recompilation and exact AR observations.

The source GLB is pre-optics; final exports are disposable compiled artifacts.
No Blender import/export, geometry deletion, topology repair or opaque lens bake.
Callers hold _job_lock(output) for the session lifetime.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil

from .job import _decode, _write, _inventory, _verify, _verify_stage
from .segmented_providers import pin, verified
from .segmented_appearance import _load_preparation
from .optical_group_asset import read_optical_group_candidate, write_optical_group_candidate
from .segmented_astra_geometry import inspect_parts, apply_geometry_edit, set_frame_material
from .segmented_astra_observe import observe_candidate, renderer_fingerprint
from .segmented_astra_tools import validate, appearance, TOOLS_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = 'segmented_astra_session_v1'


def read(path):
    return _decode(Path(path).read_bytes())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def implementation():
    # Pin all Python dependencies in this local pipeline, not only the adapter.
    files = {p.relative_to(ROOT).as_posix(): pin(p)['sha256']
             for folder in ('reconstruction', 'qa') for p in sorted((ROOT / folder).rglob('*.py'))}
    return dict(protocol=PROTOCOL, files=files, renderer=renderer_fingerprint(), tools_sha256=digest(TOOLS_SCHEMA))


def copy_pin(item, destination):
    source = verified(item)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('Refusing to overwrite immutable session input')
    shutil.copyfile(source, destination)
    result = pin(destination)
    if result['sha256'] != item['sha256']:
        raise ValueError('Source changed while copying')
    return result


def candidate_checked(candidate, settings):
    receipt = read(verified(candidate['export']))
    loaded = read_optical_group_candidate(verified(candidate), receipt, expected_sha256=candidate['sha256'])
    triangles = len(loaded['mesh'].faces)
    if triangles > settings['maximum_triangles'] or Path(candidate['path']).stat().st_size > settings['maximum_bytes']:
        raise ValueError('Edited candidate exceeds configured delivery budgets')
    return dict(triangles=triangles, vertices=len(loaded['mesh'].vertices), bytes=Path(candidate['path']).stat().st_size)


def compile_authoring(authoring, output, *, reuse_preparation=None):
    """Rebind every changed source/group/normal hypothesis before optical export."""
    from .prepare_optical_groups import run_optical_group_preparation
    from .smooth_optical_geometry import run_smooth_optical_preparation
    from .segmented_optics import recover_smooth_failed_preparation
    from .compact_glb import run_compact_asset
    from .group_photo_lens_inputs import verify_group_preview_geometry
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = verified(authoring['source'])
    inspection = inspect_parts(source, authoring['groups'])
    if reuse_preparation is not None:
        preparation = verified(reuse_preparation)
        prepared = _load_preparation(preparation)
        if prepared['stage']['source_sha256'] != authoring['source']['sha256']:
            raise ValueError('Reused preparation differs from authoring source')
    else:
        parts = {p['part_id']: p for p in inspection['parts']}
        declarations = dict(schema_version=1, source_sha256=authoring['source']['sha256'],
            coordinate_frame=authoring['coordinate_frame'], provenance=dict(method=PROTOCOL, role_identity='hypothesis'),
            groups=[dict(group_id=gid, members=[dict(id=f'part-{i}', source_part_index=i,
                source_binding={k: parts[i][k] for k in ('node_index', 'mesh_index', 'primitive_index')}) for i in ids])
                for gid, ids in authoring['groups'].items()])
        result = run_optical_group_preparation(source, output / 'preparation',
            grouping_mode='explicit_declarations', declarations=declarations)
        preparation = output / 'preparation/report.json'
        recovered = False
        if result['status'] != 'prepared_optical_group_candidate':
            if authoring['normal_policy'] != 'smooth':
                raise ValueError('Optical preparation refused edit: ' + str(result.get('reasons')))
            recover_smooth_failed_preparation(preparation, output / 'normal-recovery')
            preparation, recovered = output / 'normal-recovery/report.json', True
        if authoring['normal_policy'] == 'smooth' and not recovered:
            run_smooth_optical_preparation(preparation, output / 'smooth-preparation')
            preparation = output / 'smooth-preparation/report.json'
        prepared = _load_preparation(preparation)
    actual_groups = {g['group_id']: [m['source_part_index'] for m in g['members']]
                     for g in prepared['stage']['grouping_inventory']}
    if actual_groups != authoring['groups'] or set(prepared['groups']) != set(authoring['appearances']):
        raise ValueError('Prepared membership differs from authoring state')
    receipt = write_optical_group_candidate(source, output / 'uncompacted.glb',
        [dict(prepared=prepared['groups'][gid], appearance=authoring['appearances'][gid]) for gid in authoring['groups']],
        source_sha256=authoring['source']['sha256'], provenance=dict(method=PROTOCOL, authoring_sha256=digest(authoring)))
    verify_group_preview_geometry(prepared['receipt'], receipt)
    _write(output / 'uncompacted.export.json', receipt)
    compact = run_compact_asset(output / 'uncompacted.glb', output / 'compact', optical_receipt=receipt)
    return dict(candidate={**compact['model'], 'export': compact['export']}, preparation=pin(preparation),
                normal_evidence=prepared['stage'].get('smooth_optical_geometry'), inspection=inspection)


class SegmentedAstraSession:
    def __init__(self, output, *, observer=observe_candidate):
        self.output = Path(output).resolve()
        self.observer = observer
        self.state_path = self.output / 'state.json'
        self.state = read(self.state_path)
        self.seed = read(verified(self.state['seed']))
        if self.seed['implementation'] != implementation():
            raise ValueError('Pipeline/schema/renderer changed; use a new session, preserving the old evidence')
        _verify(self.output, self.seed['input_inventory'])
        for reference in self.state['revisions'].values():
            revision = read(verified(reference))
            _verify(self.output, revision['inventory'])
        self._observation(self.current())  # Include focused evidence outside a revision inventory.

    @classmethod
    def create(cls, base_job, output, *, observer=observe_candidate):
        base_job, output = Path(base_job).resolve(), Path(output).resolve()
        if output.exists() and any(p.name != '.lock' for p in output.iterdir()):
            raise ValueError('Use a new session output directory')
        if base_job == output or base_job.is_relative_to(output):
            raise ValueError('Session output must not contain its base job')
        output.mkdir(parents=True, exist_ok=True)
        journal, report = read(base_job / 'journal.json'), read(base_job / 'report.json')
        if journal.get('pipeline') != 'segmented_ar_v1':
            raise ValueError('A completed segmented_ar_v1 job is required')
        stages = journal['stages']
        for name in ('input', 'optical_preparation', 'appearance', 'appearance_review', 'delivery'):
            if stages.get(name, {}).get('status') != 'complete':
                raise ValueError('Base pipeline is incomplete: ' + name)
            _verify_stage(base_job, stages[name])
        selected = stages['appearance_review']['result']['selected_candidate']
        delivery = stages['delivery']['result']
        if delivery['candidate']['sha256'] != selected['sha256'] or delivery.get('violations'):
            raise ValueError('Base selection/delivery mismatch or unresolved delivery violation')
        preparation = verified(stages['optical_preparation']['result']['preparation_report'])
        if selected['normal_variant'] == 'smooth':
            preparation = Path(stages['appearance']['result']['runtime_manifest']).parent / 'smooth-preparation/report.json'
        loaded = _load_preparation(preparation)
        prep_destination = output / 'inputs/preparation'
        # Copy only the verified preparation files; declarations are separately pinned.
        pins = dict(loaded['pins'])
        if loaded['stage'].get('declarations'):
            item = loaded['stage']['declarations']
            p = preparation.parent / item['path']
            verified(dict(path=str(p), sha256=item['sha256']))
            pins[str(p)] = item['sha256']
        for p, sha in pins.items():
            relative = Path(p).resolve().relative_to(preparation.parent.resolve())
            copy_pin(dict(path=p, sha256=sha), prep_destination / relative)
        preparation = prep_destination / 'report.json'
        loaded = _load_preparation(preparation)
        candidate = copy_pin(delivery['candidate'], output / 'inputs/baseline.glb')
        candidate['export'] = copy_pin(delivery['export'], output / 'inputs/baseline.export.json')
        receipt = read(verified(candidate['export']))
        if receipt['source_sha256'] != loaded['stage']['source_sha256']:
            raise ValueError('Selected candidate does not descend from the supplied preparation')
        for group in receipt['groups']:
            if group['prepared_group_sha256'] != loaded['groups'][group['group_id']]['report']['group_sha256']:
                raise ValueError('Selected candidate/preparation optical group mismatch')
        groups = {g['group_id']: [m['source_part_index'] for m in g['members']]
                  for g in loaded['stage']['grouping_inventory']}
        authoring = dict(source=pin(loaded['source']), groups=groups,
            appearances={g['group_id']: g['appearance'] for g in receipt['groups']},
            coordinate_frame=loaded['stage']['coordinate_frame'],
            normal_policy='smooth' if loaded['stage'].get('smooth_optical_geometry') else 'preserve')
        if any(authoring['coordinate_frame'][k] != v for k, v in
               (('units', 'meters'), ('forward_axis', '+Z'), ('up_axis', '+Y'))):
            raise ValueError('Astra editing requires the canonical metre frame')
        inspect_parts(verified(authoring['source']), groups)
        photos = [{**p, **copy_pin(p, output / 'inputs/photos' / (p['id'] + Path(p['path']).suffix))}
                  for p in stages['input']['result']['photos']]
        seed = dict(protocol=PROTOCOL, product_id=report['product_id'], base_job=str(base_job),
            base_request_binding=journal['request_binding'], baseline=candidate, photos=photos,
            authoring=authoring, preparation=pin(preparation), settings=journal['settings'],
            base_review_reasons=report.get('review_reasons', []), base_role_evidence=report.get('role_inference', {}),
            implementation=implementation(), input_inventory=_inventory(output, output / 'inputs'))
        candidate_checked(candidate, seed['settings'])
        _write(output / 'seed.json', seed)
        # Initialization is restartable after an interrupted render, without provider calls.
        _write(output / 'state.json', dict(protocol=PROTOCOL, seed=pin(output / 'seed.json'), status='initializing',
            revisions={}, current_revision=None, turns=[], focus=None, events=[]))
        session = cls.__new__(cls)
        session.output, session.observer, session.state_path = output, observer, output / 'state.json'
        session.state, session.seed = read(session.state_path), seed
        session.initialize()
        return session

    def save(self):
        _write(self.state_path, self.state)

    def _fresh(self, parent):
        parent.mkdir(parents=True, exist_ok=True)
        index = len(list(parent.glob('attempt-*'))) + 1
        path = parent / f'attempt-{index:04d}'
        path.mkdir(exist_ok=False)
        return path

    def initialize(self):
        if self.state['current_revision'] is not None:
            return
        folder = self._fresh(self.output / 'revisions/r0000')
        record = dict(id='r0000', parent=None, authoring=self.seed['authoring'],
            candidate=self.seed['baseline'], preparation=self.seed['preparation'], note='Exact baseline, unchanged bytes',
            normal_evidence=_load_preparation(verified(self.seed['preparation']))['stage'].get('smooth_optical_geometry'))
        self._observe_and_commit(record, folder)

    def current(self):
        if self.state['current_revision'] is None:
            self.initialize()
        record = read(verified(self.state['revisions'][self.state['current_revision']]))
        _verify(self.output, record['inventory'])
        candidate_checked(record['candidate'], self.seed['settings'])
        return record

    def _observe(self, record, folder, focus=None):
        result = self.observer(record['candidate'], self.seed['baseline'], self.seed['photos'], folder,
            product_id=self.seed['product_id'], width_mm=self.seed['settings']['display_width_mm'],
            part_source=verified(record['authoring']['source']), groups=record['authoring']['groups'], focus=focus)
        if (result.get('status') != 'runtime_compatible' or result.get('candidate_sha256') != record['candidate']['sha256']
                or result.get('baseline_sha256') != self.seed['baseline']['sha256']):
            raise ValueError('AR observation did not validate the exact current candidate')
        _write(folder / 'session-observation.json', result)
        return pin(folder / 'session-observation.json')

    def _observation(self, current):
        observation = read(verified(self.state['current_observation']))
        if (observation.get('candidate_sha256') != current['candidate']['sha256']
                or observation.get('baseline_sha256') != self.seed['baseline']['sha256']
                or observation.get('status') != 'runtime_compatible'):
            raise ValueError('Observation does not bind the exact current candidate and baseline')
        for item in observation['images'] + observation.get('render_images', []):
            verified(item)
        for key in ('render_report', 'request', 'manifest', 'part_evidence'):
            if observation.get(key):
                verified(observation[key])
        if observation.get('renderer_fingerprint') and observation['renderer_fingerprint'] != renderer_fingerprint():
            raise ValueError('Observation renderer changed; use a new session')
        return observation

    def _observe_and_commit(self, record, folder, event=None):
        record['metrics'] = candidate_checked(record['candidate'], self.seed['settings'])
        record['observation'] = self._observe(record, folder / 'observation')
        record['inventory'] = _inventory(self.output, folder)
        _write(folder / 'revision.json', record)
        reference = pin(folder / 'revision.json')
        self.state['revisions'][record['id']] = reference
        self.state.update(current_revision=record['id'], status='editing', focus=None, current_observation=record['observation'])
        if event is not None:
            event['current_revision'] = record['id']
            self.state['events'].append(event)
        self.save()
        return record

    def apply(self, plan, *, turn_id, model_decision=False):
        """Execute an ordered batch atomically; failed attempts never promote."""
        if not isinstance(turn_id, str) or not re.fullmatch(r'turn-[0-9]{4}', turn_id):
            raise ValueError('turn_id must be a driver-owned turn-NNNN identifier')
        if any(e['turn_id'] == turn_id for e in self.state['events']):
            event = next(e for e in self.state['events'] if e['turn_id'] == turn_id)
            if event['plan_sha256'] != digest(plan):
                raise ValueError('A completed turn cannot change its plan')
            return event
        if self.state['status'] == 'finished':
            raise ValueError('Finished sessions are immutable; use a new session')
        current = self.current()
        before = deepcopy(self.state)
        parent = current['id']
        folder = self._fresh(self.output / 'edits' / turn_id)
        _write(folder / 'plan.json', plan)
        event = dict(turn_id=turn_id, parent=parent, plan_sha256=digest(plan), status='applied')
        try:
            validate(plan)
            row = plan['operations'][0]
            if row['operation'] == 'finish':
                self.state.update(status='finished', finish=row,
                    model_reviewed_revision=parent if model_decision else None)
            elif row['operation'] == 'restore':
                if row['revision_id'] not in self.state['revisions']:
                    raise ValueError('Unknown checkpoint')
                restored = read(verified(self.state['revisions'][row['revision_id']]))
                _verify(self.output, restored['inventory'])
                candidate_checked(restored['candidate'], self.seed['settings'])
                self.state.update(current_revision=restored['id'], current_observation=restored['observation'], focus=None)
            elif row['operation'] == 'inspect':
                focus = {k: row[k] for k in ('part_ids', 'padding_fraction')}
                observation = self._observe(current, folder / 'observation', focus)
                self.state.update(focus=focus, current_observation=observation)
            else:
                authoring = deepcopy(current['authoring'])
                rebuild, proofs = False, []
                for index, operation in enumerate(plan['operations']):
                    kind = operation['operation']
                    source = verified(authoring['source'])
                    if kind in ('translate', 'rotate', 'local_bend', 'frame_material'):
                        destination = folder / f'authoring-{index:02d}.glb'
                        if kind == 'frame_material':
                            proof = set_frame_material(source, destination, operation['part_ids'], groups=authoring['groups'],
                                **{k: operation[k] for k in ('base_color_linear_rgb', 'roughness', 'metallic') if operation[k] is not None})
                        else:
                            proof = apply_geometry_edit(source, destination, operation, groups=authoring['groups'])
                        _write(folder / f'proof-{index:02d}.json', proof)
                        proofs.append(pin(folder / f'proof-{index:02d}.json'))
                        authoring['source'], rebuild = pin(destination), True
                    elif kind == 'optical_appearance':
                        if set(operation['group_ids']) - set(authoring['groups']):
                            raise ValueError('Unknown optical group')
                        for group_id in operation['group_ids']:
                            authoring['appearances'][group_id] = appearance(operation['appearance'])
                    elif kind == 'normal_policy':
                        rebuild |= authoring['normal_policy'] != operation['policy']
                        authoring['normal_policy'] = operation['policy']
                    elif kind == 'group_membership':
                        if operation['group_id'] not in authoring['groups']:
                            raise ValueError('Unknown optical group')
                        authoring['groups'][operation['group_id']] = operation['part_ids']
                        inspect_parts(source, authoring['groups'])
                        rebuild = True
                compiled = compile_authoring(authoring, folder / 'compiled',
                    reuse_preparation=None if rebuild else current['preparation'])
                revision_id = f'r{len(self.state["revisions"]):04d}'
                record = dict(id=revision_id, parent=parent, authoring=authoring, candidate=compiled['candidate'],
                    preparation=compiled['preparation'], note=plan['note'], proofs=proofs,
                    normal_evidence=compiled['normal_evidence'], origin_turn=turn_id)
                self._observe_and_commit(record, folder, event)
                return event  # Promotion and execution event were one atomic journal update.
            event['current_revision'] = self.state['current_revision']
        except Exception as error:
            # Promotion is the final atomic step; all prior work lives in a fresh attempt.
            self.state = before
            event.update(status='rejected', error_type=type(error).__name__, error=str(error), current_revision=parent)
        _write(folder / 'event.json', event)
        # Do not add files to a committed revision inventory after its manifest.
        self.state['events'].append(event)
        self.save()
        return event

    def snapshot(self):
        current = self.current()
        observation = self._observation(current)
        inspection = inspect_parts(verified(current['authoring']['source']), current['authoring']['groups'])
        context = dict(protocol=PROTOCOL, product_id=self.seed['product_id'], current_revision=current['id'],
            current_candidate=current['candidate'], baseline=self.seed['baseline'], authoring=current['authoring'],
            inspection=inspection, metrics=current['metrics'], normal_evidence=current.get('normal_evidence'),
            available_checkpoints=[dict(id=i, note=read(verified(r))['note']) for i, r in self.state['revisions'].items()],
            previous_events=self.state['events'][-6:], base_review_reasons=self.seed['base_review_reasons'],
            role_hypotheses={k:self.seed['base_role_evidence'].get(k) for k in
                ('primary_groups', 'hypotheses', 'review_reasons', 'role_selection_required')},
            image_limitations=observation.get('limitations', []), focus=self.state['focus'],
            task='Improve faithful product appearance. Face-width fitting happens in AR. Reference photos have unknown cameras and lighting. Finish only after observing post-edit results.',
            accepted=False)
        images = [dict(id='photo-' + p['id'], label='Original product photo: ' + p['view'], **{k:p[k] for k in ('path', 'sha256')})
                  for p in self.seed['photos']] + observation['images']
        return context, images

    def deliver(self, reason):
        current = self.current()
        self._observation(current)
        result = dict(schema_version=1, pipeline=PROTOCOL, product_id=self.seed['product_id'],
            status='candidate_available', stop_reason=reason, candidate=current['candidate'],
            baseline=self.seed['baseline'], current_revision=current['id'], authoring=current['authoring'],
            observation=self.state['current_observation'], metrics=current['metrics'],
            session=pin(self.state_path), model_reviewed_current=self.state.get('model_reviewed_revision') == current['id'],
            finish=self.state.get('finish'), accepted=False, requires_review=True, production_ready=False,
            limitations=['Appearance accuracy is a review hypothesis; no calibrated photo or manufacturing ground truth.',
                'Geometry guards are local/sampled, not a global intersection or watertightness certificate.',
                'Synthetic AR views do not measure real-face/mobile performance.'])
        _write(self.output / 'report.json', result)
        return result
