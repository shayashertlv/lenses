"""Bind grouped preparation, actual exported attributes and photographic samples.

This adapter reuses the existing fitter. Group identity, masks and material
appearance remain hypotheses; no successful load or preview grants acceptance.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np

from .deform_glb import _read_bytes
from .mesh import load_glb_bytes
from .optical_group_asset import _prepared, _source_matrices
from .optical_group_observations import build_optical_group_observations
from .optical_group_raster import PROFILE
from .photo_lens_observations import _child, _read_pinned
from .prepare_optical_groups import _ordinary_path, _parse, validate_group_declarations


def _artifact(folder, reference, pins):
    if (not isinstance(reference, dict) or not isinstance(reference.get('path'), str)
            or not isinstance(reference.get('sha256'), str)):
        raise ValueError('Grouped preparation requires pinned artifact references')
    path = _child(folder, reference['path'])
    return path, _read_pinned(path, pins, reference['sha256'])


def _merge_pins(pins, additions):
    for path, digest in additions.items():
        if path in pins and pins[path] != digest:
            raise ValueError('Grouped observation dependency changed while loading inputs')
        pins[path] = digest


def verify_group_source_originals(inputs):
    """Optional originals are checked when present; snapshots define recovery."""
    for name, digest in inputs.get('optional_original_sha256', {}).items():
        path = _ordinary_path(name)
        if path.exists():
            _read_pinned(path, {}, digest)


def load_group_optical_fit_inputs(preparation_report, region_report, *, preparation_bytes,
                                 maximum_samples_per_hypothesis=256, group_photo_exclusions=None):
    preparation_report, region_report = (Path(p).resolve() for p in (preparation_report, region_report))
    preparation = json.loads(preparation_bytes)
    if (preparation.get('schema_version') != 1 or preparation.get('optical_profile') != PROFILE
            or preparation.get('status') != 'prepared_optical_group_candidate'
            or preparation.get('accepted') is not False):
        raise ValueError('Grouped fitting requires a complete experimental group preparation')
    folder = preparation_report.parent
    pins = {str(preparation_report): hashlib.sha256(preparation_bytes).hexdigest()}
    _read_pinned(region_report, pins)
    source, source_bytes = _artifact(folder, preparation['source_snapshot'], pins)
    if pins[str(source)] != preparation['source_sha256']:
        raise ValueError('Grouped source snapshot differs from its preparation binding')
    original = _ordinary_path(preparation['source'])
    optional_originals = {str(original): preparation['source_sha256']}
    verify_group_source_originals({'optional_original_sha256': optional_originals})
    model, _ = _artifact(folder, preparation['model'], pins)
    export_path, export_bytes = _artifact(folder, preparation['export'], pins)
    export = json.loads(export_bytes)
    if (export.get('profile') != PROFILE or export.get('source_sha256') != preparation['source_sha256']
            or export.get('output_sha256') != pins[str(model)]):
        raise ValueError('Grouped preparation source/model lineage differs from its export')
    grouping_mode = preparation.get('grouping_mode')
    if grouping_mode not in ('explicit_declarations', 'source_part_hypotheses'):
        raise ValueError('Unknown grouped preparation membership mode')
    declared = None
    if preparation.get('declarations') is not None:
        _, raw = _artifact(folder, preparation['declarations'], pins)
        declared = validate_group_declarations(_parse(raw))
        declaration_original = preparation['declarations'].get('original_path')
        if declaration_original is not None:
            optional_originals[str(_ordinary_path(declaration_original))] = preparation['declarations']['sha256']
    if (declared is None) != (grouping_mode == 'source_part_hypotheses'):
        raise ValueError('Grouped preparation membership mode differs from saved declarations')
    # A rehashed outer report cannot give contradictory declarations or an
    # ignored grouping inventory a valid interpretation beside the bound GLB.
    inventory_declaration = validate_group_declarations({'schema_version': 1,
        'source_sha256': preparation['source_sha256'], 'coordinate_frame': preparation['coordinate_frame'],
        'provenance': {'method': 'check_saved_grouping_inventory'}, 'groups': preparation['grouping_inventory']})
    if declared is not None and any(declared[key] != inventory_declaration[key]
                                   for key in ('source_sha256', 'coordinate_frame', 'groups')):
        raise ValueError('Saved declarations differ from grouped preparation inventory')
    declared_groups = {group['group_id']: group for group in inventory_declaration['groups']}
    bindings = {row['group_id']: row for row in export['groups']}
    if len(bindings) != len(export['groups']):
        raise ValueError('Duplicate exported group identity')
    prepared_groups = {}
    for group in preparation['groups']:
        gid = group['group_id']
        if gid not in bindings or gid in prepared_groups:
            raise ValueError('Prepared group identity differs from exported group inventory')
        _, raw = _artifact(folder, group['prepared_report'], pins)
        report = json.loads(raw)
        if report.get('group_id') != gid or report.get('group_sha256') != bindings[gid]['prepared_group_sha256']:
            raise ValueError('Prepared group record differs from its exported binding')
        primitives = []
        for member in group['primitives']:
            _, raw = _artifact(folder, member, pins)
            with np.load(io.BytesIO(raw), allow_pickle=False) as archive:
                if set(archive.files) != {'positions', 'normals', 'uv', 'indices'}:
                    raise ValueError('Prepared group arrays contain missing or unexpected attributes')
                arrays = {key: archive[key].copy() for key in archive.files}
            primitives.append({'id': member['id'], **arrays})
        prepared_groups[gid] = {'report': report, 'primitives': primitives}
    if set(prepared_groups) != set(bindings):
        raise ValueError('Preparation does not represent every exported optical group')
    _, document, _ = _read_bytes(source_bytes)
    normalized, _ = _prepared([
        {'prepared': prepared_groups[gid], 'appearance': bindings[gid]['appearance']}
        for gid in sorted(prepared_groups)], load_glb_bytes(source_bytes), preparation['source_sha256'],
        _source_matrices(document), document)
    inventory = []
    for group in normalized:
        binding = bindings[group['group_id']]
        if any(group[key] != binding[key] for key in
               ('coordinate_frame', 'identity', 'prepared_group_sha256', 'appearance_sha256')):
            raise ValueError('Prepared group provenance differs from its fitted GLB')
        specification = {'group_id': group['group_id'], 'members': [
            {'id': member['id'], 'source_part_index': member['source_part_index'],
             'source_binding': {key: member['source_binding'][key] for key in ('node_index', 'mesh_index', 'primitive_index')}}
            for member in group['members']]}
        declared_group = declared_groups.get(group['group_id'])
        # Export normalization sorts members; declaration order is provenance,
        # not physical identity. Its original ordering remains bound separately.
        same_members = (declared_group is not None and
                        {m['id']: m for m in declared_group['members']} == {m['id']: m for m in specification['members']})
        expected_identity = {'status': 'unverified', 'provenance': {'method': grouping_mode,
            'declaration_provenance': declared['provenance'] if declared is not None else None,
            'source_sha256': preparation['source_sha256'], 'members': declared_group['members'] if declared_group else []}}
        if (not same_members or group['identity'] != expected_identity
                or group['coordinate_frame'] != preparation['coordinate_frame']):
            raise ValueError('Prepared group identity differs from saved membership declarations')
        members = {member['id']: member for member in binding['members']}
        if len(members) != len(binding['members']) or set(members) != {m['id'] for m in group['members']}:
            raise ValueError('Prepared group member inventory differs from its fitted GLB')
        for member in group['members']:
            bound = members[member['id']]
            if any(member[key] != bound[key] for key in
                   ('source_part_index', 'source_binding', 'source_to_common_matrix', 'source_attribute_sha256')):
                raise ValueError('Prepared member source binding differs from its fitted GLB')
            for name, array in member['arrays'].items():
                if hashlib.sha256(array.tobytes()).hexdigest() != bound['attribute_sha256'][name]:
                    raise ValueError(f'Prepared {name} arrays differ from actual fitted group GLB')
        inventory.append({'group_id': group['group_id'],
            'prepared_group_sha256': group['prepared_group_sha256'],
            'members': [{'id': m['id'], 'source_part_index': m['source_part_index'],
                         'attribute_sha256': m['attribute_sha256']} for m in binding['members']]})
    if set(declared_groups) != set(prepared_groups):
        raise ValueError('Saved grouping inventory differs from the complete exported group inventory')
    # The observer's authoritative reader verifies the embedded declaration,
    # actual float32 attributes, profile and current runtime geometry contract.
    observations = build_optical_group_observations(model, export_path, region_report,
        maximum_samples_per_hypothesis=maximum_samples_per_hypothesis, group_photo_exclusions=group_photo_exclusions)
    _merge_pins(pins, observations['report']['input_sha256'])
    if {g['surface_binding']['material_group_id'] for g in observations['groups'].values()} != set(prepared_groups):
        raise ValueError('Photo observations changed the complete group inventory')
    for path, digest in tuple(pins.items()):
        _read_pinned(Path(path), pins, digest)
    verify_group_source_originals({'optional_original_sha256': optional_originals})
    return {'profile': PROFILE, 'preparation': preparation, 'source': source, 'model': model,
            'prepared_surfaces': {}, 'prepared_groups': prepared_groups,
            'group_inventory': inventory, 'export_receipt': export,
            'observations': observations, 'pins': pins, 'optional_original_sha256': optional_originals}


def verify_group_preview_geometry(neutral, preview):
    """A material preview must retain the exact geometry used to fit it."""
    def inventory(receipt):
        return {group['group_id']: {member['id']: {
            key: member[key] for key in ('source_part_index', 'source_binding', 'attribute_sha256')}
            for member in group['members']} for group in receipt['groups']}
    if inventory(neutral) != inventory(preview):
        raise ValueError('Joint preview changed the geometry or membership used for fitting')
