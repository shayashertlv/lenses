"""Conditional multi-group photo optics with shared photographic nuisance.

``groups`` is a nonempty list of {surface_binding, observations}, using the exact
photo_lens_fit contracts. Group IDs are binding.material_group_id; all groups
must bind the same prepared GLB. IDs/regions are namespaced internally, but raw
photo/source/pixel claims are checked across groups before fitting. Co-occurring
eligible ownership of one pixel by different groups is unsupported: this is a
single-interface observation model, not an optical-layer compositor.

An optional family_assignments list contains complete {group_id: family} maps.
The default explores five shared-FAMILY assignments, with independent numerical
materials, and explicitly does not explore mixed-family combinations. Rear
colors remain group/photo-specific. Optional rear_source_bindings entries
{group_id, photo_id, object_id, source_sha256} assert that the unknown constant
rear color is shared with the same object in that photo; this assertion is not
inferred from geometry. Photographic exposure/WB anchors are global within each
connected group/photo component. Disconnected components have independent gauges.

One optimizer result is stored per start. Per-group roughness alternatives are
factorized, exactly representing their Cartesian product without repeated fits.
Every material, nuisance, mask and coordinate remains a conditional hypothesis.
No parameter identification, hypothesis selection or AR acceptance is performed.
Finite differences remain the default optimizer Jacobian. The explicit analytic
option differentiates the same objective, but can follow a different local
trajectory; its solutions are not substituted for an earlier numerical run.

checkpoint_dir is an optional local, single-writer directory. Exact inputs,
implementation bytes and numerical versions are pinned. Each completed start is
atomically checksummed; an interruption returns no partial completed report.
progress receives deterministic plan/start/completed events (including resumed
starts) and may raise to interrupt after a completed checkpoint is durable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import re
from typing import Callable

import numpy as np
import scipy
from scipy.optimize import least_squares

from . import lens_appearance, photo_lens_fit as single, photo_lens_derivatives as derivatives, material_relations, grounded_fit_support
from .atomic_files import replace_with_retry


@dataclass(frozen=True)
class JointPhotoLensFitPolicy:
    photo_policy: single.PhotoLensFitPolicy = field(default_factory=single.PhotoLensFitPolicy)
    maximum_groups: int = 4
    maximum_joint_mask_branches: int = 243
    maximum_optimization_runs: int = 2430
    maximum_candidates: int = 10000
    jacobian_mode: str = 'finite_difference'
    mask_search_mode: str = 'exhaustive'
    conditional_beam_width: int = 8
    rear_response_hypotheses: tuple[str,...] = ('free_rear_reflection','weak_rear_reflection')

    def __post_init__(self):
        if not isinstance(self.photo_policy, single.PhotoLensFitPolicy):
            raise ValueError('photo_policy must be PhotoLensFitPolicy')
        if not isinstance(self.jacobian_mode, str) or self.jacobian_mode not in ('finite_difference', 'analytic'):
            raise ValueError('jacobian_mode must be finite_difference or analytic')
        if self.mask_search_mode not in ('exhaustive', 'conditional_seed_beam'):
            raise ValueError('mask_search_mode must be exhaustive or conditional_seed_beam')
        if type(self.conditional_beam_width) is not int or not 1 <= self.conditional_beam_width <= 64:
            raise ValueError('conditional_beam_width must lie in 1..64')
        if self.mask_search_mode == 'conditional_seed_beam' and self.photo_policy.rear_content != 'excluded':
            raise ValueError('Conditional mask search currently requires rear_content=excluded')
        modes=self.rear_response_hypotheses
        if (not isinstance(modes,(tuple,list)) or not modes or len(set(modes))!=len(modes)
                or any(v not in ('free_rear_reflection','weak_rear_reflection') for v in modes)):
            raise ValueError('Invalid bounded rear-response hypothesis set')
        object.__setattr__(self,'rear_response_hypotheses',tuple(modes))
        for name in ('maximum_groups', 'maximum_joint_mask_branches', 'maximum_optimization_runs', 'maximum_candidates'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f'{name} must be a positive integer')


def _identity(row):
    return {'group_id': row['group_id'], 'observation_id': row['original_id'],
            'photo_id': row['photo_id'], 'region_id': row['original_region_id'],
            'hypothesis_id': row['hypothesis_id']}


def _prepare_joint(groups, policy):
    if not isinstance(groups, list) or not 1 <= len(groups) <= policy.maximum_groups:
        raise ValueError('groups must be a bounded nonempty list')
    bindings, observations, identities = {}, [], {}
    p = replace(policy.photo_policy, maximum_mask_branches=policy.maximum_joint_mask_branches)
    deferred = policy.mask_search_mode == 'conditional_seed_beam'
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'surface_binding', 'observations'}:
            raise ValueError('Each group requires exactly surface_binding and observations')
        # Reuse the authoritative binding/observation validation; global _prepare
        # below determines the only split and combined branch product.
        binding, _, _, _, _ = single._prepare(group['observations'], group['surface_binding'], p, defer_large_branches=deferred)
        gid = binding['material_group_id']
        if gid in bindings:
            raise ValueError('material_group_id must be unique')
        bindings[gid] = binding
        for observation in group['observations']:
            row = dict(observation)
            row['id'] = json.dumps([gid, observation['id']], separators=(',', ':'))
            row['region_id'] = json.dumps([gid, observation['region_id']], separators=(',', ':'))
            identities[row['id']] = (gid, observation['id'], observation['region_id'])
            observations.append(row)
    if len({b['prepared_glb_sha256'] for b in bindings.values()}) != 1:
        raise ValueError('Joint groups must bind the same prepared GLB')
    common = dict(bindings[sorted(bindings)[0]], material_group_id='joint_group_validation')
    _, records, branches, aliases, split = single._prepare(observations, common, p, defer_large_branches=deferred)
    for r in records:
        r['group_id'], r['original_id'], r['original_region_id'] = identities[r['id']]
    # Unknown-vs-known changes are contradictory geometry/support claims, not
    # independent mask membership. Membership may vary by omitting the pixel.
    pixel_claims, group_claims = {}, {}
    for r in records:
        a = r['arrays']
        for i, xy in enumerate(a['xy']):
            key = (r['photo_id'], *xy)
            background = tuple(a['background'][i])
            if key in pixel_claims and pixel_claims[key] != background:
                raise ValueError('One photo pixel has contradictory background claims across groups')
            pixel_claims[key] = background
            gkey = (r['group_id'], *key)
            claim = {name: single._array_hash(np.asarray(a[name][i])) for name in
                     ('v', 'angle', 'direction', 'rear', 'rear_weight')}
            claim['rear_supplied'] = r['has_rear']
            if gkey in group_claims and group_claims[gkey] != claim:
                raise ValueError('One group/photo pixel has contradictory coordinate or rear/support claims')
            group_claims[gkey] = claim
    # The approximate search checks union ownership conservatively: it will not
    # choose masks to hide cross-group layered rays from the single-interface
    # observation model. Exhaustive mode keeps its historical branch-wise check.
    for branch in ([records] if not branches and deferred else branches):
        owners = {}
        for r in branch:
            for xy in r['arrays']['xy'][r['eligible']]:
                key = (r['photo_id'], *xy)
                if key in owners and owners[key] != r['group_id']:
                    raise ValueError('One source pixel has overlapping eligible ownership by different groups; layered observations unsupported')
                owners[key] = r['group_id']
    for alias in aliases:
        gid, region = json.loads(alias['region_id'])
        alias.update(group_id=gid, region_id=region)
        alias['observation_ids'] = [json.loads(v)[1] for v in alias['observation_ids']]
    return dict(sorted(bindings.items())), records, branches, aliases, split


def _conditional_seed_branches(records, bindings, assignments, p, priors, policy):
    """Bounded approximate branch proposals conditioned on fixed material starts.

    This is a seed beam, not exhaustive enumeration or a global optimizer.
    Candidate evaluation later uses the same union of supplied observations,
    preventing a smaller mask from concealing a badly predicted source pixel.
    """
    grouped = {}
    for row in records:
        grouped.setdefault((row['photo_id'], row['region_id']), {}).setdefault(row['numeric_sha256'], row)
    options = [list(v.values()) for _, v in sorted(grouped.items())]
    union = [r for rows in options for r in rows]
    possible = int(np.prod([len(rows) for rows in options], dtype=object))
    largest = tuple(max(rows, key=lambda r: (int(r['train'].sum()), r['numeric_sha256'])) for rows in options)
    proposals = {tuple(r['id'] for r in largest): (float('inf'), largest)}
    seeds = 0
    for assignment, lighting in itertools.product(assignments, p.lighting_families):
        if lighting == 'semantic_softbox' and any(r['image_size'] is None for r in union):
            continue
        if lighting == 'smooth' and any(not np.isfinite(r['arrays']['direction'][r['eligible']]).all() for r in union):
            continue
        if any(not any(r['group_id'] == gid and r['train'].any() for r in union) for gid in bindings):
            continue
        model = _JointModel(union, assignment, lighting, {g:'rear_content_excluded' for g in bindings}, p, {}, priors)
        for start in range(min(3, p.starts)):
            x, costs = model.initial(start), {}
            for gid, local_model in model.models.items():
                for data in local_model.data:
                    row = data['observation']
                    residual = single._interval_residual(local_model.predict(x[model.maps[gid]], data), data['arrays']['code'])[data['train']]
                    robust = single._robust_residual(residual/p.code_robust_scale)
                    costs[row['id']] = float(np.mean(robust*robust)) if len(robust) else float('inf')
            beam = [(0., ())]
            for rows in options:
                expanded = [(cost+costs[row['id']], chosen+(row,)) for cost, chosen in beam for row in rows
                            if np.isfinite(costs[row['id']])]
                expanded.sort(key=lambda item: (item[0], tuple(r['id'] for r in item[1])))
                beam = expanded[:policy.conditional_beam_width]
                if not beam:
                    break
            seeds += 1
            for cost, branch in beam:
                key = tuple(r['id'] for r in branch)
                if key not in proposals or cost < proposals[key][0]:
                    proposals[key] = (cost, branch)
    largest_key = tuple(r['id'] for r in largest)
    ranked = sorted(proposals.items(), key=lambda item: (item[1][0], item[0]))
    selected = [largest]
    selected.extend(value[1] for key, value in ranked if key != largest_key)
    selected = selected[:policy.maximum_joint_mask_branches]
    return selected, union, {'mode':'conditional_seed_beam', 'cartesian_branch_count':possible,
        'conditional_seed_count':seeds, 'beam_width':policy.conditional_beam_width,
        'proposed_branch_count':len(proposals), 'fitted_branch_count':len(selected),
        'complete_mask_exploration':False, 'optimization_guarantee':'bounded_local_candidates_no_global_optimum',
        'evaluation_domain':'frozen_union_of_all_supplied_eligible_observation_pixels',
        'evaluation_observation_ids':sorted(r['id'] for r in union),
        'largest_training_support_branch_retained':True,
        'limitations':['Beam energies use fixed material/lighting starts; subsequent optimization does not guarantee the best omitted mask.',
                       'Every supplied mask contributes to the common evaluation union, including erroneous mask alternatives.']}


def _assignments(value, group_ids, p):
    default = value is None
    if default:
        value = [{g: family for g in group_ids} for family in p.families]
    if not isinstance(value, list) or not value:
        raise ValueError('family_assignments must be a nonempty list of complete maps')
    result = []
    for row in value:
        if not isinstance(row, dict) or set(row) != set(group_ids) or any(v not in p.families for v in row.values()):
            raise ValueError('Family assignments must name every group and use policy families')
        normalized = {g: row[g] for g in group_ids}
        if normalized in result:
            raise ValueError('Duplicate family assignment')
        result.append(normalized)
    return sorted(result, key=single._hash), default


def _rear_bindings(value, records):
    if value is None:
        return {}
    if not isinstance(value, list):
        raise ValueError('rear_source_bindings must be a list')
    present = {(r['group_id'], r['photo_id']) for r in records}
    result = {}
    objects = {}
    for row in value:
        if not isinstance(row, dict) or set(row) != {'group_id', 'photo_id', 'object_id', 'source_sha256'}:
            raise ValueError('Rear binding requires group_id, photo_id, object_id, source_sha256')
        row = single._json(row, 'rear_source_binding')
        for name in ('group_id', 'photo_id', 'object_id'):
            single._text(row[name], name)
        single._sha(row['source_sha256'], 'rear source_sha256')
        key = (row['group_id'], row['photo_id'])
        if key not in present or key in result:
            raise ValueError('Rear binding must uniquely identify an observed group/photo')
        object_key = (row['photo_id'], row['object_id'])
        if object_key in objects and objects[object_key] != row['source_sha256']:
            raise ValueError('Rear object has contradictory source hashes')
        objects[object_key] = row['source_sha256']
        result[key] = row
    return result


def _components(branch):
    graph = {}
    for r in branch:
        if r['train'].any():
            a, b = ('group', r['group_id']), ('photo', r['photo_id'])
            graph.setdefault(a, set()).add(b); graph.setdefault(b, set()).add(a)
    unseen, result = set(graph), []
    while unseen:
        pending, visited = [min(unseen)], set()
        while pending:
            item = pending.pop()
            if item in visited:
                continue
            visited.add(item); pending.extend(graph[item] - visited)
        unseen -= visited
        photos = sorted(v for kind, v in visited if kind == 'photo')
        result.append({'group_ids': sorted(v for kind, v in visited if kind == 'group'),
                       'photo_ids': photos, 'exposure_white_balance_anchor_photo': photos[0]})
    return sorted(result, key=lambda v: v['photo_ids'])


class _JointModel:
    """Alias the frozen forward model; regularize each shared nuisance once."""
    def __init__(self, branch, assignment, lighting, rear_modes, p, rear_bindings, appearance_priors=None,
                 material_relation=None, rear_response_hypothesis='free_rear_reflection'):
        self.policy, self.branch = p, branch
        self.material_relation = material_relation or {'mode': 'independent'}
        shared_groups = self.material_relation.get('shared_group_sets', [])
        self.material_owners = {g: ('material', g) for g in assignment}
        for number, groups in enumerate(shared_groups):
            if len({assignment[g] for g in groups}) != 1:
                raise ValueError('Shared material groups require the same response family')
            for gid in groups:
                self.material_owners[gid] = ('shared_material', self.material_relation['relation_ids'][number])
        self.components = _components(branch)
        anchors = {g: c['exposure_white_balance_anchor_photo'] for c in self.components for g in c['group_ids']}
        self.models, self.maps, self.blocks = {}, {}, {}
        self.lower, self.upper = [], []
        for gid in sorted(assignment):
            rows = [r for r in branch if r['group_id'] == gid]
            paired = [g for g in assignment if self.material_owners[g] == self.material_owners[gid]]
            photo_priors=(appearance_priors or {}).get('photos',{})
            group_priors=(appearance_priors or {}).get('groups',{})
            shared_rear=any(photo_priors.get(r['photo_id'],{}).get('interface')=='rear'
                           for r in branch if r['group_id'] in paired)
            shared_rear |= assignment[gid] not in ('gradient_tint','gradient_angular_mirror') and any(
                group_priors.get(g,{}).get('rear_image_constraints') for g in paired)
            model = single._Model(rows, assignment[gid], lighting, rear_modes[gid], p, appearance_priors, gid,
                                  shared_rear_response=shared_rear,rear_response_hypothesis=rear_response_hypothesis)
            # A group's local first photo is NOT another calibration anchor.
            for photo in model.photos:
                if photo != anchors[gid] and (photo, 'exposure') not in model.slices:
                    model._add((photo, 'exposure'), 1, -p.maximum_exposure_stops*np.log(2), p.maximum_exposure_stops*np.log(2))
                    model._add((photo, 'wb'), 2, -p.maximum_white_balance_log, p.maximum_white_balance_log)
            mapping = np.empty(len(model.lower), dtype=int)
            for key, sl in model.slices.items():
                if isinstance(key, str):
                    shared_key = (*self.material_owners[gid], key)
                elif key[1] == 'rear':
                    binding = rear_bindings.get((gid, key[0]))
                    identity = ('object', binding['object_id'], binding['source_sha256']) if binding else ('group', gid)
                    shared_key = ('rear', key[0], json.dumps(identity, separators=(',', ':')))
                else:
                    shared_key = ('photo', key[0], key[1])
                bounds = (model.lower[sl], model.upper[sl])
                if shared_key not in self.blocks:
                    start = len(self.lower)
                    self.blocks[shared_key] = slice(start, start+sl.stop-sl.start)
                    self.lower.extend(bounds[0]); self.upper.extend(bounds[1])
                dest = self.blocks[shared_key]
                if self.lower[dest] != bounds[0] or self.upper[dest] != bounds[1]:
                    raise ValueError('Aliased parameter bounds differ')
                mapping[sl] = np.arange(dest.start, dest.stop)
            self.models[gid], self.maps[gid] = model, mapping
        self.unique_training_pixels, multiplicities = {}, {}
        for model in self.models.values():
            for d in model.data:
                photo = d['observation']['photo_id']
                counts = multiplicities.setdefault(photo, {})
                for xy in d['arrays']['xy'][d['train']]:
                    key = tuple(xy); counts[key] = counts.get(key, 0)+1
        for photo, counts in multiplicities.items():
            self.unique_training_pixels[photo] = len(counts)
        for model in self.models.values():
            for d in model.data:
                counts = multiplicities[d['observation']['photo_id']]
                d['train_weights'] = np.asarray([1/np.sqrt(3*len(counts)*counts[tuple(xy)]) for xy in d['arrays']['xy'][d['train']]])

    def initial(self, start):
        x = np.full(len(self.lower), np.nan)
        shared_initials = {}
        for gid, model in self.models.items():
            local = model.initial(start); indices = self.maps[gid]
            known = np.isfinite(x[indices])
            material = np.zeros(len(local), dtype=bool)
            if self.material_owners[gid][0] == 'shared_material':
                for key, sl in model.slices.items():
                    if isinstance(key, str):
                        material[sl] = True
                        shared_initials.setdefault((*self.material_owners[gid], key), []).append(local[sl])
            nuisance_known = known & ~material
            if np.any(x[indices][nuisance_known] != local[nuisance_known]):
                raise ValueError('Shared parameter initialization mismatch')
            x[indices] = local
        # Distinct source anchors can initialize the same manufactured lens
        # response differently. Average starts symmetrically; the objective
        # retains every actual anchor with its own measured uncertainty.
        for key, values in shared_initials.items():
            x[self.blocks[key]] = np.mean(values, axis=0)
        return x

    def residual(self, x):
        parts = []
        regularized_materials = set()
        for gid, model in self.models.items():
            local = x[self.maps[gid]]
            for d in model.data:
                error = single._interval_residual(model.predict(local, d), d['arrays']['code'])[d['train']]/self.policy.code_robust_scale
                parts.append((single._robust_residual(error)*d['train_weights'][:, None]).ravel())
            # Numerical material anchors belong to this group, but photographic
            # nuisance regularizers must occur only once per shared block.
            owner = self.material_owners[gid]
            if owner not in regularized_materials:
                parts.append(model.material_regularization(local))
                regularized_materials.add(owner)
            parts.append(model.material_anchor_penalties(local))
        for key, sl in self.blocks.items():
            if key[0] == 'photo' and key[2] in ('shape', 'exposure', 'wb', 'softbox'):
                parts.append(self.policy.nuisance_penalty*x[sl])
        return np.concatenate(parts)

    def parameter_receipt(self):
        return [{'key': list(key), 'start': sl.start, 'count': sl.stop-sl.start,
                 'referenced_by_groups': [g for g, indices in self.maps.items() if np.any((indices >= sl.start)&(indices < sl.stop))]}
                for key, sl in self.blocks.items()]


def _implementation():
    return {'files': {Path(m.__file__).name: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
                      for m in (single, lens_appearance, derivatives, material_relations, grounded_fit_support)},
            'joint_photo_lens_fit.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'python': platform.python_version(), 'numpy': np.__version__, 'scipy': scipy.__version__}


def _atomic(path, payload):
    encoded = json.dumps({'payload': payload, 'sha256': single._hash(payload)}, sort_keys=True, allow_nan=False)
    temporary = path.with_suffix('.pending')
    if temporary.is_symlink() or temporary.exists() and not temporary.is_file():
        raise ValueError('Pending checkpoint must be an ordinary local file')
    with temporary.open('w', encoding='utf-8', newline='\n') as handle:
        handle.write(encoded); handle.flush(); os.fsync(handle.fileno())
    replace_with_retry(temporary, path)


def _read_checkpoint(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Checkpoint must be an ordinary local file')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if set(value) != {'payload', 'sha256'} or single._hash(value['payload']) != value['sha256']:
            raise ValueError('Checkpoint checksum mismatch')
        return value['payload']
    except (TypeError, KeyError, json.JSONDecodeError) as error:
        raise ValueError('Invalid checkpoint receipt') from error


def _checkpoints(directory, request, expected_keys):
    if directory is None:
        return None, {}
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('Checkpoint directory cannot be a symlink')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/'request.json'
    if path.exists():
        if _read_checkpoint(path) != request:
            raise ValueError('Checkpoint request differs from immutable input/policy/implementation')
    elif any(directory.iterdir()):
        # A crash during the very first atomic write has no completed starts to
        # reuse. Retry that write only if its pending file is the sole artifact.
        contents = list(directory.iterdir())
        if len(contents) != 1 or contents[0].name != 'request.pending' or contents[0].is_symlink() or not contents[0].is_file():
            raise ValueError('Nonempty checkpoint directory lacks its immutable request')
        _atomic(path, request)
    else:
        _atomic(path, request)
    saved = {}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError('Checkpoint artifacts must be ordinary local files')
        if path.name in ('request.json', 'request.pending'):
            continue
        if not re.fullmatch(r'[a-f0-9]{64}\.(json|pending)', path.name) or path.stem not in expected_keys:
            raise ValueError('Unexpected checkpoint artifact')
        if path.suffix == '.json':
            receipt = _read_checkpoint(path)
            if receipt.get('run_key') != path.stem or receipt.get('input_sha256') != request['input_sha256']:
                raise ValueError('Checkpoint start identity mismatch')
            saved[path.stem] = receipt
    return directory, saved


def _photo_support(rows, split):
    return {photo: len({tuple(xy) for r in rows if r['photo_id'] == photo for xy in r['arrays']['xy'][r[split]]})
            for photo in sorted({r['photo_id'] for r in rows})}


def _emit_candidate(model, fitted, input_sha, config, start, bindings, p, jacobian_mode):
    if (not isinstance(fitted, dict) or set(fitted) != {'x', 'cost', 'success', 'status', 'nfev'}
            or type(fitted['success']) is not bool or type(fitted['status']) is not int
            or type(fitted['nfev']) is not int or not 1 <= fitted['nfev'] <= p.max_nfev
            or isinstance(fitted['cost'], bool) or not isinstance(fitted['cost'], (int, float))
            or not np.isfinite(fitted['cost']) or fitted['cost'] < 0):
        raise ValueError('Invalid optimizer metadata in completed start')
    x = np.asarray(fitted['x'], dtype=float)
    if x.shape != (len(model.lower),) or not np.isfinite(x).all() or np.any(x < model.lower) or np.any(x > model.upper):
        raise ValueError('Saved optimizer vector is invalid')
    residual = model.residual(x)
    cost = float(np.dot(residual, residual)/2)
    if not np.isfinite(residual).all() or not np.isclose(cost, fitted['cost'], rtol=1e-10, atol=1e-12):
        raise ValueError('Saved optimizer objective differs from exact replay')
    groups, shared = {}, {}
    supported, within = True, True
    evaluation_model = getattr(model, 'evaluation_model', model)
    for gid, local_model in model.models.items():
        local = x[model.maps[gid]]
        evaluator = evaluation_model.models[gid]
        metrics = evaluator.measurements(x[evaluation_model.maps[gid]])
        for row, data in zip(metrics, evaluator.data):
            source = data['observation']
            row.update(observation_id=source['original_id'], region_id=source['original_region_id'], hypothesis_id=source['hypothesis_id'])
        support = {split: _photo_support(evaluator.branch, split) for split in ('train', 'validation')}
        group_supported = all(n >= p.minimum_validation_points_per_photo for n in support['validation'].values())
        compared = [r[s] for r in metrics for s in ('train', 'validation') if r[s]['points']]
        group_within = group_supported and all(m['fraction_channels_within_policy'] >= p.required_within_tolerance_fraction for m in compared)
        supported &= group_supported; within &= group_within
        appearances = [{'roughness': r, 'appearance': local_model.appearance(local, r).to_dict()} for r in p.roughness_values]
        rear = []
        for nuisance in local_model.nuisance(local):
            photo = nuisance['photo_id']
            if 'unknown_rear_rgb' in nuisance:
                rear.append({'photo_id': photo, 'unknown_rear_rgb': nuisance.pop('unknown_rear_rgb')})
            if 'rear_reflection_fraction' in nuisance:
                rear.append({'photo_id':photo,'rear_reflection_fraction':nuisance.pop('rear_reflection_fraction'),
                             'rear_interface_model':nuisance.pop('rear_interface_model')})
            if photo in shared and shared[photo] != nuisance:
                raise ValueError('Shared photo nuisance did not alias exactly')
            shared[photo] = nuisance
        groups[gid] = {'family': config['family_assignment'][gid], 'surface_binding': bindings[gid],
                       'appearance': appearances[0]['appearance'], 'appearance_alternatives': appearances,
                       'roughness_source': 'factorized_fixed_prior_not_measured_by_photo_objective',
                       'display_roughness_prior': p.roughness_values[0],
                       'rear_mode': config['rear_modes'][gid], 'nuisance_rear_by_photo': rear,
                       'observations': [_identity(r) for r in local_model.branch], 'photo_metrics': metrics,
                       'support_unique_pixels_by_photo': support,
                       'photo_policy_status': 'within_declared_policy' if group_within else 'outside_declared_policy' if group_supported else 'validation_unmeasured',
                       'ar_probes': single.candidate_ar_probe_predictions(appearances[0]['appearance'])}
        if evaluation_model is not model:
            groups[gid]['evaluation_domain'] = 'frozen_union_of_all_supplied_eligible_observation_pixels'
            groups[gid]['fitted_mask_observation_ids'] = [r['original_id'] for r in local_model.branch]
    candidate_id = single._hash({'input': input_sha, 'configuration': config, 'start': start, 'parameters': fitted['x']})[:24]
    return {'candidate_id': candidate_id, 'groups': groups, 'shared_nuisance_by_photo': [shared[k] for k in sorted(shared)],
            'assumptions': {**config, 'start': start}, 'parameter_identification': 'unmeasured',
            'photo_policy_status': 'within_declared_policy' if within else 'outside_declared_policy' if supported else 'validation_unmeasured',
            'global_photometric_gauges': {'components': model.components, 'relative_component_calibration': 'unmeasured' if len(model.components) > 1 else 'single_connected_component_not_calibrated'},
            'optimizer': {'converged': fitted['success'], 'status': fitted['status'], 'evaluations': fitted['nfev'],
                          'jacobian_mode': jacobian_mode,
                          'objective_including_priors': cost, 'parameter_count': len(x),
                          'bound_active_parameter_indices': np.flatnonzero((x-np.asarray(model.lower)<1e-5)|(np.asarray(model.upper)-x<1e-5)).tolist()},
            'parameter_blocks': model.parameter_receipt(), 'unique_training_pixels_by_photo': model.unique_training_pixels,
            'objective_weighting': 'each_unique_training_pixel_once; equal_photo_weight; shared_photo_priors_once'}


def fit_joint_photo_lens_candidates(groups: list[dict], *, family_assignments: list[dict] | None = None,
        rear_source_bindings: list[dict] | None = None, policy: JointPhotoLensFitPolicy = JointPhotoLensFitPolicy(),
        progress: Callable[[dict], None] | None = None, checkpoint_dir: Path | None = None,
        appearance_priors: dict | None = None) -> dict:
    """Explore the complete declared bounded ensemble; never select or accept it.

    Budgets are checked before any optimization or checkpoint creation. The photo
    policy's observation/sample budgets apply to all groups together; its local
    maximum_mask_branches/maximum_optimization_runs are superseded by the joint
    limits. Every completed numerical start is retained, including nonconverged
    fits. Numerical failures are explicit receipts, not silently missing starts.
    """
    if not isinstance(policy, JointPhotoLensFitPolicy) or progress is not None and not callable(progress):
        raise ValueError('Expected JointPhotoLensFitPolicy and optional callable progress')
    p = policy.photo_policy
    groups=grounded_fit_support.apply_grounded_fit_support(groups,appearance_priors)
    bindings, records, branches, aliases, split = _prepare_joint(groups, policy)
    appearance_priors = single.validate_appearance_priors(appearance_priors, records, bindings)
    if 'semantic_softbox' in p.lighting_families and appearance_priors is None:
        raise ValueError('semantic_softbox requires appearance_priors')
    if policy.jacobian_mode == 'analytic' and (appearance_priors is not None or 'semantic_softbox' in p.lighting_families):
        raise ValueError('Appearance priors and semantic_softbox currently require finite_difference Jacobian')
    p = single._prior_policy(p, appearance_priors)
    policy = replace(policy, photo_policy=p)
    assignments, same_family_prior = _assignments(family_assignments, list(bindings), p)
    rear_bindings = _rear_bindings(rear_source_bindings, records)
    evaluation_records = None
    mask_search = {'mode':'exhaustive', 'complete_mask_exploration':True, 'cartesian_branch_count':len(branches)}
    if not branches and policy.mask_search_mode == 'conditional_seed_beam':
        branches, evaluation_records, mask_search = _conditional_seed_branches(records, bindings, assignments, p,
                                                                               appearance_priors, policy)
    coverage = [{**_identity(r), 'samples': len(r['eligible']), 'eligible': int(r['eligible'].sum()),
                 'train': int(r['train'].sum()), 'validation': int(r['validation'].sum()),
                 'excluded_nonopaque': int(np.count_nonzero(r['arrays']['alpha'] != 255)),
                 'excluded_unknown_coordinates': int(np.count_nonzero(~np.isfinite(r['arrays']['v'])|~np.isfinite(r['arrays']['angle']))),
                 'excluded_back_interface': int(np.count_nonzero(r['arrays']['angle'] >= 90)),
                 'excluded_image_grounding':int(np.count_nonzero(r['arrays'].get('image_eligible',np.ones(len(r['eligible'])))==0)),
                 'numeric_sha256': r['numeric_sha256'], 'source_sha256': r['source_sha256'], 'provenance': r['provenance']} for r in records]
    input_identity = {'bindings': bindings, 'coverage': coverage, 'policy': asdict(policy),
                             'family_assignments': assignments, 'rear_source_bindings': [rear_bindings[k] for k in sorted(rear_bindings)],
                             'spatial_split': split, 'default_same_family_prior': same_family_prior}
    if appearance_priors is not None:
        input_identity['appearance_priors'] = appearance_priors
    if policy.mask_search_mode != 'exhaustive':
        input_identity['mask_search'] = mask_search
    input_sha = single._hash(input_identity)
    relations = (appearance_priors or {}).get('material_relations', [])
    material_hypotheses = [{'mode': 'independent'}]
    for size in range(1, len(relations)+1):
        for subset in itertools.combinations(relations, size):
            material_hypotheses.append({'mode': 'shared_manufactured_pair',
                'relation_ids': [r['relation_id'] for r in subset],
                'shared_group_sets': [r['group_ids'] for r in subset],
                'source_bound_hypothesis_not_verified_identity': True})
    configurations, unsupported = [], []
    for branch in branches:
        by_group = {g: [r for r in branch if r['group_id'] == g] for g in bindings}
        identity = [_identity(r) for r in branch]
        if any(n < p.minimum_training_points_per_photo for rows in by_group.values() for n in _photo_support(rows, 'train').values()):
            unsupported.append({'observations': identity, 'reason': 'insufficient_group_photo_training_support'}); continue
        modes = {}
        for gid, rows in by_group.items():
            has_rear = any(np.any(r['arrays']['rear_weight'][r['eligible']] > 0) for r in rows)
            known = all(np.isfinite(r['arrays']['rear'][r['eligible'] & (r['arrays']['rear_weight'] > 0)]).all() for r in rows)
            modes[gid] = single.rear_mode_options(rows, p)
            if has_rear and not known:
                unsupported.append({'observations': identity, 'group_id': gid, 'rear': 'geometry_conditioned_rear', 'reason': 'rear_color_unknown_at_positive_geometry_weight'})
        for lighting in p.lighting_families:
            if lighting == 'semantic_softbox' and any(r['image_size'] is None for r in branch):
                unsupported.append({'observations': identity, 'lighting': lighting, 'reason': 'explicit_source_image_size_unavailable'}); continue
            if lighting == 'smooth' and any(not np.isfinite(r['arrays']['direction'][r['eligible']]).all() for r in branch):
                unsupported.append({'observations': identity, 'lighting': lighting, 'reason': 'complete_reflected_directions_unavailable'}); continue
            for assignment, rear_tuple in itertools.product(assignments, itertools.product(*(modes[g] for g in bindings))):
                config = {'family_assignment': assignment, 'lighting': lighting, 'rear_modes': dict(zip(bindings, rear_tuple)), 'observations': identity}
                for hypothesis in material_hypotheses:
                    if any(len({assignment[g] for g in shared}) != 1 for shared in hypothesis.get('shared_group_sets', [])):
                        unsupported.append({'family_assignment': assignment, 'material_relation': hypothesis,
                                            'reason': 'shared_material_requires_same_response_family'})
                        continue
                    configured=dict(config,material_relation=hypothesis) if relations else dict(config)
                    rear_observed=any((appearance_priors or {}).get('photos',{}).get(r['photo_id'],{}).get('interface')=='rear' for r in branch)
                    rear_observed |= any((appearance_priors or {}).get('groups',{}).get(g,{}).get('rear_image_constraints')
                        and assignment[g] not in ('gradient_tint','gradient_angular_mirror') for g in bindings)
                    for response in policy.rear_response_hypotheses if rear_observed else (None,):
                        configurations.append((branch,dict(configured,rear_response_hypothesis=response) if response else configured))
    runs = len(configurations)*p.starts
    if runs > policy.maximum_optimization_runs or runs > policy.maximum_candidates:
        raise ValueError(f'Joint optimization/candidate budget exceeded: {runs}; exploration would be incomplete')
    run_plan = [(branch, config, start, single._hash({'input': input_sha, 'configuration': config, 'start': start}))
                for branch, config in configurations for start in range(p.starts)]
    request = {'schema_version': 1, 'input_sha256': input_sha, 'jacobian_mode': policy.jacobian_mode,
               'implementation': _implementation(), 'run_keys': [v[3] for v in run_plan]}
    directory, saved = _checkpoints(checkpoint_dir, request, set(request['run_keys']))
    notify = progress or (lambda event: None)
    notify({'event': 'planned', 'input_sha256': input_sha, 'jacobian_mode': policy.jacobian_mode,
            'optimization_runs': runs, 'mask_branches': len(branches), 'checkpointed_runs': len(saved)})
    candidates, failures = [], []
    models = {}
    for index, (branch, config, start, key) in enumerate(run_plan):
        config_key = single._hash(config)
        if config_key not in models:
            # Retain only this configuration; large ensembles do not retain all
            # duplicated NumPy model buffers in memory.
            models = {config_key: _JointModel(branch, config['family_assignment'], config['lighting'], config['rear_modes'], p, rear_bindings, appearance_priors,
                                               config.get('material_relation'),config.get('rear_response_hypothesis','free_rear_reflection'))}
        model = models[config_key]
        if evaluation_records is not None and not hasattr(model, 'evaluation_model'):
            evaluator = _JointModel(evaluation_records, config['family_assignment'], config['lighting'], config['rear_modes'],
                p, rear_bindings, appearance_priors, config.get('material_relation'),config.get('rear_response_hypothesis','free_rear_reflection'))
            if evaluator.blocks != model.blocks or any(not np.array_equal(evaluator.maps[g], model.maps[g]) for g in bindings):
                raise ValueError('Frozen union evaluation changed the fitted parameter binding')
            model.evaluation_model = evaluator
        notify({'event': 'start', 'run_index': index, 'run_key': key, 'total_runs': runs, 'resumed': key in saved})
        receipt = saved.get(key)
        if receipt is None:
            receipt = {'run_key': key, 'input_sha256': input_sha}
            try:
                optimizer_options = ({'jac': lambda x: derivatives.joint_residual_jacobian(model, x)}
                                     if policy.jacobian_mode == 'analytic' else {})
                fitted = least_squares(model.residual, model.initial(start), bounds=(model.lower, model.upper), max_nfev=p.max_nfev,
                                       ftol=1e-7, xtol=1e-7, gtol=1e-7, **optimizer_options)
                receipt['fit'] = {'x': fitted.x.tolist(), 'cost': float(fitted.cost), 'success': bool(fitted.success), 'status': int(fitted.status), 'nfev': int(fitted.nfev)}
                # Replay before persisting: no invalid numerical candidate enters
                # a durable successful receipt.
                _emit_candidate(model, receipt['fit'], input_sha, config, start, bindings, p, policy.jacobian_mode)
            except (ValueError, FloatingPointError, OverflowError, np.linalg.LinAlgError) as error:
                receipt.pop('fit', None)
                receipt['failure'] = type(error).__name__+': '+str(error)
            if directory is not None:
                _atomic(directory/f'{key}.json', receipt)
        if set(receipt) not in ({'run_key', 'input_sha256', 'fit'}, {'run_key', 'input_sha256', 'failure'}):
            raise ValueError('Invalid completed start receipt')
        if 'fit' in receipt:
            candidates.append(_emit_candidate(model, receipt['fit'], input_sha, config, start, bindings, p, policy.jacobian_mode))
        else:
            if not isinstance(receipt['failure'], str) or not receipt['failure']:
                raise ValueError('Invalid numerical failure receipt')
            failures.append({'configuration': config, 'start': start, 'run_key': key, 'reason': receipt['failure']})
        notify({'event': 'completed', 'run_index': index, 'run_key': key, 'completed_runs': index+1, 'total_runs': runs, 'resumed': key in saved,
                'status': 'candidate' if 'fit' in receipt else 'numerical_failure'})
    compatible = [c for c in candidates if c['photo_policy_status'] == 'within_declared_policy' and c['optimizer']['converged']]
    chosen_envelope = compatible or candidates
    envelopes = {}
    for gid in bindings:
        if not chosen_envelope:
            envelopes[gid] = None; continue
        values = np.asarray([c['groups'][gid]['ar_probes']['composed_linear_rgb'] for c in chosen_envelope])
        low, high = values.min(axis=0), values.max(axis=0)
        spread = float((high-low).max())
        envelopes[gid] = {'scope': 'explored_converged_photo_policy_candidates' if compatible else 'diagnostic_candidates_no_converged_policy_match',
                          'candidate_ids': [c['candidate_id'] for c in chosen_envelope], 'minimum_linear_rgb': low.tolist(), 'maximum_linear_rgb': high.tolist(),
                          'maximum_channel_spread': spread, 'response_status': 'explored_responses_disagree' if spread > p.probe_agreement_tolerance_linear else 'explored_responses_agree_within_policy',
                          'tolerance_linear': p.probe_agreement_tolerance_linear, 'not_a_certified_uncertainty_bound': True}
    unresolved = len(failures)+sum(not c['optimizer']['converged'] for c in candidates)
    diagnosis = ('photo_compatible_explanations_available' if compatible else 'optimization_unresolved' if unresolved else
                 'validation_unmeasured' if any(c['photo_policy_status'] == 'validation_unmeasured' for c in candidates) else
                 'model_mismatch_under_declared_policy' if candidates else 'no_supported_fit')
    if _implementation() != request['implementation']:
        raise ValueError('Implementation changed during joint fitting; result is not complete')
    result = {'schema_version': 1, 'method': 'joint_uncalibrated_photo_lens_ensemble_v1',
        'status': 'candidates_available' if candidates else 'no_supported_candidates', 'diagnosis': diagnosis,
        'parameter_identification': 'unmeasured', 'photometric_calibration': 'unmeasured', 'surface_bindings': bindings,
        'input_sha256': input_sha, 'implementation': request['implementation'], 'policy': asdict(policy), 'coverage': coverage,
        'duplicate_hypothesis_aliases': aliases, 'spatial_split': split, 'validation_parameters_frozen': True,
        'environment_bounds': single._environment_bounds(p), 'rear_source_bindings': [rear_bindings[k] for k in sorted(rear_bindings)],
        'family_assignment_prior': {'mode': 'shared_family_independent_material_parameters' if same_family_prior else 'explicit_assignments',
                                    'assignments': assignments, 'mixed_family_combinations_exhaustive': len(assignments) == len(p.families)**len(bindings)},
        'roughness_factorization': {'values_per_group': {g: list(p.roughness_values) for g in bindings},
                                   'cartesian_variants_per_candidate': len(p.roughness_values)**len(bindings),
                                   'represented_material_combinations': len(candidates)*len(p.roughness_values)**len(bindings),
                                   'photo_objective_independent_of_roughness': True, 'display_prior': 'lowest_roughness_per_group'},
        'exploration': {'complete_for_declared_configurations': True, 'mask_branches': len(branches), 'configurations': len(configurations),
                        'optimization_runs': runs, 'candidate_records': len(candidates), 'roughness_does_not_multiply_fit_runs': True,
                        'unresolved_optimizer_runs': unresolved, 'unsupported_configurations': unsupported, 'failed_runs': failures},
        'candidates': candidates, 'ar_prediction_envelopes_by_group': envelopes,
        'limitations': ['Shared per-photo illumination assumes a common effective field for these groups; near-field differences may violate it.',
            'Reflected directions within each photo must use one shared coordinate basis; unit-vector validation does not establish that caller assertion.',
            'Each connected component fixes one exposure/WB gauge, not a physical calibration; disconnected components have no inferred relative calibration.',
            'Global material/illumination ambiguity remains even when the graph is connected.',
            'Explicit rear bindings assert one shared unknown constant color only within the named photo/object; other rear nuisance stays group-specific.',
            'Source bytes and geometric correspondence remain caller-bound hypotheses; this module validates supplied claims, not their physical truth.',
            'All default starts share their material initialization pattern; they are not an exhaustive Cartesian set of per-group starts.',
            'Photo validation remains fixed spatial support, not independent photographs or AR acceptance.',
            'Finite families, bounded local optimizers and probe envelopes do not certify every admissible explanation.']}
    if appearance_priors is not None:
        result['appearance_priors'] = appearance_priors
        result['appearance_priors_sha256'] = single._hash(appearance_priors)
        result['validation_scope'] = 'conditional_on_full_photo_semantic_and_numeric_priors_not_independent_holdout'
    result['mask_search'] = mask_search
    result['exploration']['complete_mask_exploration'] = mask_search['complete_mask_exploration']
    if evaluation_records is not None:
        result['validation_domain_frozen_across_mask_candidates'] = True
        result['limitations'].extend(mask_search['limitations'])
    if relations:
        result['material_relation_hypotheses'] = material_hypotheses
        result['family_assignment_prior']['mode'] = 'shared_family_with_explicit_shared_and_independent_material_hypotheses' if same_family_prior else 'explicit_assignments_and_material_relations'
        factorization = result['roughness_factorization']
        combinations = {c['candidate_id']: len(p.roughness_values)**(len(bindings)-sum(
            len(gs)-1 for gs in c['assumptions']['material_relation'].get('shared_group_sets', []))) for c in candidates}
        factorization['cartesian_variants_per_candidate'] = None
        factorization['variants_by_candidate_id'] = combinations
        factorization['represented_material_combinations'] = sum(combinations.values())
        factorization['shared_material_roughness_is_paired'] = True
        result['limitations'].append('Manufactured-pair sharing is an explicit hypothesis; independent materials remain a contrary candidate on the identical sample branch.')
    return single._json(result, 'joint result')
