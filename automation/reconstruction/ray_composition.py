"""Per-pixel photographed-ray contents under one physical-group hypothesis.

Every optical-candidate group contributes its nearest event; every component
whose group is non-optical is treated as an opaque stop; unresolved groups are
kept as a third, separate class rather than being folded into either. The map
says what a photographed pixel would contain if the hypothesis and the frozen
camera were right: frame in front, a lens over background, a lens over opaque
rear geometry, a lens over an unresolved piece, stacked lenses, or nothing.
It does not measure a color, verify the hypothesis or identify a material.
"""
from __future__ import annotations

import numpy as np

from .physical_groups import ROLES


CLASSES = ('background', 'opaque_only', 'frame_in_front', 'lens_over_background', 'lens_over_opaque',
           'lens_over_unresolved', 'stacked_optical', 'unresolved_in_front', 'unresolved_only', 'depth_tie')
CODES = {name: index for index, name in enumerate(CLASSES)}
CLEAN_TRANSMISSION = ('lens_over_background',)
REAR_CONTENT = ('lens_over_opaque', 'lens_over_unresolved')
EXCLUDED = ('frame_in_front', 'stacked_optical', 'unresolved_in_front', 'depth_tie')


def _nearest(view, members):
    depth = np.full(view['pixels'], np.inf)
    for m in members:
        p = view['component_pixels'][m]
        if len(p):
            np.minimum.at(depth, p, view['component_depths'][m])
    return depth


def compose_view_rays(view, groups, *, separation=0.0):
    """Classify every pixel of one view under explicit group roles.

    ``view`` has ``shape``, ``component_pixels``, ``component_depths`` and
    ``interpretations`` (each with ``id``, flat boolean ``mask``/``known``
    arrays and ``pixels``). ``groups`` list ``{'group_id', 'members', 'role'}``
    covering every component exactly once. Depths share one unit and
    ``separation`` is the tie tolerance in that unit. Ties between an optical
    event and any stop, or between two optical groups, are their own class.
    """
    if isinstance(separation, bool) or not np.isfinite(separation) or separation < 0:
        raise ValueError('Nonnegative finite depth separation required')
    count = len(view['component_pixels'])
    if len(view['component_depths']) != count:
        raise ValueError('View pixel and depth inventories differ')
    shape = tuple(view['shape']); pixels = shape[0]*shape[1]
    if view.get('pixels', pixels) != pixels:
        raise ValueError('View pixel count contradicts its shape')
    view = {**view, 'pixels': pixels}
    owner = np.full(count, -1, np.int64)
    optical, opaque, unresolved = [], [], []
    for index, group in enumerate(groups):
        if not isinstance(group, dict) or set(group) < {'group_id', 'members', 'role'} or group['role'] not in ROLES:
            raise ValueError('Each group needs group_id, members and a declared role')
        members = group['members']
        if any(type(m) is not int or not 0 <= m < count or owner[m] >= 0 for m in members):
            raise ValueError('Group members must cover components at most once')
        owner[members] = index
        (optical if group['role'] == 'optical_candidate' else opaque if group['role'] == 'non_optical_evidence'
         else unresolved).append(index)
    if np.any(owner < 0):
        raise ValueError('Every component must belong to exactly one group')
    optical_depths = np.stack([_nearest(view, groups[g]['members']) for g in optical]) if optical else np.full((0, pixels), np.inf)
    opaque_depth = _nearest(view, [m for g in opaque for m in groups[g]['members']])
    unresolved_depth = _nearest(view, [m for g in unresolved for m in groups[g]['members']])
    order = np.argsort(optical_depths, axis=0, kind='stable') if optical else np.empty((0, pixels), np.int64)
    sorted_optical = np.take_along_axis(optical_depths, order, axis=0) if optical else optical_depths
    nearest_optical = sorted_optical[0] if optical else np.full(pixels, np.inf)
    nearest_group = np.full(pixels, -1, np.int64)
    if optical:
        first = np.asarray(optical)[order[0]]
        nearest_group[np.isfinite(nearest_optical)] = first[np.isfinite(nearest_optical)]
    has_optical, has_opaque, has_unresolved = np.isfinite(nearest_optical), np.isfinite(opaque_depth), np.isfinite(unresolved_depth)
    stop = np.minimum(opaque_depth, unresolved_depth)
    layers = np.sum(np.isfinite(sorted_optical) & (sorted_optical < opaque_depth[None]-separation), axis=0) if optical else np.zeros(pixels, np.int64)
    tie = _within(nearest_optical, opaque_depth, separation)
    tie |= _within(nearest_optical, unresolved_depth, separation)
    tie |= ~has_optical & _within(opaque_depth, unresolved_depth, separation)
    if len(sorted_optical) > 1:
        tie |= _within(sorted_optical[0], sorted_optical[1], separation)
    classes = np.full(pixels, CODES['background'], np.int8)
    nothing = ~has_optical & ~has_opaque & ~has_unresolved
    opaque_only = ~has_optical & has_opaque & (opaque_depth <= unresolved_depth)
    unresolved_only = ~has_optical & has_unresolved & (unresolved_depth < opaque_depth)
    optical_first = has_optical & (nearest_optical < stop)
    frame_front = has_optical & has_opaque & (opaque_depth < nearest_optical) & (opaque_depth <= unresolved_depth)
    unresolved_front = has_optical & has_unresolved & (unresolved_depth < nearest_optical) & (unresolved_depth < opaque_depth)
    classes[opaque_only] = CODES['opaque_only']
    classes[unresolved_only] = CODES['unresolved_only']
    classes[frame_front] = CODES['frame_in_front']
    classes[unresolved_front] = CODES['unresolved_in_front']
    behind_opaque = optical_first & has_opaque & (opaque_depth <= unresolved_depth)
    behind_unresolved = optical_first & has_unresolved & (unresolved_depth < opaque_depth)
    classes[optical_first & ~has_opaque & ~has_unresolved] = CODES['lens_over_background']
    classes[behind_opaque] = CODES['lens_over_opaque']
    classes[behind_unresolved] = CODES['lens_over_unresolved']
    classes[optical_first & (layers >= 2)] = CODES['stacked_optical']
    classes[tie] = CODES['depth_tie']
    classes[nothing] = CODES['background']
    counts = {name: int(np.count_nonzero(classes == code)) for name, code in CODES.items()}
    per_interpretation = []
    for item in view.get('interpretations', []):
        mask, known = np.asarray(item['mask']).ravel(), np.asarray(item['known']).ravel()
        inside = {name: int(np.count_nonzero(mask & (classes == code))) for name, code in CODES.items()}
        aperture = int(mask.sum())
        optical_codes = [CODES[n] for n in (*CLEAN_TRANSMISSION, *REAR_CONTENT, 'stacked_optical')]
        inside_optical = sum(inside[n] for n in (*CLEAN_TRANSMISSION, *REAR_CONTENT, 'stacked_optical'))
        outside_optical = int(np.count_nonzero(known & ~mask & np.isin(classes, optical_codes)))
        per_interpretation.append({'interpretation_id': item['id'], 'aperture_pixels': aperture,
                                   'classes_inside_aperture': inside,
                                   'aperture_without_geometry_fraction': _fraction(inside['background'], aperture),
                                   'aperture_on_opaque_only_fraction': _fraction(inside['opaque_only'], aperture),
                                   'clean_transmission_fraction': _fraction(sum(inside[n] for n in CLEAN_TRANSMISSION), aperture),
                                   'rear_content_fraction': _fraction(sum(inside[n] for n in REAR_CONTENT), aperture),
                                   'excluded_fraction': _fraction(sum(inside[n] for n in EXCLUDED), aperture),
                                   'optical_inside_aperture_known_pixels': inside_optical,
                                   'optical_outside_aperture_known_pixels': outside_optical,
                                   # Share of the optical groups' known footprint that the photo
                                   # proposal calls non-lens: pixels the hypothesis would sample wrongly.
                                   'contamination_fraction': _fraction(outside_optical, outside_optical+inside_optical)})
    report = {'schema_version': 1, 'method': 'group_hypothesis_ray_composition_v1', 'accepted': False,
              'quality_verdict': 'unmeasured', 'view_id': view.get('id'), 'shape': list(shape),
              'depth_separation': float(separation), 'legend': dict(CODES),
              'group_roles': [{'group_id': g['group_id'], 'role': g['role'], 'member_count': len(g['members'])} for g in groups],
              'optical_group_count': len(optical), 'class_counts': counts,
              'maximum_optical_layers': int(layers.max()) if pixels else 0,
              'interpretations': per_interpretation,
              'sampling_scope': {'clean_transmission': list(CLEAN_TRANSMISSION), 'rear_content_unknown_color': list(REAR_CONTENT),
                                 'excluded_from_single_interface_fit': list(EXCLUDED)},
              'limitations': ['Non-optical groups are treated as opaque stops; their actual materials are not simulated.',
                              'Unresolved groups are neither transmitted nor opaque here; pixels behind them stay a separate class.',
                              'Aperture pixels without geometry indicate mask, camera, articulation or missing-geometry error, not lens color.',
                              'Pixel-center depths under frozen cameras; no antialiasing, refraction or GPU parity.']}
    return {'report': report, 'class_map': classes.reshape(shape), 'nearest_optical_group': nearest_group.reshape(shape),
            'optical_depth': nearest_optical.reshape(shape), 'opaque_depth': opaque_depth.reshape(shape),
            'unresolved_depth': unresolved_depth.reshape(shape), 'optical_layers': layers.reshape(shape)}


def _fraction(numerator, denominator):
    return float(numerator/denominator) if denominator else None


def _within(left, right, separation):
    """Finite pairs whose depth difference is at most ``separation``; no inf arithmetic."""
    both = np.isfinite(left) & np.isfinite(right)
    delta = np.zeros(left.shape)
    np.subtract(left, right, out=delta, where=both)
    return both & (np.abs(delta) <= separation)
