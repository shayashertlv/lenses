"""Shared numeric fixture payloads; no acceptance is inferred from their results."""
from copy import deepcopy
import numpy as np


def primitive(identifier='sheet', points=None, normals=None, faces=None, v=None, height=None):
    source = np.asarray(points if points is not None else [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], float)
    low, high = height if height is not None else (source[:, 1].min(), source[:, 1].max())
    return {'id': identifier, 'positions': source.astype('<f4'),
        'indices': np.asarray(faces if faces is not None else [[0, 1, 2]], dtype='<u4'),
        'normals': np.asarray(normals if normals is not None else np.tile([0., 0., 1.], (len(source), 1)), dtype='<f4'),
        'uv': np.column_stack((np.full(len(source), .5), (source[:, 1]-low)/(high-low) if v is None else v)).astype('<f4')}


def group(identifier='lens', members=None):
    return {'group_id': identifier, 'coordinate_frame_id': 'source-world', 'appearance_sha256': 'a'*64,
            'primitives': members if members is not None else [primitive()]}


def fixed_cases():
    cases = []
    def add(name, expected, groups, reason=None):
        cases.append({'id': name, 'expected': expected, 'groups': groups, 'reason': reason})
    add('ordinary', True, [group()])
    add('zero_corner', False, [group(members=[primitive(normals=[[0, 0, 0], [0, 0, 1], [0, 0, 1]])])])
    add('antiparallel_edge', False, [group(members=[primitive(normals=[[0, 0, 1], [0, 0, -2], [.25, 0, 1]])])], 'normal_field_contains_zero_on_edge')
    for reverse in (False, True):
        add('interior_'+str(reverse), False, [group(members=[primitive(normals=[[1, 0, 0], [0, 2, 0], [-4, -4, 0]],
            faces=[[2, 1, 0]] if reverse else None)])], 'normal_field_contains_zero_interior')
    obtuse = np.array([[1, 0, 0], [-.25, 1, 0], [-.25, .5, 0.]])
    add('valid_obtuse', True, [group(members=[primitive(normals=obtuse)])])
    add('valid_obtuse_duplicate', True, [group(members=[primitive(normals=obtuse),
        primitive('reverse', normals=obtuse*np.array([[-2], [-.5], [-4]]), faces=[[2, 1, 0]])])])
    add('tiny_rank3', True, [group(members=[primitive(normals=[[1, 0, 0], [0, 1, 0], [-1, -1, 2.**-100]])])])
    add('same_sign_rank1', True, [group(members=[primitive(normals=[[0, 0, -1], [0, 0, -2], [0, 0, -.5]])])])
    u = 2.**-23
    add('incompatible_shared_height', False, [group(members=[primitive(points=[[0, 1, 0], [1, 1+u, 0], [1, 1+3*u, 0], [0, 1+u, 0]],
        faces=[[0, 1, 2], [0, 2, 3]], v=[0, .15, 1, .59])])], 'intrinsic_V_does_not_match_group_wide_Y_height')
    off = primitive(points=[[0, 1+.3*u, 0], [1, 1+.51*u, 0], [1, 1+2.7*u, 0], [0, 1+1.49*u, 0]], faces=[[0, 1, 2], [0, 2, 3]])
    add('off_midpoint_common_height', True, [group(members=[off])])
    invalid = deepcopy(off); invalid['uv'][0, 1] = .1; invalid['uv'][1, 1] = 0
    add('unattained_endpoint', False, [group(members=[invalid])], 'group_height_requires_distinct_bottom_0_and_top_1')
    add('independent_position_uv_rounding', True, [group(members=[primitive(points=[[0, .100000003, 0], [1, .3456789123, 0], [0, .89999998, 0]])])])
    low = primitive('lower', points=[[0, 0, 0], [1, 0, 0], [0, .5, 0]], height=(0, 1))
    high = primitive('upper', points=[[2, .5, 0], [3, .5, 0], [2, 1, 0]], height=(0, 1))
    add('multipart_group_height', True, [group(members=[low, high])])
    wrong = deepcopy(low); wrong['uv'][2, 1] = 1
    add('independent_member_height', False, [group(members=[wrong, high])], 'intrinsic_V_does_not_match_group_wide_Y_height')
    points = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]]
    a = primitive('a', points=points, faces=[[0, 1, 2], [0, 2, 3]])
    b = primitive('b', points=points, faces=[[0, 1, 3], [1, 2, 3]])
    add('retessellated_constant', True, [group(members=[a, b])])
    a, b = deepcopy(a), deepcopy(b)
    a['normals'][0] = b['normals'][0] = [.1, 0, 1]
    add('retessellated_varying', False, [group(members=[a, b])], 'unproven_retessellated_varying_normal_field')
    add('cross_group_coincident', False, [group('a'), group('b')], 'cross_group_coincident_patch_undefined_order')
    a = primitive(points=[[0, 0, 0], [1, .5, 0], [0, 1, 0]])
    b = deepcopy(a); b['id'] = 'different-v'; b['uv'][1, 1] = .5+2.**-24
    add('exact_v_despite_height_enclosure', False, [group(members=[a, b])], 'conflicting_intrinsic_v_affine_fields')
    return cases


def serializable(cases):
    return [{**case, 'groups': [{**g, 'primitives': [{k: v.tolist() if isinstance(v, np.ndarray) else v
                for k, v in member.items()} for member in g['primitives']]} for g in case['groups']]} for case in cases]
