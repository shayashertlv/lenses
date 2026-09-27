"""Closed, typed operation catalog for canonical glasses editing."""
from copy import deepcopy

from .lens_appearance import LensAppearance, COLOR_SPACE, DENSITY_INTERPOLATION, VERTICAL_COORDINATE
from .segmented_astra_geometry import GEOMETRY_EDIT_SCHEMA
from .segmented_astra_transport import validate_plan, validate_tools_schema


def obj(properties, description=None):
    result = dict(type='object', properties=properties, required=list(properties), additionalProperties=False)
    if description:
        result['description'] = description
    return result


def number(low, high):
    return dict(type='number', minimum=low, maximum=high)


def rgb(high=1):
    return dict(type='array', items=number(0, high), minItems=3, maxItems=3)


def nullable(schema):
    return {'anyOf': [schema, {'type': 'null'}]}


IDS = dict(type='array', items=dict(type='integer', minimum=0), minItems=1, maxItems=256)
TEXT = dict(type='string', minLength=1, maxLength=4000)
APPEARANCE = obj({
    'normal_reflectance_rgb': rgb(), 'refractive_index': number(1, 3), 'roughness': number(0, 1),
    'optical_density_keyframes': dict(type='array', minItems=1, maxItems=8, items=obj({
        'v': number(0, 1), 'optical_density_rgb': rgb(12)})),
    'angular_reflectance_keyframes': nullable(dict(type='array', minItems=2, maxItems=8, items=obj({
        'angle_degrees': number(0, 90), 'reflectance_rgb': rgb()}))),
    'rear_reflection_fraction_rgb': nullable(rgb()),
}, 'Scene-linear RGB. Density 0 is clear; larger density darkens. Density keys begin at v=0 and cover v=1 unless constant. Angular keys cover 0..90; first equals normal reflectance. Rear fraction affects only nontransmitted energy; null uses default reciprocal response.')


def operation(name, properties, description):
    return obj({'operation': dict(type='string', enum=[name]), **properties}, description)


def _geometry():
    rows = deepcopy(GEOMETRY_EDIT_SCHEMA['oneOf'])
    def normalize(value):
        if isinstance(value, dict):
            value.pop('uniqueItems', None)
            if 'const' in value:
                value.update(type='string', enum=[value.pop('const')])
            for child in value.values():
                normalize(child)
        elif isinstance(value, list):
            for child in value:
                normalize(child)
    normalize(rows)
    for row in rows:
        row['description'] = 'Bounded canonical geometry edit in metres. Preserves UV/indices and part IDs. Contact/collision guards may refuse it. Select connected parts together for rigid edits; local bends must avoid seams.'
        if 'offset_m' in row['properties']:
            row['properties']['offset_m']['items'] = number(-.003, .003)
            row['properties']['offset_m']['description'] = 'Nonzero vector with Euclidean norm at most 0.003m. For local_bend, norm(offset)/radius must be at most 0.2038.'
    return rows


TOOLS_SCHEMA = obj({
    'note': TEXT,
    'operations': dict(type='array', minItems=1, maxItems=6, items={'anyOf': [
        *_geometry(),
        operation('frame_material', {'part_ids': IDS, 'base_color_linear_rgb': nullable(rgb()),
            'roughness': nullable(number(.04, 1)), 'metallic': nullable(number(0, 1))},
            'Opaque parts only. Non-null factors replace existing factors and multiply retained textures; this does not remove baked highlights or repaint texture pixels.'),
        operation('optical_appearance', {'group_ids': dict(type='array', items=dict(type='string'), minItems=1, maxItems=32),
            'appearance': APPEARANCE}, 'Replace complete optical descriptor for selected existing groups; clear, tint, gradient and angular/front/rear reflection are supported.'),
        operation('normal_policy', {'policy': dict(type='string', enum=['preserve', 'smooth'])},
            'Rebuild optical normals using original source or guarded smooth-surface fit. Geometry and UV remain unchanged; unsupported smooth fits retain original normals and report why.'),
        operation('group_membership', {'group_id': dict(type='string'), 'part_ids': IDS},
            'Replace members of one existing optical group. Whole parts only, disjoint groups, nonempty lenses. Does not split triangles or decide semantics; inspect ambiguous frame/edge hardware first.'),
        operation('inspect', {'part_ids': IDS, 'padding_fraction': number(.02, .6)},
            'Request labelled source geometry and close-up crops in actual-AR views for these parts. Run alone; consumes no mutation.'),
        operation('restore', {'revision_id': dict(type='string', pattern='^r[0-9]{4}$')},
            'Restore an exact available checkpoint including geometry, groups, normals and descriptors. Run alone.'),
        operation('finish', {'verdict': dict(type='string', enum=['review_ready', 'best_effort']), 'summary': TEXT},
            'Finish only after inspecting current post-edit renders. Run alone. Host still requires human review; this never certifies product accuracy.'),
    ]})
})
validate_tools_schema(TOOLS_SCHEMA)


def appearance(value):
    result = dict(schema_version=1, color_space=COLOR_SPACE, density_interpolation=DENSITY_INTERPOLATION,
                  vertical_coordinate=VERTICAL_COORDINATE, **value)
    if result.get('rear_reflection_fraction_rgb') is None:
        result.pop('rear_reflection_fraction_rgb', None)
    return LensAppearance.from_dict(result).to_dict()


def validate(value):
    validate_plan(value, TOOLS_SCHEMA)
    rows = value['operations']
    if len(rows) != 1 and any(r['operation'] in ('inspect', 'restore', 'finish') for r in rows):
        raise ValueError('inspect, restore and finish must run alone after reviewing current observations')
    for row in rows:
        for name in ('part_ids', 'group_ids'):
            if name in row and len(set(row[name])) != len(row[name]):
                raise ValueError('Duplicate selection IDs')
        if row['operation'] == 'optical_appearance':
            appearance(row['appearance'])
        if row['operation'] == 'frame_material' and all(row[k] is None for k in ('base_color_linear_rgb', 'roughness', 'metallic')):
            raise ValueError('Supply at least one material factor')
    return value
