"""Bounded smooth effective optical normals with exact source geometry retention.

A manufactured lens has a smooth optical surface, whereas generated triangles
often carry irregular normal fields. Fit a low-order bend to *geometric* surface
samples, using a spatial holdout and a thickness/multilayer guard. The resulting
normals are an explicit geometry-derived alternative, never photographic truth.
No silhouette, frame triangle, texture, group membership, or depth is changed.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import cv2
from scipy.optimize import least_squares

from .optical_groups import prepare_optical_group


@dataclass(frozen=True)
class SmoothOpticalPolicy:
    grid_size: int = 32
    minimum_cells: int = 64
    minimum_projected_normal: float = .25
    minimum_graph_surface_area_share: float = .65
    maximum_holdout_p95_fraction: float = .02
    maximum_cell_depth_spread_fraction: float = .035
    degree_tolerance_fraction: float = .001
    maximum_degrees: int = 3
    maximum_skin_separation_fraction: float = .06
    maximum_skin_normal_disagreement_degrees: float = 7.5
    minimum_skin_overlap: float = .8

    def __post_init__(self):
        if (type(self.grid_size) is not int or not 8 <= self.grid_size <= 64
                or type(self.minimum_cells) is not int or self.minimum_cells < 20
                or type(self.maximum_degrees) is not int or self.maximum_degrees not in (1, 2, 3)):
            raise ValueError('Use bounded grid, minimum support, and polynomial degree')
        for name in ('minimum_projected_normal', 'minimum_graph_surface_area_share', 'maximum_holdout_p95_fraction',
                     'maximum_cell_depth_spread_fraction', 'degree_tolerance_fraction',
                     'maximum_skin_separation_fraction', 'minimum_skin_overlap'):
            value = getattr(self, name)
            if not np.isfinite(value) or not 0 < value < 1:
                raise ValueError('Smooth optical policy fractions must be in (0,1)')
        if not np.isfinite(self.maximum_skin_normal_disagreement_degrees) or not 0 < self.maximum_skin_normal_disagreement_degrees < 45:
            raise ValueError('Skin normal agreement must use a bounded angular tolerance')


def _terms(degree):
    return [(i, j) for total in range(degree + 1) for i in range(total + 1) for j in [total - i]]


def _matrix(xy, terms):
    x, y = np.asarray(xy).T
    return np.column_stack([x ** i * y ** j for i, j in terms])


def evaluate_front_bend(fit, positions):
    """Evaluate the fitted effective surface and analytic unit normal in world axes."""
    p = np.asarray(positions, float)
    if p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all():
        raise ValueError('Finite Nx3 positions required')
    if fit.get('representation') == 'two_skin_mid_surface':
        a, na = evaluate_front_bend(fit['skins'][0], p)
        b, nb = evaluate_front_bend(fit['skins'][1], p)
        # Averaging unnormalized graph gradients is the derivative of the
        # averaged depth function. Averaging unit normals would not be.
        normals = .5 * (na / na[:, 2, None] + nb / nb[:, 2, None])
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        return (a + b) * .5, normals
    xy = (p[:, :2] - fit['center_xy']) / fit['span_xy']
    x, y = xy.T
    terms, coefficients = fit['terms'], fit['coefficients']
    z = _matrix(xy, terms) @ coefficients
    dx, dy = np.zeros(len(p)), np.zeros(len(p))
    for (i, j), coefficient in zip(terms, coefficients):
        if i:
            dx += coefficient * i * x ** (i - 1) * y ** j / fit['span_xy'][0]
        if j:
            dy += coefficient * j * x ** i * y ** (j - 1) / fit['span_xy'][1]
    normals = np.column_stack((-dx, -dy, np.ones(len(p))))
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    return z, normals


def _fit_front_bend_single(primitives, *, policy=SmoothOpticalPolicy()):
    """Fit one +Z graph per declared group, refusing unsupported surface classes.

    One area-weighted median per occupied XY cell avoids tessellation-density
    bias. One in four spatial cells is excluded while selecting degree. Cells
    containing separated depth layers reject the single-surface hypothesis.
    This holdout tests smooth representation of the input mesh, not image fit.
    """
    samples, weights, hashes = [], [], []
    total_area, kept_area = 0., 0.
    for item in primitives:
        p, faces = np.asarray(item['positions'], float), np.asarray(item['indices'])
        if (p.ndim != 2 or p.shape[1] != 3 or not np.isfinite(p).all() or faces.ndim != 2
                or faces.shape[1] != 3 or faces.dtype.kind not in 'iu' or not len(faces)
                or faces.min() < 0 or faces.max() >= len(p)):
            raise ValueError('Invalid optical geometry')
        t = p[faces]
        cross = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        length = np.linalg.norm(cross, axis=1)
        if np.any(length <= 0):
            raise ValueError('Degenerate optical geometry')
        projected = np.abs(cross[:, 2])
        keep = projected / length >= policy.minimum_projected_normal
        total_area += float(length.sum())
        kept_area += float(length[keep].sum())
        samples.append(t[keep].mean(axis=1))
        weights.append(projected[keep])
        hashes.append(hashlib.sha256(p.tobytes() + faces.tobytes()).hexdigest())
    report = {'method': 'spatially_validated_smooth_optical_bend_v1', 'status': 'unsupported',
              'accepted': False, 'source_geometry_sha256': sorted(hashes), 'policy': asdict(policy),
              'scope': 'Input-mesh smoothness hypothesis; no independent photographic geometry validation',
              'reasons': [], 'trials': []}
    report['graph_surface_area_share'] = kept_area / total_area if total_area else 0.
    p = np.concatenate(samples) if samples else np.empty((0, 3))
    w = np.concatenate(weights) if weights else np.empty(0)
    if not len(p) or np.any(np.ptp(p[:, :2], axis=0) <= 0):
        report['reasons'].append('no_front_graph_support')
        return {'fit': None, 'report': report}
    if report['graph_surface_area_share'] < policy.minimum_graph_surface_area_share:
        report['reasons'].append('front_graph_does_not_cover_group')
        return {'fit': None, 'report': report}
    lo, span = p[:, :2].min(axis=0), np.ptp(p[:, :2], axis=0)
    scale = float(span.max())
    center = lo + span * .5
    bins = np.minimum(policy.grid_size - 1, ((p[:, :2] - lo) / span * policy.grid_size).astype(int))
    ids = bins[:, 0] + policy.grid_size * bins[:, 1]
    cells, cell_ids, spreads = [], [], []
    for cell in np.unique(ids):
        selected = ids == cell
        values, weight = p[selected], w[selected]
        order = np.argsort(values[:, 2], kind='stable')
        representative = order[np.searchsorted(np.cumsum(weight[order]), weight.sum() * .5)]
        # Keep the representative's actual XY with its median depth. Combining
        # mean XY with median Z biases even an exactly planar surface.
        cells.append(values[representative])
        cell_ids.append(int(cell))
        # A robust depth range discounts a small amount of side-wall support.
        order = np.argsort(values[:, 2]); cumulative = np.cumsum(weight[order]) / weight.sum()
        z = values[order, 2]
        spreads.append(float(np.interp(.9, cumulative, z) - np.interp(.1, cumulative, z)))
    cells, cell_ids = np.asarray(cells), np.asarray(cell_ids)
    report.update(occupied_cells=len(cells), surface_triangle_samples=len(p),
                  maximum_xy_extent=scale, cell_depth_spread_p95_fraction=float(np.quantile(spreads, .95) / scale))
    if len(cells) < policy.minimum_cells:
        report['reasons'].append('insufficient_spatial_support')
    holdout = ((cell_ids % policy.grid_size) + 2 * (cell_ids // policy.grid_size)) % 4 == 0
    if min(int(holdout.sum()), int((~holdout).sum())) < 12:
        report['reasons'].append('insufficient_spatial_holdout')
    if report['reasons']:
        return {'fit': None, 'report': report}
    xy = (cells[:, :2] - center) / span
    z = cells[:, 2]
    fits = []
    for degree in range(1, policy.maximum_degrees + 1):
        terms = _terms(degree)
        design = _matrix(xy, terms)
        train = design[~holdout]
        if np.linalg.matrix_rank(train) < len(terms):
            continue
        initial = np.linalg.lstsq(train, z[~holdout], rcond=None)[0]
        fit = least_squares(lambda c: (train @ c - z[~holdout]) / scale, initial,
                            loss='soft_l1', f_scale=.005, max_nfev=100)
        residual = design @ fit.x - z
        row = {'degree': degree, 'holdout_cell_count': int(holdout.sum()),
               'holdout_rms_fraction': float(np.sqrt(np.mean(residual[holdout] ** 2)) / scale),
               'holdout_p95_fraction': float(np.quantile(np.abs(residual[holdout]), .95) / scale),
               'optimizer_converged': bool(fit.success)}
        report['trials'].append(row)
        fits.append((row, fit.x, terms, design))
    eligible = [f for f in fits if f[0]['optimizer_converged'] and
                f[0]['holdout_p95_fraction'] <= policy.maximum_holdout_p95_fraction]
    if not eligible:
        report['reasons'].append('smooth_surface_not_supported_on_holdout')
        return {'fit': None, 'report': report}
    best = min(f[0]['holdout_rms_fraction'] for f in eligible)
    row, coefficients, terms, design = next(f for f in eligible if
        f[0]['holdout_rms_fraction'] <= best + policy.degree_tolerance_fraction)
    refit = least_squares(lambda c: (design @ c - z) / scale, coefficients,
                          loss='soft_l1', f_scale=.005, max_nfev=100)
    if not refit.success:
        report['reasons'].append('full_surface_fit_did_not_converge')
        return {'fit': None, 'report': report}
    value = {'center_xy': center.tolist(), 'span_xy': span.tolist(), 'terms': terms,
             'coefficients': refit.x.tolist(), 'degree': row['degree']}
    # Remove supported bend before measuring thickness: raw within-cell Z
    # spread also contains legitimate curvature, especially near a shield rim.
    residual = p[:, 2] - evaluate_front_bend(value, p)[0]
    detrended_spreads = []
    for cell in cell_ids:
        selected = ids == cell
        values, weight = residual[selected], w[selected]
        order = np.argsort(values); cumulative = np.cumsum(weight[order]) / weight.sum()
        detrended_spreads.append(float(np.interp(.9, cumulative, values[order]) -
                                       np.interp(.1, cumulative, values[order])))
    report['detrended_cell_depth_spread_p95_fraction'] = float(np.quantile(detrended_spreads, .95) / scale)
    if report['detrended_cell_depth_spread_p95_fraction'] > policy.maximum_cell_depth_spread_fraction:
        report['reasons'].append('thick_or_multiple_depth_layers')
        return {'fit': None, 'report': report}
    report.update(status='supported_smooth_surface_hypothesis', selected_degree=row['degree'],
                  selected_holdout=row, fitted_surface=value,
                  full_surface_rms_fraction=float(np.sqrt(np.mean((design @ refit.x - z) ** 2)) / scale))
    return {'fit': value, 'report': report}


def fit_front_bend(primitives, *, policy=SmoothOpticalPolicy()):
    """Compare a single surface with a bounded front/back-skin construction.

    Opposite winding is a geometric partition, not a physical front/back claim.
    Two separately smooth skins may support one effective midpoint surface only
    when their projected support overlaps, depth ordering is consistent, the
    gap is small, and their normals agree. This avoids treating shell thickness
    as surface roughness while retaining an explicit single-interface assumption.
    """
    primitives = list(primitives)
    single = _fit_front_bend_single(primitives, policy=policy)
    sides, samples, projected_triangles, area = [[], []], [[], []], [[], []], [0., 0.]
    for item in primitives:
        p, faces = np.asarray(item['positions'], float), np.asarray(item['indices'])
        t = p[faces]; cross = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        length = np.linalg.norm(cross, axis=1)
        eligible = np.abs(cross[:, 2]) / length >= policy.minimum_projected_normal
        for side, sign in enumerate((1, -1)):
            select = cross[:, 2] * sign > 0
            if np.any(select):
                sides[side].append({'positions': p, 'indices': faces[select]})
            keep = select & eligible
            samples[side].append(t[keep].mean(axis=1))
            projected_triangles[side].append(t[keep, :, :2])
            area[side] += float(np.abs(cross[keep, 2]).sum())
    if min(area) <= sum(area) * .1 or not all(sides):
        return single
    skin_fits = [_fit_front_bend_single(side, policy=policy) for side in sides]
    if any(s['fit'] is None for s in skin_fits):
        single['report']['two_skin_attempt'] = {'status': 'unsupported_skin_fit',
                                               'skins': [s['report'] for s in skin_fits]}
        return single
    points = [np.concatenate(p) for p in samples]
    all_points = np.concatenate(points)
    lo, span = all_points[:, :2].min(axis=0), np.ptp(all_points[:, :2], axis=0)
    # Compare triangle-union coverage, not centroid occupancy. Different
    # triangulations leave different empty centroid cells on identical skins.
    masks = []
    resolution = policy.grid_size * 4
    for triangles in projected_triangles:
        mask = np.zeros((resolution + 2, resolution + 2), np.uint8)
        for triangle in np.concatenate(triangles):
            xy = np.rint(((triangle - lo) / span * (resolution - 1) + 1) * 256).astype(np.int32)
            cv2.fillConvexPoly(mask, xy, 1, shift=8)
        masks.append(mask.astype(bool))
    overlap = float(np.count_nonzero(masks[0] & masks[1]) / max(np.count_nonzero(mask) for mask in masks))
    za, na = evaluate_front_bend(skin_fits[0]['fit'], all_points)
    zb, nb = evaluate_front_bend(skin_fits[1]['fit'], all_points)
    angles = np.degrees(np.arccos(np.clip(np.sum(na * nb, axis=1), -1., 1.)))
    gap = za - zb
    median_sign = np.sign(np.median(gap))
    ordering = float(np.mean(gap * median_sign >= -float(span.max()) * 1e-5))
    audit = {'status': 'supported', 'projected_support_overlap': overlap,
             'projected_support_method': f'XY triangle-union raster at {resolution} cells with subpixel coordinates',
             'normal_disagreement_p95_degrees': float(np.quantile(angles, .95)),
             'skin_separation_p95_fraction': float(np.quantile(np.abs(gap), .95) / span.max()),
             'consistent_depth_ordering_share': ordering,
             'skins': [s['report'] for s in skin_fits],
             'assumption': 'Nearby similarly bent declared optical skins represent one effective lens, not proof of physical thickness'}
    if (overlap < policy.minimum_skin_overlap
            or audit['normal_disagreement_p95_degrees'] > policy.maximum_skin_normal_disagreement_degrees
            or audit['skin_separation_p95_fraction'] > policy.maximum_skin_separation_fraction
            or ordering < .98):
        audit['status'] = 'incompatible_skins'
        single['report']['two_skin_attempt'] = audit
        return single
    score = max(s['report']['selected_holdout']['holdout_p95_fraction'] for s in skin_fits)
    if single['fit'] is not None and score >= single['report']['selected_holdout']['holdout_p95_fraction'] * .8:
        audit['status'] = 'single_surface_retained_by_complexity'
        single['report']['two_skin_attempt'] = audit
        return single
    fit = {'representation': 'two_skin_mid_surface', 'skins': [s['fit'] for s in skin_fits],
           'degree': max(s['fit']['degree'] for s in skin_fits)}
    report = deepcopy(single['report'])
    report.update(status='supported_smooth_surface_hypothesis', representation='two_skin_mid_surface',
                  reasons=[], selected_degree=fit['degree'], fitted_surface=fit, two_skin_support=audit,
                  single_surface_attempt=single['report'], selected_holdout={
                      'holdout_p95_fraction': score,
                      'holdout_rms_fraction': max(s['report']['selected_holdout']['holdout_rms_fraction'] for s in skin_fits),
                      'holdout_cell_count': sum(s['report']['selected_holdout']['holdout_cell_count'] for s in skin_fits),
                      'scope': 'Worst skin holdout residual; paired midpoint is a manufacturing prior'})
    return {'fit': fit, 'report': report}


def propose_smooth_optical_group(prepared, *, policy=SmoothOpticalPolicy()):
    """Return a normal-field alternative accepted by the unchanged verified exporter."""
    result = fit_front_bend(prepared['primitives'], policy=policy)
    if result['fit'] is None:
        return {'prepared': prepared, 'report': result['report'], 'changed': False}
    source = prepared['report']
    rows = {row['id']: row for row in source['primitives']}
    members = []
    for item in prepared['primitives']:
        normals = evaluate_front_bend(result['fit'], item['positions'])[1]
        members.append({'id': item['id'], 'source_binding': rows[item['id']]['source_binding'],
            'coordinate_frame_id': source['coordinate_frame']['id'], 'positions': item['positions'],
            'indices': item['indices'], 'normals': normals,
            'normal_transform': {'method': 'identity', 'source_to_common_matrix': np.eye(4).tolist(),
                'provenance': result['report']}})
    alternative = prepare_optical_group(source['group_id'], members, identity=source['identity'],
                                         coordinate_frame=source['coordinate_frame'])
    if alternative['report']['status'] != 'prepared_candidate':
        raise ValueError('Smooth normal hypothesis failed group preparation')
    for old, new in zip(prepared['primitives'], alternative['primitives']):
        for name in ('positions', 'indices', 'uv'):
            if not np.array_equal(old[name], new[name]):
                raise ValueError('Smooth normals changed source geometry or coordinates')
    return {'prepared': alternative, 'report': result['report'], 'changed': True}


def run_smooth_optical_preparation(preparation_report, output, *, policy=SmoothOpticalPolicy()):
    """Write a complete preparation-stage alternative for existing ray/fitting stages.

    Original positions, triangles, texture source, declarations and membership
    identity remain pinned. Unsupported groups retain their existing normals.
    The result deliberately has a new model hash: old photo observations/fits
    must be recomputed against the new incidence field, never silently reused.
    """
    from .deform_glb import _read_bytes
    from .mesh import load_glb_bytes
    from .optical_group_asset import (_prepared, _source_matrices, read_optical_group_candidate,
                                      write_optical_group_candidate)
    from .photo_lens_observations import _child, _read_pinned
    from .prepare_optical_groups import _write, _json_bytes
    from .refine_photos import implementation_manifest
    source_report, output = Path(preparation_report).resolve(), Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Use a new or empty smooth preparation output')
    raw_report = source_report.read_bytes()
    original = json.loads(raw_report)
    if original.get('status') != 'prepared_optical_group_candidate':
        raise ValueError('A completed optical group preparation is required')
    pins = {str(source_report): hashlib.sha256(raw_report).hexdigest()}
    folder = source_report.parent

    def read(reference):
        return _read_pinned(_child(folder, reference['path']), pins, reference['sha256'])

    source_bytes = read(original['source_snapshot'])
    if hashlib.sha256(source_bytes).hexdigest() != original['source_sha256']:
        raise ValueError('Prepared source hash mismatch')
    export = json.loads(read(original['export']))
    model_path = _child(folder, original['model']['path'])
    read(original['model'])
    read_optical_group_candidate(model_path, export, expected_sha256=original['model']['sha256'])
    appearances = {g['group_id']: g['appearance'] for g in export['groups']}
    exported_group_hashes = {g['group_id']: g['prepared_group_sha256'] for g in export['groups']}
    groups = []
    for group in original['groups']:
        report = json.loads(read(group['prepared_report']))
        if (report.get('group_id') != group['group_id'] or
                report.get('group_sha256') != exported_group_hashes.get(group['group_id'])):
            raise ValueError('Prepared group identity differs from exported model')
        primitives = []
        for member in group['primitives']:
            with np.load(io.BytesIO(read(member)), allow_pickle=False) as archive:
                if set(archive.files) != {'positions', 'indices', 'normals', 'uv'}:
                    raise ValueError('Incomplete prepared geometry archive')
                primitives.append({'id': member['id'], **{k: archive[k].copy() for k in archive.files}})
        groups.append({'report': report, 'primitives': primitives})
    if len(groups) != len(exported_group_hashes) or {g['report']['group_id'] for g in groups} != set(exported_group_hashes):
        raise ValueError('Prepared group inventory differs from exported model')
    _, document, _ = _read_bytes(source_bytes)
    _prepared([{'prepared': group, 'appearance': appearances[group['report']['group_id']]} for group in groups],
              load_glb_bytes(source_bytes), original['source_sha256'], _source_matrices(document), document)
    alternatives = [propose_smooth_optical_group(group, policy=policy) for group in groups]
    report = deepcopy(original)
    report.update(groups=[], model=None, export=None, implementation=implementation_manifest(),
        smooth_optical_geometry={'method': 'source_preserving_smooth_optical_normals_v1',
            'source_preparation': str(source_report), 'source_preparation_sha256': pins[str(source_report)],
            'groups': [a['report'] for a in alternatives], 'changed_groups': sum(a['changed'] for a in alternatives),
            'positions_indices_and_uv_exact': True, 'photo_observations_require_recomputation': True,
            'independent_photo_validation': False})
    declaration_bytes = read(original['declarations']) if original.get('declarations') else None
    if any(hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest for p, digest in pins.items()):
        raise ValueError('Smooth geometry inputs changed while loading')
    output.mkdir(parents=True, exist_ok=True)
    report['source_snapshot'] = _write(output, 'source.glb', source_bytes)
    if declaration_bytes is not None:
        report['declarations'] = {**_write(output, 'declarations.json', declaration_bytes),
                                 'original_path': original['declarations'].get('original_path')}
    for i, alternative in enumerate(alternatives):
        group = alternative['prepared']; prefix = f'group-{i:03d}'
        row = {'group_id': group['report']['group_id'], 'prepared_report':
               _write(output, prefix + '/report.json', _json_bytes(group['report'])), 'primitives': []}
        for j, item in enumerate(group['primitives']):
            stream = io.BytesIO()
            np.savez_compressed(stream, **{k: item[k] for k in ('positions', 'indices', 'normals', 'uv')})
            row['primitives'].append({'id': item['id'], **_write(output, f'{prefix}/primitive-{j:03d}.npz', stream.getvalue())})
        report['groups'].append(row)
    receipt = write_optical_group_candidate(output / 'source.glb', output / 'prepared-neutral.glb',
        [{'prepared': a['prepared'], 'appearance': appearances[a['prepared']['report']['group_id']]} for a in alternatives],
        source_sha256=original['source_sha256'], provenance=report['smooth_optical_geometry'])
    read_optical_group_candidate(output / 'prepared-neutral.glb', receipt, expected_sha256=receipt['output_sha256'])
    report['export'] = _write(output, 'export.json', _json_bytes(receipt))
    report['model'] = {'path': 'prepared-neutral.glb', 'sha256': receipt['output_sha256'], 'purpose': 'smooth normal hypothesis control'}
    _write(output, 'report.json', _json_bytes(report))
    return report
