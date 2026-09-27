"""Prepare bounded optical-role alternatives without silently choosing a branch.

The role report supplies product-independent hypotheses. Existing reduction is
verified against the retained source and reused for the primary. Alternatives
receive the same LOD budgets and strict optical preparation on fresh outputs.
Neutral renders test the consequences of a role assignment, not lens identity.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

import numpy as np
from scipy.spatial import cKDTree

from .mesh import load_glb_bytes
from .segmented_providers import immutable, pin, verified
from qa.provider_benchmark import canonical, digest, read_json, write_json


def _load(value):
    return deepcopy(value) if isinstance(value, dict) else read_json(Path(value))


def validated_hypotheses(report, maximum_alternatives=3):
    """Reject invented memberships, duplicate bindings and seed removals."""
    if type(maximum_alternatives) is not int or not 0 <= maximum_alternatives <= 7:
        raise ValueError('At most seven explicit role alternatives can be processed')
    count = len(report['parts'])
    def check(groups):
        if not isinstance(groups, list) or not groups or any(not isinstance(g, list) or not g for g in groups):
            raise ValueError('Role hypothesis requires nonempty groups')
        members = [i for group in groups for i in group]
        if len(members) != len(set(members)) or any(type(i) is not int or not 0 <= i < count for i in members):
            raise ValueError('Invalid or duplicate role member')
        return set(members)
    primary = report['primary_groups']
    seeds = check(primary)
    permitted = {(f['part_index'], f['seed_part_index']) for f in report['fragment_evidence'] if f['candidate']}
    hypotheses = report['hypotheses']
    if not hypotheses or hypotheses[0]['id'] != 'primary' or hypotheses[0]['groups'] != primary:
        raise ValueError('Role report has no matching primary hypothesis')
    names = set()
    for hypothesis in hypotheses:
        name = hypothesis['id']
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', name) or name in names:
            raise ValueError('Role hypothesis IDs must be unique safe identifiers')
        names.add(name)
        members = check(hypothesis['groups'])
        if not seeds <= members:
            raise ValueError('Role alternative removes a supported seed')
        for seed_group in primary:
            containing = [g for g in hypothesis['groups'] if set(seed_group) <= set(g)]
            if len(containing) != 1 or set(containing[0]) & seeds != set(seed_group):
                raise ValueError('Role alternative changes established seed grouping')
        for fragment in members-seeds:
            group = next(g for g in hypothesis['groups'] if fragment in g)
            if not any((fragment, seed) in permitted for seed in group):
                raise ValueError('Role alternative lacks recorded fragment-to-seed evidence')
    return hypotheses[:maximum_alternatives+1], hypotheses[maximum_alternatives+1:]


def verify_reduced_part_lineage(reduced_model, report, reduction_proof):
    """Verify receipts and actual part-specific vertex membership after LOD.

    LOD changes triangle indices, so original face-ID rasters are not reused.
    Compaction may renumber nodes/meshes; its maps must match actual instances.
    """
    source_path = verified(dict(path=report['source_path'], sha256=report['source_sha256']))
    reduced_path = Path(reduced_model).resolve()
    reduced_pin = pin(reduced_path)
    proof = _load(reduction_proof)
    if proof['model']['sha256'] != reduced_pin['sha256']:
        raise ValueError('Reduced model differs from reduction proof')
    lod, compact = proof['lod'], proof['compact']
    if (lod['source_sha256'] != report['source_sha256'] or
            compact['source_sha256'] != lod['candidate_sha256'] or
            compact['output_sha256'] != reduced_pin['sha256'] or
            compact.get('active_semantics_identical') is not True or
            lod.get('vertices_exact') is not True or lod.get('material_uv_bindings_exact') is not True):
        raise ValueError('Reduction/compaction lineage is not verified')
    source = load_glb_bytes(source_path.read_bytes())
    reduced = load_glb_bytes(reduced_path.read_bytes())
    if len(source.parts) != len(reduced.parts) or len(report['parts']) != len(source.parts):
        raise ValueError('Part count changed across role binding')
    maximum = 0.
    extent = float(np.ptp(source.vertices, axis=0).max())
    for index, (before, after) in enumerate(zip(source.parts, reduced.parts)):
        evidence = report['parts'][index]
        if evidence['part_index'] != index or evidence['source_binding'] != {
                key:before[key] for key in ('node_index', 'mesh_index', 'primitive_index')}:
            raise ValueError('Role part binding differs from retained source')
        for key, mapping in [('node_index', 'nodes'), ('mesh_index', 'meshes')]:
            if compact['index_maps'][mapping].get(str(before[key])) != after[key]:
                raise ValueError('Compacted part binding differs from verified mapping')
        if before['primitive_index'] != after['primitive_index']:
            raise ValueError('Primitive order changed across compaction')
        old = source.vertices[before['vertex_start']:before['vertex_start']+before['vertex_count']]
        new = reduced.vertices[after['vertex_start']:after['vertex_start']+after['vertex_count']]
        distances, _ = cKDTree(old).query(new, workers=1)
        maximum = max(maximum, float(distances.max(initial=0)))
        if maximum > max(extent*1e-12, 1e-14):
            raise ValueError('Reduced part vertices are not retained source-part vertices')
    return dict(source=pin(source_path), reduced=reduced_pin, part_count=len(source.parts),
                maximum_vertex_membership_error=maximum, old_face_ids_reused=False,
                reduction_proof_sha256=digest(canonical(proof)))


def _build_variant(source_model, existing_reduced, hypothesis, folder, settings):
    from qa.part_lod_probe import run as reduce_parts
    from qa.part_optics_probe import run as prepare_optics, standard_control
    from .compact_glb import run_compact_asset
    from .optical_group_asset import read_optical_group_candidate
    indices = sorted({i for group in hypothesis['groups'] for i in group})
    if hypothesis['id'] == 'primary':
        reduced = Path(existing_reduced)
        reduction = dict(reused_existing_verified_reduction=True)
    else:
        reduction = reduce_parts(source_model, folder/'lod', indices, settings['lens_triangles'],
                                 settings['frame_triangles'], settings['simplification_error'])
        compact = run_compact_asset(folder/'lod/candidate.glb', folder/'raw-compact')
        reduced = Path(compact['model']['path'])
    prepared = prepare_optics(reduced, folder/'prepared', hypothesis['groups'],
                             yaw_degrees=settings['yaw_degrees'], width_m=settings['display_width_mm']/1000)
    if prepared['preparation_status'] != 'prepared_optical_group_candidate':
        return dict(id=hypothesis['id'], groups=hypothesis['groups'], status='optical_preparation_rejected',
                    preparation=prepared, accepted=False)
    compact = run_compact_asset(prepared['candidate'], folder/'compact', folder/'prepared/optics/export.json')
    model = pin(folder/'compact/compact.glb')
    receipt = pin(folder/'compact/compact.export.json')
    read_optical_group_candidate(verified(model), read_json(verified(receipt)), expected_sha256=model['sha256'])
    triangles = len(load_glb_bytes(verified(model).read_bytes()).faces)
    violations = []
    if triangles > settings['maximum_triangles']:
        violations.append('triangle_budget_exceeded')
    if model['bytes'] > settings['maximum_bytes']:
        violations.append('byte_budget_exceeded')
    # Same existing geometry in these standard controls isolates the material
    # consequence from the separately budgeted alternative's LOD differences.
    standard = standard_control(existing_reduced, folder/'standard-control', hypothesis['groups'])
    return dict(id=hypothesis['id'], groups=hypothesis['groups'], added_fragments=hypothesis['added_fragments'],
                status='within_delivery_budgets' if not violations else 'delivery_budget_review',
                model=model, export=receipt, preparation_report=pin(folder/'prepared/optics/report.json'),
                standard_control=pin(standard['candidate']), triangles=triangles, violations=violations,
                reduction=reduction, accepted=False)


def process_role_alternatives(reduced_model, role_report, output, *, reduction_proof,
                              lens_triangles=20000, frame_triangles=100000, simplification_error=.002,
                              yaw_degrees=-90., display_width_mm=145., maximum_triangles=150000,
                              maximum_bytes=15000000, maximum_alternatives=3):
    """Prepare reviewable branches; unresolved alternatives prohibit silent use.

    `reduction_proof` is the existing reduced-stage result `{lod,compact,model}`.
    The returned `role_selection_required` is true whenever the input contains
    alternatives, including truncated or rejected ones. This stage never awards
    semantic acceptance or selects a branch from preparation success alone.
    """
    report = _load(role_report)
    selected, deferred = validated_hypotheses(report, maximum_alternatives)
    settings = dict(lens_triangles=lens_triangles, frame_triangles=frame_triangles,
        simplification_error=simplification_error, yaw_degrees=yaw_degrees, display_width_mm=display_width_mm,
        maximum_triangles=maximum_triangles, maximum_bytes=maximum_bytes, maximum_alternatives=maximum_alternatives)
    if (any(type(settings[k]) is not int or settings[k] <= 0 for k in
            ('lens_triangles', 'frame_triangles', 'maximum_triangles', 'maximum_bytes')) or
            not 0 < simplification_error <= .02 or not 60 <= display_width_mm <= 250 or not np.isfinite(yaw_degrees)):
        raise ValueError('Invalid role-alternative delivery settings')
    output = Path(output).resolve()
    lineage = verify_reduced_part_lineage(reduced_model, report, reduction_proof)
    request = dict(schema_version=1, role_report_sha256=digest(canonical(report)), lineage=lineage,
                   settings=settings, hypotheses=selected,
                   implementation_sha256=pin(Path(__file__))['sha256'])
    immutable(output/'request.json', request)
    if (output/'completion.json').exists():
        completion = read_json(output/'completion.json')
        if completion['request_sha256'] != digest(canonical(request)):
            raise ValueError('Role-alternative completion belongs to another request')
        for item in completion['artifacts']:
            verified(item)
        return read_json(verified(completion['report']))
    variants = []
    for hypothesis in selected:
        folder = output/hypothesis['id']
        # A caller journal owns interrupted attempts. Never overwrite partial
        # LOD/optical outputs with another branch or weaken preparation guards.
        if folder.exists() and any(folder.iterdir()):
            raise ValueError('Partial alternative output requires a fresh journal attempt')
        try:
            variant = _build_variant(lineage['source']['path'], reduced_model, hypothesis, folder, settings)
        except ValueError as error:
            variant = dict(id=hypothesis['id'], groups=hypothesis['groups'], status='variant_rejected',
                           reason=str(error), accepted=False)
        variants.append(variant)
    runtime = dict(cases=[dict(id=v['id'], product='role-review', provider=v['id'], path=v['model']['path'],
                                model_sha256=v['model']['sha256'], rotation_degrees=[0,0,0], width_mm=display_width_mm)
                         for v in variants if v.get('model')],
                   input_description='Prepared neutral optical alternatives for unresolved boundary assignment. Geometry is reduced under the same budgets; these controls do not recover product appearance.',
                   environments=[dict(id='room', preset='room', intensity=.8), dict(id='broad', preset='broad_studio', intensity=.8)],
                   ar_views=[dict(id='front', yaw_degrees=0), dict(id='angled', yaw_degrees=35), dict(id='back', type='asset-back')],
                   background_fixture='checker')
    standard = dict(cases=[dict(id=v['id'], product='role-review', provider=v['id'], path=v['standard_control']['path'],
                               model_sha256=v['standard_control']['sha256'], rotation_degrees=[0,yaw_degrees,0])
                         for v in variants if v.get('standard_control')],
                    views=['front','angled','angled-opposite','back'], modes=['raw'], backgrounds=['checker'], width=800,height=600)
    write_json(output/'runtime-manifest.json', runtime)
    write_json(output/'standard-manifest.json', standard)
    unresolved = len(report['hypotheses']) > 1
    result = dict(schema_version=1, status='role_selection_required' if unresolved else 'single_role_hypothesis',
        role_selection_required=unresolved, selected_hypothesis=None, primary_may_ship=not unresolved,
        semantic_acceptance=False, requires_review=True, lineage=lineage, variants=variants,
        deferred_hypothesis_ids=[h['id'] for h in deferred],
        review_reasons=['opaque_vs_optical_boundary_assignment_unresolved'] if unresolved else ['semantic_identity_unverified'],
        evidence_limit='Preparation success, smooth adjacency and mask proximity cannot distinguish a lens bevel from adjacent frame hardware.',
        runtime_manifest=pin(output/'runtime-manifest.json'), standard_manifest=pin(output/'standard-manifest.json'))
    write_json(output/'report.json', result)
    artifacts = [result['runtime_manifest'], result['standard_manifest']]
    for variant in variants:
        artifacts.extend(variant[k] for k in ('model','export','preparation_report','standard_control') if k in variant)
    immutable(output/'completion.json', dict(request_sha256=digest(canonical(request)),
              report=pin(output/'report.json'), artifacts=artifacts))
    return result
