"""Photo samples from the actual exported effective-group geometry.

This experimental adapter preserves the existing mask/sampling/color policy.
Its geometry contract counts one nearest event per supplied optical group and
uses symmetric normals. Closed surfaces in one group do not become multiple
optical layers. Distinct groups on one ray are still unsupported by the current
single-event photographic fitter and remain explicit unknown observations.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .mesh import TriangleMesh
from .optical_group_raster import (
    PROFILE, rasterize_optical_groups, sample_single_group_fields,
)
from .photo_lens_observations import _read_pinned, _sample_bound_photo_observations


METHOD = 'exported_effective_group_symmetric_photo_observations_v1'


def project_effective_group_fields(mesh, uv, normals, face_groups, camera, shape, normalization, *, view_scene=None):
    """Keep original photographic normalization and actual exported attributes."""
    center = np.asarray(normalization['center'], dtype=float)
    extent = normalization['extent']
    if (center.shape != (3,) or not np.isfinite(center).all()
            or isinstance(extent, bool) or not np.isscalar(extent)
            or not np.isfinite(extent) or extent <= 0):
        raise ValueError('Invalid original camera normalization')
    articulation = None
    if view_scene is not None:
        from .view_scene import pose_mesh_from_contract
        posed = pose_mesh_from_contract(mesh, view_scene['contract'], view_scene['view_id'],
            photo_sha256=view_scene.get('source_image_sha256'), uv=uv, normals=normals)
        mesh, uv, normals, articulation = posed['mesh'], posed['uv'], posed['normals'], posed['report']
    scene = TriangleMesh((mesh.vertices-center)/extent, mesh.faces, mesh.parts)
    events = rasterize_optical_groups(scene, face_groups, camera, shape)
    fields = sample_single_group_fields(scene, uv, normals, events, camera)
    fields.update(
        visible=events.count > 0,
        depth_separation=events.report['depth_separation'],
        depth_units=events.report['depth_units'],
        mesh_length_units='original_camera_normalized_candidate_coordinates',
        profile_diagnostics={
            'profile': PROFILE,
            'events': events.report,
            'normal_response': fields['normal_contract'],
            'single_group_fit_only': True,
            'same_group_coplanar_check': 'exported_asset_receipt_required',
            'view_dependent_depth_ties': 'retained_as_unknown',
            'articulation': articulation,
        },
    )
    return fields


def _exclusions(value):
    """Caller-declared (group, photo) pairs the fit must not sample, with reasons."""
    if value is None:
        return {}
    if not isinstance(value, list):
        raise ValueError('group_photo_exclusions must be a list of {group_id, photo_id, reason}')
    result = {}
    for row in value:
        if (not isinstance(row, dict) or set(row) != {'group_id', 'photo_id', 'reason'}
                or any(not isinstance(row[k], str) or not row[k] for k in row)):
            raise ValueError('group_photo_exclusions must be a list of {group_id, photo_id, reason}')
        key = (row['group_id'], row['photo_id'])
        if key in result:
            raise ValueError('Duplicate group/photo exclusion')
        result[key] = row['reason']
    return result


def build_optical_group_observations(prepared_model: Path, export_report: Path, region_report: Path,
                                     *, maximum_samples_per_hypothesis=256, group_photo_exclusions=None):
    """Return exported-group fitter inputs, preserving every supplied mask.

    A source part may belong to one supplied group; a group can contain several
    complete source parts. This adapter does not split or infer group identity.
    Original camera and region priors remain bound to the source asset, while
    fitted optical coordinates bind to the separate exported candidate's bytes.
    """
    # Keep the dependency profile-specific: the front-sheet sampler never imports
    # or silently accepts this experimental asset profile.
    from .optical_group_asset import read_optical_group_candidate

    if type(maximum_samples_per_hypothesis) is not int or not 16 <= maximum_samples_per_hypothesis <= 4096:
        raise ValueError('Sample capacity must be 16..4096')
    prepared_model, export_report, region_report = (
        Path(p).resolve() for p in (prepared_model, export_report, region_report))
    pins = {}
    _read_pinned(prepared_model, pins)
    export = json.loads(_read_pinned(export_report, pins))
    regions = json.loads(_read_pinned(region_report, pins))
    if export['source_sha256'] != regions['candidate_sha256']:
        raise ValueError('Photo regions and prepared source refer to different candidates')
    bound = read_optical_group_candidate(prepared_model, export,
                                        expected_sha256=pins[str(prepared_model)])
    source_to_group, identities, suffixes, groups = {}, {}, {}, {}
    for index, group in bound['groups'].items():
        for part in group['source_part_indices']:
            if part in source_to_group:
                raise ValueError('A source part belongs to several optical groups')
            source_to_group[part] = index
        identities[index] = {'optical_group_id': group['group_id'],
                             'source_part_indices': group['source_part_indices']}
        suffixes[index] = f"group-{group['group_id']}"
        groups[index] = {'surface_binding': {**group['surface_binding'], 'coordinate_method': METHOD},
                         'observations': []}

    unbound_priors = []

    def resolve_groups(parts):
        if (not isinstance(parts, list) or not parts
                or any(type(i) is not int or i < 0 for i in parts) or len(parts) != len(set(parts))):
            raise ValueError('Region optical prior requires unique nonnegative source parts')
        # A face partition leaves undeclared lens-material pieces (pads, fragments,
        # remainders) beside the declared groups. A prior touching such a piece
        # is retained with that piece recorded as unbound, not turned into an
        # error and not silently promoted to a group.
        unbound = [part for part in parts if part not in source_to_group]
        if unbound:
            unbound_priors.append({'prior_part_indices': list(parts), 'unbound_part_indices': unbound})
        return sorted({source_to_group[part] for part in parts if part in source_to_group})

    report = {
        'schema_version': 1, 'method': METHOD, 'optical_profile': PROFILE,
        'quality_verdict': 'unmeasured', 'accepted': False,
        'maximum_samples_per_hypothesis': maximum_samples_per_hypothesis, 'photos': [],
        'limitations': [
            'Source-part groups, masks, cameras and photo articulation remain unverified hypotheses.',
            'The response is symmetric and effective; no front/back coating or volume optics is recovered.',
            'Only one distinct optical group before an opaque stop is supported by this photo fitter.',
            'Cross-group depth ties, invalid normals and layer overflow remain unknown and counted.',
            'Native photo centers use nearest working-grid geometry, without raster-edge equivalence to a GPU.',
            'Image-border median is an uncalibrated backdrop; rear-frame color remains unknown.',
            'Unselected source geometry supplies opaque proxy stops; alpha, textures and frame transparency are not simulated.',
            'Exact coincident-patch checks do not certify view-dependent GPU depth ties.',
            'Fitter-ready samples do not establish material identity, production compatibility or AR fidelity.',
        ],
    }
    result = _sample_bound_photo_observations(
        bound['mesh'], bound['uv'], bound['normals'], bound['face_groups'], groups,
        regions=regions, region_report=region_report, export_report=export_report,
        source_sha256=export['source_sha256'], pins=pins, report=report,
        maximum_samples_per_hypothesis=maximum_samples_per_hypothesis,
        method=METHOD, project_fields=project_effective_group_fields,
        region_group_ids=resolve_groups, group_identities=identities,
        observation_suffixes=suffixes,
    )
    excluded = _exclusions(group_photo_exclusions)
    dropped = []
    for group in result['groups'].values():
        gid = group['surface_binding']['material_group_id']
        kept = []
        for observation in group['observations']:
            if (gid, observation['photo_id']) in excluded:
                dropped.append({'group_id': gid, 'photo_id': observation['photo_id'], 'observation_id': observation['id'],
                                'reason': excluded[(gid, observation['photo_id'])]})
            else:
                kept.append(observation)
        group['observations'] = kept
    result['report']['group_photo_exclusions'] = [{'group_id': g, 'photo_id': p, 'reason': r} for (g, p), r in sorted(excluded.items())]
    result['report']['registration_excluded_observations'] = dropped
    for row in result['report']['groups']:
        row['observations'] = sum(1 for g in result['groups'].values() if g['surface_binding']['material_group_id'] == row['optical_group_id'] for _ in g['observations'])
    result['report']['unbound_region_prior_parts'] = unbound_priors
    result['report']['unbound_prior_policy'] = ('source parts outside every declared group contribute no observations; '
                                                'their priors are recorded, never promoted to a group')
    return result
