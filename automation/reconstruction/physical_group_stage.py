"""Job stage: photo apertures, component projection, group hypotheses, bridges.

For one retained geometry candidate with fitted cameras, this stage inventories
exact components, proposes image-only lens apertures per photo, projects every
component under each camera, infers competing physical-group partitions at the
fixed tolerance ladder, composes photographed rays per hypothesis, ranks the
hypotheses by their composition, and runs the partition/preparation bridge and
a verified camera transfer for each distinct consensus declaration. Ranking is
an explicit composition score, not a verified identity; every hypothesis, its
numbers and its failures stay in the report.
"""
from __future__ import annotations

from dataclasses import asdict
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np
from PIL import Image

from .camera import Camera
from .candidate_cameras import transfer_cameras_to_partition
from .component_projection import project_components
from .interior_contact import cut_continuity, interior_contact_faces
from .component_scene import load_component_scene
from .mesh import TriangleMesh
from .partition_optical_groups import declarations_for_partition
from .partition_stage import run_component_inventory, run_face_partition
from .physical_groups import PhysicalGroupLimits, PhysicalGroupPolicy, infer_physical_groups, partition_declarations_for_hypothesis
from .prepare_optical_groups import run_optical_group_preparation
from .ray_composition import CODES, compose_view_rays


METHOD = 'physical_group_inference_stage_v1'
RANKING = 'composition_rank_v4'
CONTAMINATION_BAND = .02
CLASS_COLORS = {'background': (245, 245, 245), 'opaque_only': (120, 120, 120), 'frame_in_front': (200, 60, 40),
                'lens_over_background': (40, 170, 90), 'lens_over_opaque': (230, 160, 30),
                'lens_over_unresolved': (150, 90, 200), 'stacked_optical': (30, 90, 220),
                'unresolved_in_front': (220, 80, 190), 'unresolved_only': (180, 150, 210), 'depth_tie': (0, 0, 0)}
COORDINATE_FRAME = {'id': 'source-world', 'units': 'unitless', 'up_axis': '+Y', 'forward_axis': '+Z',
                    'provenance': {'method': 'unchanged source world coordinates; physical scale/front orientation unverified'}}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(value):
    return _sha(json.dumps(value, sort_keys=True, allow_nan=False).encode())


def _write(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return {'path': path.name, 'sha256': _sha(path.read_bytes())}


def _working_indices(native, working):
    return np.minimum(native-1, np.floor((np.arange(working)+.5)*native/working).astype(np.int64))


def _view_for(refinement, photo, model_hash):
    views = [v for v in refinement.get('views', []) if v.get('view_id') == photo['id']]
    if len(views) != 1 or 'camera_fit' not in views[0]:
        return None
    view = views[0]
    if view.get('source_sha256') != photo['sha256']:
        raise ValueError('Camera is bound to a different photograph')
    original = refinement.get('source_sha256') == model_hash
    exported = (refinement.get('status') == 'proposal_exported' and refinement.get('rerender_nonregression') is True
                and refinement.get('export', {}).get('output_sha256') == model_hash)
    if not (original or exported):
        raise ValueError('Camera report is bound to a different candidate model')
    return view


def composition_rank_key(table, index, declared_count=None):
    """Explicit rank: contamination in the best-measured view first, then fittable coverage.

    Contamination is the share of the optical groups' known footprint that the
    photo proposal calls non-lens: those pixels would be sampled as lens and
    corrupt the fit, so a hypothesis that absorbs frame geometry loses to one
    that keeps it out even when the absorption turns excluded rays into
    apparently usable ones. It is judged in the view where the groups have the
    most known pixels (the best-measured one): a part that is clean there and
    contaminated in a foreshortened view is registered badly in that view, not
    a frame piece, whereas absorbed frame geometry is outside the aperture in
    every view that sees it. Within one band the usable share (clean
    transmission plus rear content) decides, then the worst view's band, then
    fewer declared groups (two readings that compose identically differ only in
    how many materials they would fit: one body the provider split cut in two
    is one material), then the hypothesis index. Excluded rays only reduce
    coverage.
    """
    groups = 10**6 if declared_count is None else int(declared_count)
    if not table:
        return (10**6, 1., 10**6, 10**6, index), {'usable_min': None, 'contamination_max': None, 'contamination_primary': None,
                                                  'primary_view': None, 'contamination_band': CONTAMINATION_BAND}
    by_view = {}
    for row in table:
        record = by_view.setdefault(row['view_id'], {'contamination': 0., 'pixels': 0})
        record['contamination'] = max(record['contamination'], row.get('contamination_fraction') or 0.)
        record['pixels'] = max(record['pixels'], (row.get('optical_inside_aperture_known_pixels') or 0) + (row.get('optical_outside_aperture_known_pixels') or 0))
    primary_view = max(sorted(by_view), key=lambda v: by_view[v]['pixels'])
    primary = by_view[primary_view]['contamination']
    worst_contamination = max(r['contamination'] for r in by_view.values())
    least_usable = min((row['clean_transmission_fraction'] or 0)+(row['rear_content_fraction'] or 0) for row in table)
    primary_band = int(np.floor(primary/CONTAMINATION_BAND))
    worst_band = int(np.floor(worst_contamination/CONTAMINATION_BAND))
    return (primary_band, -least_usable, worst_band, groups, index), {
        'usable_min': least_usable, 'contamination_max': worst_contamination, 'contamination_primary': primary,
        'primary_view': primary_view, 'contamination_band_index': primary_band, 'worst_contamination_band_index': worst_band,
        'contamination_band': CONTAMINATION_BAND, 'declared_groups': groups,
        'rule': 'lower contamination band in the most-visible view first, then higher least-usable share, then the worst view band, then fewer declared groups, then hypothesis index'}


def run_physical_group_stage(model: Path, refinement: dict, photos: list[dict], output: Path, *,
                             aperture_engine, policy=PhysicalGroupPolicy(), limits=PhysicalGroupLimits(),
                             maximum_bridges=8) -> dict:
    """Return the stage report; artifacts live under ``output``.

    ``photos`` are ``{'id', 'path', 'sha256'}`` normalized RGB(A) images bound
    to the refinement's views. ``aperture_engine`` supplies ``describe()`` and
    ``propose(rgb)``. The output directory must be new or empty.
    """
    model, output = Path(model).resolve(), Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Physical group stage output must be new or empty')
    if type(maximum_bridges) is not int or maximum_bridges < 1:
        raise ValueError('maximum_bridges must be a positive integer')
    started = time.perf_counter()
    from .refine_photos import implementation_manifest
    implementation = implementation_manifest()
    raw = model.read_bytes(); model_hash = _sha(raw)
    output.mkdir(parents=True, exist_ok=True)
    inventory = run_component_inventory(model, output/'inventory')
    scene = load_component_scene(output/'inventory'/'report.json')
    if scene['source_sha256'] != model_hash:
        raise ValueError('Inventory snapshot differs from the retained candidate')
    center = np.asarray(refinement['normalization']['center'], float); extent = float(refinement['normalization']['extent'])
    if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite(extent) or extent <= 0:
        raise ValueError('Invalid refinement normalization')
    normalized = TriangleMesh((scene['mesh'].vertices-center)/extent, scene['mesh'].faces, [])
    # Faces two parts share exactly are inside their union: no photograph sees
    # them. They rasterize nothing (collapsed to a point, so component labels
    # stay complete), leave every optical group, and are omitted from the candidate.
    face_owner = np.zeros(len(normalized.faces), np.int64)
    face_offsets = {}
    for ordinal, (key, labels) in enumerate(sorted(scene['primitive_labels'].items())):
        rows = [r for r in scene['component_table'] if tuple(r['source_binding'][k] for k in ('node_index', 'mesh_index', 'primitive_index')) == key]
        offset = min(r['source_global_face_offset'] for r in rows)
        face_owner[offset:offset + len(labels)] = ordinal
        face_offsets[key] = (offset, len(labels))
    interior_mask, interior_receipt = interior_contact_faces(scene['mesh'], owners=face_owner)
    # Bodies (components) whose outer surfaces continue across a shared contact
    # patch were one body the split cut; they get a reunited membership reading.
    face_component = np.full(len(normalized.faces), -1, np.int64)
    for key, labels in scene['primitive_labels'].items():
        rows = [r for r in scene['component_table'] if tuple(r['source_binding'][k] for k in ('node_index', 'mesh_index', 'primitive_index')) == key]
        offset = min(r['source_global_face_offset'] for r in rows)
        mapping = np.full(max(r['local_component_id'] for r in rows) + 1, -1, np.int64)
        for r in rows:
            mapping[r['local_component_id']] = r['component_id']
        face_component[offset:offset + len(labels)] = mapping[np.asarray(labels, np.int64)]
    if np.any(face_component < 0):
        raise ValueError('Every face must belong to an inventoried component')
    continuity = cut_continuity(scene['mesh'], interior_mask, face_component, angle_degrees=policy.cut_continuity_angle_degrees,
                                minimum_edges=policy.cut_continuity_minimum_edges)
    body_edges = [p['bodies'] for p in continuity['pairs'] if p['enough_edges'] and p['continuous_fraction'] >= policy.cut_continuity_fraction]
    continuity = {**continuity, 'body_edges': body_edges, 'continuity_fraction': policy.cut_continuity_fraction,
                  'decision': 'a pair with enough boundary edges and at least the continuity fraction is one cut body; '
                              'the inference adds a reunited variant of every partition and the composition rank decides'}
    visible_faces = np.array(normalized.faces, copy=True)
    visible_faces[interior_mask] = visible_faces[interior_mask][:, [0, 0, 0]]
    projected = TriangleMesh(normalized.vertices, visible_faces, [])
    interior_by_binding = {key: interior_mask[offset:offset + count] for key, (offset, count) in face_offsets.items()}
    depth_to_reference = extent/scene['reference_extent']
    components = [{'component_id': t['component_id'], 'source_face_count': t['source_face_count'],
                   'source_binding': t['source_binding'], 'local_component_id': t['local_component_id']}
                  for t in scene['component_table']]
    views, view_arrays, view_records, posed_views = [], {}, [], {}
    for photo in photos:
        view = _view_for(refinement, photo, model_hash)
        if view is None:
            view_records.append({'id': photo['id'], 'status': 'no_fitted_camera'})
            continue
        photo_raw = Path(photo['path']).read_bytes()
        if _sha(photo_raw) != photo['sha256']:
            raise ValueError('Photo bytes differ from their pinned hash')
        with Image.open(io.BytesIO(photo_raw)) as image:
            if getattr(image, 'n_frames', 1) != 1 or image.getexif().get(274, 1) != 1:
                raise ValueError('Photos must be normalized single-frame upright images')
            if 'A' in image.getbands() and image.getchannel('A').getextrema()[0] < 255:
                view_records.append({'id': photo['id'], 'status': 'alpha_geometry_only',
                                     'reason': 'Unknown backdrop; alpha supports geometry but not lens aperture or color membership.'})
                continue
            rgb = np.asarray(image.convert('RGB')).copy()
        native_h, native_w = rgb.shape[:2]
        if view.get('image_size_original') != [native_w, native_h]:
            raise ValueError('Camera original pixel grid differs from photograph')
        width, height = view['image_size_working']
        camera = Camera(**view['camera_fit']['camera'])
        proposals = aperture_engine.propose(rgb)
        rows, cols = _working_indices(native_h, height), _working_indices(native_w, width)
        interpretations, aperture_records, arrays = [], [], {'native_row_indices': rows, 'native_column_indices': cols}
        for variant, item in proposals['variants'].items():
            mask = np.asarray(item['mask'], bool)[np.ix_(rows, cols)]; known = np.asarray(item['known_domain'], bool)[np.ix_(rows, cols)]
            if mask.shape != (height, width) or np.any(mask & ~known):
                raise ValueError('Aperture proposal grid or domain is invalid')
            interpretations.append({'id': variant, 'mask': mask, 'known_domain': known,
                                    'provenance': {'engine': aperture_engine.describe(), 'variant': variant,
                                                   'crop_xyxy_exclusive': item['crop_xyxy_exclusive'],
                                                   'pixel_mapping': 'native[floor((working_index+0.5)*native_size/working_size)]'}})
            arrays[variant+'_mask'] = mask; arrays[variant+'_known_domain'] = known
            aperture_records.append({'variant': variant, 'crop_xyxy_exclusive': item['crop_xyxy_exclusive'],
                                     'native_positive_pixels': item['native_positive_pixels'],
                                     'working_positive_pixels': int(mask.sum()), 'logit_range': item.get('logit_range')})
        if not any(i['mask'].any() for i in interpretations):
            view_records.append({'id': photo['id'], 'status': 'no_positive_aperture_proposal', 'apertures': aperture_records})
            continue
        posed = projected
        if refinement.get('view_scene'):
            from .view_scene import pose_mesh_from_contract
            posed = pose_mesh_from_contract(projected, refinement['view_scene'], photo['id'],
                photo_sha256=photo['sha256'], normalization=refinement['normalization'])['mesh']
        posed_views[photo['id']] = posed
        projection = project_components(posed, scene['face_components'], camera, (height, width), depth_to_reference=depth_to_reference)
        pixels = [p['pixels'] for p in projection['pieces']]
        depths = [p['depth']*depth_to_reference for p in projection['pieces']]
        views.append({'id': photo['id'], 'shape': [height, width], 'component_pixels': pixels, 'component_depths': depths,
                      'interpretations': interpretations,
                      'provenance': {'photo_sha256': photo['sha256'], 'camera': camera.to_dict(), 'working_size_xy': [width, height],
                                     'depth_to_reference': depth_to_reference, 'depth_units': 'source referenced-world max-axis extent',
                                     'contrast': proposals.get('contrast')}})
        view_arrays[photo['id']] = {'id': photo['id'], 'shape': (height, width), 'pixels': height*width,
                                    'first_visible_source_face': projection['full_scene'].face_index,
                                    'component_pixels': pixels, 'component_depths': depths,
                                    'interpretations': [{'id': i['id'], 'mask': i['mask'].ravel(), 'known': i['known_domain'].ravel(),
                                                         'pixels': int(i['mask'].sum())} for i in interpretations]}
        folder = output/'views'/photo['id']; folder.mkdir(parents=True)
        stream = io.BytesIO(); np.savez_compressed(stream, **arrays); (folder/'apertures.npz').write_bytes(stream.getvalue())
        view_records.append({'id': photo['id'], 'status': 'projected', 'camera': camera.to_dict(), 'working_size_xy': [width, height],
                             'apertures': aperture_records, 'projection': projection['report'],
                             'aperture_arrays': {'path': f'views/{photo["id"]}/apertures.npz', 'sha256': _sha((folder/'apertures.npz').read_bytes())}})
    report = {'schema_version': 1, 'method': METHOD, 'status': 'running', 'accepted': False, 'quality_verdict': 'unmeasured',
              'implementation': implementation,
              'semantic_identity': 'not_inferred', 'model_sha256': model_hash, 'inventory': {'path': 'inventory/report.json',
              'sha256': scene['inventory_report_sha256'], 'components': len(components), 'source_faces': int(len(scene['face_components']))},
              'aperture_engine': aperture_engine.describe(), 'policy': asdict(policy), 'limits': asdict(limits),
              'interior_contact': interior_receipt, 'cut_continuity': continuity,
              'ranking': RANKING, 'views': view_records, 'hypotheses': [],
              'limitations': ['Apertures are image-only proposals; cameras are the refinement\'s fitted hypotheses.',
                              'Hypothesis ranking is a composition score under those inputs, not a verified physical identity.',
                              'A branch-optimistic entry promotes groups that are optical under some aperture branches; the rank, not the roles, decides between the readings.',
                              'Bridged candidates keep every source face; undeclared pieces keep their source materials until export.',
                              'Exactly coincident faces shared by two parts are interior contact faces: not projected, in no optical group, omitted from the runtime candidate.']}
    if len(views) < 2:
        if _sha(model.read_bytes()) != model_hash or implementation_manifest() != implementation:
            raise ValueError('Source or implementation changed during physical grouping')
        report.update(status='insufficient_views', reason='Physical grouping needs at least two views with cameras and positive apertures',
                      seconds=time.perf_counter()-started)
        _write(output/'report.json', report)
        return report
    ids = [v['id'] for v in views]
    branches = [{'id': 'all-full', 'interpretation_by_view': {i: 'full' for i in ids}},
                {'id': 'all-contrast_crop', 'interpretation_by_view': {i: 'contrast_crop' for i in ids}}]
    if len(ids) <= 3:
        import itertools
        for combo in itertools.product(('full', 'contrast_crop'), repeat=len(ids)):
            mapping = dict(zip(ids, combo))
            if mapping not in [b['interpretation_by_view'] for b in branches]:
                branches.append({'id': '__'.join(f'{i}-{v}' for i, v in mapping.items()), 'interpretation_by_view': mapping})
    inference = infer_physical_groups(components, views, branches, body_edges=body_edges, policy=policy, limits=limits)
    report['inference'] = _write(output/'inference.json', inference['report'])
    ranked = []
    entries = []
    next_index = len(inference['report']['hypotheses'])
    for h_report in inference['report']['hypotheses']:
        entries.append((h_report, h_report['index'], 'consensus', []))
        # A group whose role depends on the aperture branch (optical under some
        # crop policies, contained or minor under others) is not decided by the
        # roles: both readings become hypotheses and the composition rank decides.
        promoted = [g['group_id'] for g in h_report['groups'] if g['role_consensus'] == 'branch_dependent'
                    and any(r['role'] == 'optical_candidate' for r in g.get('roles_by_branch', []))]
        if promoted:
            entries.append((h_report, next_index, 'branch_optimistic', promoted))
            next_index += 1
    # An omitted fragment can be almost entirely visible inside the lens while
    # its hidden cut walls project outside it. Evaluate first-hit photographic
    # support and shared surface continuity before declaring the split complete.
    from .optical_membership import audit_optical_membership
    membership_audits = []
    repair_cache = {}
    for base, _, variant, promoted in list(entries):
        optical = [g for g in base['groups'] if g['role_consensus'] == 'optical_candidate' or g['group_id'] in promoted]
        cache_key = _identity([sorted(g['members']) for g in optical])
        if cache_key in repair_cache or not optical:
            continue
        audit = audit_optical_membership(normalized, scene['face_components'], optical,
            [{'id': v['id'], 'shape': v['shape'], 'camera': v['provenance']['camera'],
              'interpretations': v['interpretations'], 'mesh': posed_views[v['id']]} for v in views], visibility_mesh=projected)
        audit.update(source_sha256=model_hash, membership_hypothesis=base['index'], role_variant=variant)
        membership_audits.append(audit); repair_cache[cache_key] = audit
        if not audit['assignments']:
            continue
        repaired = deepcopy(base)
        repaired['membership_repair'] = audit
        assigned = {a['component_id']: a['group_id'] for a in audit['assignments']}
        for group in repaired['groups']:
            group['members'] = sorted([m for m in group['members'] if m not in assigned] +
                                      [m for m, gid in assigned.items() if gid == group['group_id']])
            group['source_face_count'] = sum(r['source_face_count'] for r in scene['component_table'] if r['component_id'] in group['members'])
            if group['group_id'] in [g['group_id'] for g in optical]:
                group['role_consensus'] = 'optical_candidate'
                group['views'] = []
                for view_id, va in view_arrays.items():
                    pixels = np.unique(np.concatenate([va['component_pixels'][m] for m in group['members']]))
                    interpretations = []
                    for it in va['interpretations']:
                        count = int(np.count_nonzero(it['known'][pixels]))
                        interpretations.append({'group_inside_fraction': float(np.count_nonzero(it['mask'][pixels])/count) if count else None})
                    group['views'].append({'view_id': view_id, 'interpretations': interpretations})
        repaired['groups'] = [g for g in repaired['groups'] if g['members']]
        repaired['group_count'] = len(repaired['groups'])
        repaired['role_counts'] = {role: sum(g['role_consensus']==role for g in repaired['groups']) for role in repaired['role_counts']}
        entries.append((repaired, next_index, 'visible_fragment_repair', []))
        next_index += 1
    report['optical_membership_audits'] = _write(output/'optical-membership-audits.json', membership_audits)
    # A mixed primitive is not an indivisible physical part. Make complete,
    # source-face-preserving virtual components only where independent views
    # and the existing optical surface agree on an interior patch.
    from .face_role_repair import propose_face_roles, split_component_ledger
    face_audits, face_cache, contexts = [], set(), {}
    for base, _, variant, promoted in list(entries):
        optical = [g for g in base['groups'] if g['role_consensus'] == 'optical_candidate' or g['group_id'] in promoted]
        signature = _identity([sorted(g['members']) for g in optical])
        if not optical or signature in face_cache:
            continue
        face_cache.add(signature)
        audit = propose_face_roles(normalized, scene['face_components'], optical,
            [{'id': v['id'], 'shape': v['shape'], 'camera': v['provenance']['camera'],
              'source_sha256': v['provenance']['photo_sha256'], 'interpretations': v['interpretations'],
              'mesh': posed_views[v['id']]} for v in views],
            visibility_mesh=projected)
        audit.update(source_sha256=model_hash, membership_hypothesis=base['index'], role_variant=variant)
        face_audits.append(audit)
        if not audit['assignments']:
            continue
        split_scene, repaired = split_component_ledger(scene, base, audit['assignments'])
        revised_arrays = {}
        for view in views:
            projection = project_components(posed_views[view['id']], split_scene['face_components'],
                Camera(**view['provenance']['camera']), tuple(view['shape']), depth_to_reference=depth_to_reference)
            revised_arrays[view['id']] = {**view_arrays[view['id']],
                'component_pixels': [p['pixels'] for p in projection['pieces']],
                'component_depths': [p['depth']*depth_to_reference for p in projection['pieces']]}
        for group in repaired['groups']:
            if group['group_id'] in [g['group_id'] for g in optical]:
                group['role_consensus'] = 'optical_candidate'
            group['views'] = []
            for vid, va in revised_arrays.items():
                pixels = np.unique(np.concatenate([va['component_pixels'][m] for m in group['members']]))
                group['views'].append({'view_id': vid, 'interpretations': [
                    {'group_inside_fraction': float(np.count_nonzero(it['mask'][pixels])/max(1,np.count_nonzero(it['known'][pixels])))}
                    for it in va['interpretations']]})
        repaired['role_counts'] = {role: sum(g['role_consensus'] == role for g in repaired['groups']) for role in repaired['role_counts']}
        repaired['face_role_repair'] = {k:v for k,v in audit.items() if k != 'assignments'}
        repaired['face_role_repair']['assigned_faces'] = sum(len(a['source_face_indices']) for a in audit['assignments'])
        contexts[next_index] = (split_scene, revised_arrays)
        entries.append((repaired, next_index, 'fused_face_repair', []))
        next_index += 1
    report['face_role_audits'] = _write(output/'face-role-audits.json', face_audits)
    for h_report, index, variant, promoted in entries:
        active_scene, active_arrays = contexts.get(index, (scene, view_arrays))
        folder = output/'hypotheses'/f'h{index:02d}'; folder.mkdir(parents=True)
        optical = {g['group_id'] for g in h_report['groups'] if g['role_consensus'] == 'optical_candidate'} | set(promoted)
        consensus = [{'group_id': g['group_id'], 'members': g['members'],
                      'role': 'optical_candidate' if g['group_id'] in optical else
                              g['role_consensus'] if g['role_consensus'] in ('non_optical_evidence', 'unobserved') else 'unresolved'}
                     for g in h_report['groups']]
        declared = [g['group_id'] for g in h_report['groups'] if g['group_id'] in optical]
        # Per-view registration of each declared group: the best interpretation's
        # inside share. A view under the optical inside threshold projects the
        # part onto pixels that are not its lens, so it cannot measure the part's
        # appearance; the fit is told to leave that (group, photo) out.
        registration = {}
        for g in h_report['groups']:
            if g['group_id'] not in optical:
                continue
            registration[g['group_id']] = {}
            for v in g['views']:
                shares = [it['group_inside_fraction'] for it in v['interpretations'] if it['group_inside_fraction'] is not None]
                inside = max(shares) if shares else None
                registration[g['group_id']][v['view_id']] = {
                    'inside_fraction': inside, 'fit_eligible': inside is not None and inside >= policy.optical_inside_fraction}
        table, compositions, core_audit = [], {}, []
        for view_id, va in active_arrays.items():
            from scipy.ndimage import binary_erosion
            core = binary_erosion(np.logical_and.reduce([it['mask'].reshape(va['shape']) for it in va['interpretations']]), iterations=2)
            visible = va['first_visible_source_face']
            hit = visible >= 0
            optical_faces = np.isin(active_scene['face_components'], [m for g in consensus if g['group_id'] in optical for m in g['members']])
            opaque = np.zeros_like(core)
            opaque[hit] = ~optical_faces[visible[hit]]
            core_audit.append({'view_id': view_id, 'core_pixels': int(core.sum()),
                               'opaque_core_pixels': int(np.count_nonzero(core & opaque)),
                               'opaque_core_fraction': float(np.count_nonzero(core & opaque)/core.sum()) if core.any() else None})
            composition = compose_view_rays(va, consensus)
            compositions[view_id] = composition['report']
            for item in composition['report']['interpretations']:
                table.append({'view_id': view_id, 'interpretation_id': item['interpretation_id'],
                              **{k: item[k] for k in ('aperture_pixels', 'clean_transmission_fraction', 'rear_content_fraction', 'excluded_fraction',
                                                      'aperture_without_geometry_fraction', 'aperture_on_opaque_only_fraction',
                                                      'optical_inside_aperture_known_pixels', 'optical_outside_aperture_known_pixels',
                                                      'contamination_fraction')}})
            picture = np.zeros((*va['shape'], 3), np.uint8)
            for name, code in CODES.items():
                picture[composition['class_map'] == code] = CLASS_COLORS[name]
            Image.fromarray(picture).save(folder/f'{view_id}-rays.png')
        key, detail = composition_rank_key(table if declared else [], index, len(declared))
        if not declared:
            detail = {**detail, 'reason': 'no consensus optical candidate'}
        summary = {'index': index, 'membership_hypothesis': h_report['index'], 'role_variant': variant, 'promoted_groups': promoted,
                   'membership_repair': h_report.get('membership_repair'),
                   'face_role_repair': h_report.get('face_role_repair'),
                   'cut_reunion': bool(h_report.get('cut_reunion')),
                   'rungs': h_report['rungs'], 'depth_tolerances': h_report['depth_tolerances'],
                   'group_count': h_report['group_count'], 'role_counts': h_report['role_counts'],
                   'consensus_optical_groups': [{'group_id': g['group_id'], 'members': g['members'], 'source_face_count': g['source_face_count'],
                                                 'attached_minor_members': g['attached_minor_members']}
                                                for g in h_report['groups'] if g['group_id'] in optical],
                   'declared_membership_sha256': _identity([np.flatnonzero(np.isin(active_scene['face_components'],g['members'])).tolist()
                                                          for g in h_report['groups'] if g['group_id'] in optical]),
                   'composition_table': table, 'composition_score': detail['usable_min'] if detail['usable_min'] is not None else -1.,
                   'composition_contamination': detail['contamination_max'], 'composition_rank_key': list(key), 'composition_detail': detail,
                   'view_registration': registration,
                   'lens_core_audit': core_audit,
                   'compositions': compositions, 'folder': folder.relative_to(output).as_posix(), 'bridge': None}
        ranked.append((h_report, summary))
    ranked.sort(key=lambda item: tuple(item[1]['composition_rank_key']))
    executed = {}
    for rank, (h_report, summary) in enumerate(ranked):
        active_scene, _ = contexts.get(summary['index'], (scene, view_arrays))
        summary['composition_rank'] = rank
        declared = [g['group_id'] for g in summary['consensus_optical_groups']]
        folder = output/summary['folder']
        if not declared:
            summary['bridge'] = {'status': 'not_executed', 'reason': 'no consensus optical candidate'}
        elif summary['declared_membership_sha256'] in executed:
            summary['bridge'] = {'status': 'identical_declaration_already_executed', 'executed_in_hypothesis': executed[summary['declared_membership_sha256']]}
        elif len(executed) >= maximum_bridges:
            summary['bridge'] = {'status': 'not_executed', 'reason': f'bridge capacity {maximum_bridges} reached; lower-ranked hypothesis retained unbridged'}
        else:
            try:
                bridge = folder/'bridge'; bridge.mkdir()
                declarations, groups = partition_declarations_for_hypothesis(h_report, active_scene['component_table'], active_scene['primitive_labels'],
                    source_sha256=model_hash, declared_groups=declared, interior_faces=interior_by_binding,
                    provenance={'stage': METHOD, 'ranking': RANKING, 'rank': rank, 'role_variant': summary['role_variant']})
                _write(bridge/'partition-declarations.json', declarations)
                partition = run_face_partition(model, bridge/'partition', declarations=bridge/'partition-declarations.json')
                partitioned_raw = (bridge/'partition'/'partitioned.glb').read_bytes()
                receipt = json.loads((bridge/'partition'/'receipt.json').read_bytes())
                group_declarations = declarations_for_partition(raw, partitioned_raw, receipt, groups, coordinate_frame=COORDINATE_FRAME,
                    provenance={'method': METHOD, 'hypothesis_index': summary['index'], 'membership_hypothesis': h_report['index'],
                                'role_variant': summary['role_variant'], 'declared_groups': declared, 'semantic_identity': 'not_inferred'})
                _write(bridge/'group-declarations.json', group_declarations)
                preparation = run_optical_group_preparation(bridge/'partition'/'partitioned.glb', bridge/'optical-preparation',
                                                            grouping_mode='explicit_declarations', declarations=bridge/'group-declarations.json')
                transferred = transfer_cameras_to_partition(refinement, raw, partitioned_raw, receipt,
                                                            partitioned_model_path=str(bridge/'partition'/'partitioned.glb'))
                cameras = _write(bridge/'cameras.json', transferred)
                summary['bridge'] = {'status': 'bridge_executed' if preparation['status'] == 'prepared_optical_group_candidate' else 'preparation_unsupported',
                                     'partition_status': partition['status'], 'partition_verification': partition['verification']['status'],
                                     'partitioned_model': {'path': (bridge/'partition'/'partitioned.glb').relative_to(output).as_posix(), 'sha256': _sha(partitioned_raw)},
                                     'preparation_status': preparation['status'], 'preparation_reasons': preparation.get('reasons'),
                                     'preparation_report': (bridge/'optical-preparation'/'report.json').relative_to(output).as_posix(),
                                     'prepared_groups': len(preparation.get('groups', [])), 'declared_groups': declared, 'piece_groups': groups,
                                     'cameras': {'path': (bridge/'cameras.json').relative_to(output).as_posix(), 'sha256': cameras['sha256']}}
                if summary['bridge']['status'] == 'bridge_executed':
                    executed[summary['declared_membership_sha256']] = summary['index']
            except Exception as error:
                summary['bridge'] = {'status': 'failed', 'error_type': type(error).__name__, 'error': str(error), 'declared_groups': declared}
        _write(folder/'report.json', summary)
        report['hypotheses'].append(summary)
    selected = next((s for s in report['hypotheses'] if s['bridge'] and s['bridge']['status'] == 'bridge_executed'), None)
    report['selected_hypothesis'] = ({'index': selected['index'], 'composition_rank': selected['composition_rank'],
                                      'composition_score': selected['composition_score'],
                                      'composition_contamination': selected['composition_contamination'],
                                      'partitioned_model': selected['bridge']['partitioned_model'],
                                      'preparation_report': selected['bridge']['preparation_report'], 'cameras': selected['bridge']['cameras'],
                                      'view_registration': selected['view_registration'],
                                      'registration_rule': f'a (group, photo) whose best-interpretation inside share is under {policy.optical_inside_fraction} is excluded from the appearance fit',
                                      'rule': 'highest composition score with an executed bridge; every other hypothesis is retained'}
                                     if selected else None)
    report.update(status='hypotheses_bridged' if selected else 'no_bridged_hypothesis', seconds=time.perf_counter()-started,
                  hypothesis_count=len(report['hypotheses']), bridges_executed=len(executed))
    if _sha(model.read_bytes()) != model_hash or implementation_manifest() != implementation:
        raise ValueError('Source or implementation changed during physical grouping')
    _write(output/'report.json', report)
    return report
