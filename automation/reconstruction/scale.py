"""Physical scale and origin from a stated frame width: positions to metres, bridge at the origin.

A provider generation arrives in arbitrary units (a Meshy split is about 1.9
units across), while the AR runtime places eyewear in metres and fits it to
the wearer from the model's own geometry. This stage multiplies every vertex
position, node translation and matrix translation by one uniform factor so the
model's lateral (X) extent equals the stated ``frame_width`` in metres. Nothing
else changes: normals, UVs, colours, materials, node hierarchy and topology
keep their bytes, and the source is retained unchanged beside the output.

Assumptions, all recorded in the receipt: the lateral axis is X in the source
frame (the refinement's front camera looks along Z), the stated width spans the
model's full X extent (rims and hinges included, temples folded or not), and
the width is product data supplied by the caller, never inferred from pixels.
Without a stated frame width the stage applies nothing and says so.

After the scale the model is placed the way the AR runtime expects its assets
(``ar/src/eyewear/external.ts``: real-size metres, the bridge underside at the
origin, the front toward +Z). A provider generation arrives centred on its
bounding box, which puts the frame front several centimetres in front of the
origin; the runtime measures the front's width under the raw face pose
before any fitting and, with the front that much closer to the camera, the
widest product exceeded the clarity limit and never settled. The bridge is
found in a narrow central column of the front: runs of material along Y,
split at gaps of two millimetres; the lowest run that is thin (under 45% of
the front's height) and at the front (its median depth within 8 mm of the
column's front-most point) is the bridge, its lowest point the underside and
its median depth the origin's Z. A continuous column (a shield lens with its
nose piece) has no such run and falls back to the front slab's vertical
centre, 2.5 mm behind its front-most point; the receipt says which. The
translation is baked into the positions when no node carries a transform
(the runtime refuses lens meshes with transforms), else applied to the root
nodes. On the five archived assets built to the contract the rule lands
within a few millimetres of their origin.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct

import numpy as np

from .mesh import load_glb_bytes


METHOD = 'stated_frame_width_x_extent_uniform_scale_v1'
PLACEMENT_METHOD = 'bridge_underside_origin_v1'
MINIMUM_WIDTH_MM, MAXIMUM_WIDTH_MM = 60., 250.
# Placement geometry, in millimetres of the scaled model.
BAND_FRACTION_OF_WIDTH, BAND_MINIMUM_MM = .04, 3.
COLUMN_DEPTH_MM, RUN_GAP_MM, BRIDGE_DEPTH_MM, BRIDGE_HEIGHT_FRACTION, FALLBACK_SETBACK_MM = 30., 2., 8., .45, 2.5


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _chunks(raw: bytes):
    if len(raw) < 20 or struct.unpack_from('<4sII', raw) != (b'glTF', 2, len(raw)):
        raise ValueError('Expected an intact GLB version 2')
    offset, chunks = 12, []
    while offset < len(raw):
        size, kind = struct.unpack_from('<II', raw, offset)
        offset += 8
        if size % 4 or offset + size > len(raw):
            raise ValueError('Invalid GLB chunk extent/alignment')
        chunks.append((kind, raw[offset:offset + size]))
        offset += size
    if [c[0] for c in chunks] != [0x4E4F534A, 0x004E4942]:
        raise ValueError('Expected a self-contained JSON/BIN GLB')
    return json.loads(chunks[0][1]), bytearray(chunks[1][1])


def _pack(doc: dict, binary: bytes) -> bytes:
    text = json.dumps(doc, separators=(',', ':'), allow_nan=False).encode('utf-8')
    text += b' ' * (-len(text) % 4)
    binary = bytes(binary) + b'\0' * (-len(binary) % 4)
    body = struct.pack('<II', len(text), 0x4E4F534A) + text + struct.pack('<II', len(binary), 0x004E4942) + binary
    return struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body


def bridge_origin(vertices_m) -> tuple[np.ndarray, dict]:
    """The AR origin of a scaled model in metres: lateral centre, bridge underside, bridge depth; with its receipt."""
    v = np.asarray(vertices_m, dtype=float) * 1000.
    if v.ndim != 2 or v.shape[1] != 3 or not len(v) or not np.isfinite(v).all():
        raise ValueError('Finite Nx3 vertices are required')
    lower, upper = v.min(axis=0), v.max(axis=0)
    width, height = upper[0] - lower[0], upper[1] - lower[1]
    x_center = (lower[0] + upper[0]) / 2
    band = np.abs(v[:, 0] - x_center) <= max(BAND_MINIMUM_MM, BAND_FRACTION_OF_WIDTH * width)
    column = v[band]
    runs, candidates = [], []
    if len(column):
        column = column[column[:, 2] >= column[:, 2].max() - COLUMN_DEPTH_MM]
        front_z = column[:, 2].max()
        ys = column[:, 1]
        bottom = float(np.floor(ys.min()))
        occupied = np.zeros(int(np.ceil(ys.max() - bottom)) + 1, bool)
        occupied[np.floor(ys - bottom).astype(int)] = True
        start = None
        for i, filled in enumerate(occupied):
            if filled and start is None:
                start = i
            elif not filled and start is not None:
                gap = 1
                while i + gap < len(occupied) and not occupied[i + gap]:
                    gap += 1
                if gap >= RUN_GAP_MM or i + gap >= len(occupied):
                    runs.append((start, i)); start = None
        if start is not None:
            runs.append((start, len(occupied)))
        for first, stop in runs:
            points = column[(ys >= bottom + first) & (ys < bottom + stop)]
            if not len(points):
                continue
            run_height = float(points[:, 1].max() - points[:, 1].min())
            depth = float(np.median(points[:, 2]))
            record = {'y_from_mm': float(points[:, 1].min()), 'y_to_mm': float(points[:, 1].max()), 'height_mm': run_height,
                      'median_z_mm': depth, 'vertices': int(len(points)),
                      'thin': bool(run_height <= BRIDGE_HEIGHT_FRACTION * height), 'at_front': bool(depth >= front_z - BRIDGE_DEPTH_MM)}
            candidates.append(record)
    bridges = [c for c in candidates if c['thin'] and c['at_front']]
    if bridges:
        bridge = min(bridges, key=lambda c: c['y_from_mm'])
        origin = np.array([x_center, bridge['y_from_mm'], bridge['median_z_mm']])
        how = 'bridge_underside'
    else:
        slab = v[v[:, 2] >= upper[2] - COLUMN_DEPTH_MM]
        origin = np.array([x_center, (slab[:, 1].max() + slab[:, 1].min()) / 2, upper[2] - FALLBACK_SETBACK_MM])
        how = 'front_slab_center'
    receipt = {'method': PLACEMENT_METHOD, 'origin_mm_before_placement': [float(x) for x in origin], 'rule': how,
               'central_column_vertices': int(len(column)), 'column_runs': candidates,
               'parameters_mm': {'band_half_width': float(max(BAND_MINIMUM_MM, BAND_FRACTION_OF_WIDTH * width)), 'column_depth': COLUMN_DEPTH_MM,
                                 'run_gap': RUN_GAP_MM, 'bridge_depth': BRIDGE_DEPTH_MM, 'bridge_height_fraction': BRIDGE_HEIGHT_FRACTION,
                                 'fallback_setback': FALLBACK_SETBACK_MM},
               'convention': 'ar/src/eyewear/external.ts: real-size metres, bridge underside at the origin, front toward +Z',
               'limitations': ['The bridge is inferred from geometry in a central column; a design whose bridge is not the lowest thin '
                               'front run there (an ornament below it) would place the origin on that piece.',
                               'The front is assumed to face +Z, as the refinement front camera does.']}
    return origin / 1000., receipt


def _translate(doc, binary, offset_m):
    """Bake a world translation into every POSITION accessor (identity nodes) or into the root nodes (transformed nodes)."""
    nodes = doc.get('nodes', [])
    transformed = any(any(k in n for k in ('matrix', 'rotation', 'scale', 'translation')) for n in nodes)
    offset = np.asarray(offset_m, dtype=float)
    if transformed:
        for index in doc['scenes'][doc.get('scene', 0)].get('nodes', []):
            node = nodes[index]
            if 'matrix' in node:
                matrix = list(node['matrix'])
                for k, o in zip((12, 13, 14), offset):
                    matrix[k] = float(matrix[k]) + float(o)
                node['matrix'] = matrix
            else:
                node['translation'] = [float(t) + float(o) for t, o in zip(node.get('translation', [0., 0., 0.]), offset)]
        return {'via': 'root_node_translation', 'root_nodes': len(doc['scenes'][doc.get('scene', 0)].get('nodes', []))}
    views, accessors, done, touched = doc.get('bufferViews', []), doc.get('accessors', []), set(), 0
    delta = offset.astype('<f4')
    for mesh_record in doc.get('meshes', []):
        for primitive in mesh_record.get('primitives', []):
            index = primitive.get('attributes', {}).get('POSITION')
            if index is None or index in done:
                continue
            accessor = accessors[index]
            view = views[accessor['bufferView']]
            stride = view.get('byteStride', 12)
            start = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
            count = accessor['count']
            for i in range(count):
                at = start + i * stride
                binary[at:at + 12] = (np.frombuffer(bytes(binary[at:at + 12]), dtype='<f4') + delta).astype('<f4').tobytes()
            block = np.asarray([np.frombuffer(bytes(binary[start + i * stride:start + i * stride + 12]), dtype='<f4') for i in range(count)])
            accessor['min'] = [float(x) for x in block.min(axis=0)]
            accessor['max'] = [float(x) for x in block.max(axis=0)]
            done.add(index); touched += count
    return {'via': 'baked_into_positions', 'translated_position_accessors': sorted(done), 'translated_vertices': touched}


def scale_glb(raw: bytes, *, frame_width_mm: float) -> tuple[bytes, dict]:
    """Return the uniformly scaled GLB bytes and a receipt of the factor and its basis."""
    if isinstance(frame_width_mm, bool) or not isinstance(frame_width_mm, (int, float)) or not np.isfinite(frame_width_mm):
        raise ValueError('frame_width_mm must be a finite number')
    if not MINIMUM_WIDTH_MM <= frame_width_mm <= MAXIMUM_WIDTH_MM:
        raise ValueError(f'frame_width_mm must lie within {MINIMUM_WIDTH_MM}..{MAXIMUM_WIDTH_MM} mm')
    mesh = load_glb_bytes(raw)
    vertices = np.asarray(mesh.vertices, dtype=float)
    lower, upper = vertices.min(axis=0), vertices.max(axis=0)
    extents = upper - lower
    if not np.isfinite(extents).all() or extents[0] <= 0:
        raise ValueError('The model has no finite lateral extent to scale')
    factor = (frame_width_mm / 1000.) / float(extents[0])
    doc, binary = _chunks(raw)
    if len(doc.get('buffers', [])) != 1 or 'uri' in doc['buffers'][0]:
        raise ValueError('Expected one embedded buffer')
    views, accessors = doc.get('bufferViews', []), doc.get('accessors', [])
    scaled_accessors, touched = set(), 0
    for mesh_record in doc.get('meshes', []):
        for primitive in mesh_record.get('primitives', []):
            index = primitive.get('attributes', {}).get('POSITION')
            if index is None or index in scaled_accessors:
                continue
            accessor = accessors[index]
            if (accessor.get('componentType') != 5126 or accessor.get('type') != 'VEC3' or 'sparse' in accessor
                    or 'bufferView' not in accessor or accessor.get('normalized')):
                raise ValueError('POSITION accessors must be dense float32 VEC3 buffer views')
            view = views[accessor['bufferView']]
            stride = view.get('byteStride', 12)
            if stride < 12:
                raise ValueError('Invalid POSITION stride')
            start = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
            count = accessor['count']
            for i in range(count):
                at = start + i * stride
                values = np.frombuffer(bytes(binary[at:at + 12]), dtype='<f4') * factor
                binary[at:at + 12] = values.astype('<f4').tobytes()
            block = np.asarray([np.frombuffer(bytes(binary[start + i * stride:start + i * stride + 12]), dtype='<f4') for i in range(count)])
            accessor['min'] = [float(v) for v in block.min(axis=0)]
            accessor['max'] = [float(v) for v in block.max(axis=0)]
            scaled_accessors.add(index)
            touched += count
    for node in doc.get('nodes', []):
        if 'translation' in node:
            node['translation'] = [float(v) * factor for v in node['translation']]
        if 'matrix' in node:
            matrix = list(node['matrix'])
            for k in (12, 13, 14):
                matrix[k] = float(matrix[k]) * factor
            node['matrix'] = matrix
    doc.setdefault('extras', {})['lensesPhysicalScale'] = {
        'method': METHOD, 'factor_source_units_to_meters': factor, 'frame_width_mm': float(frame_width_mm),
        'lateral_axis': 'X', 'basis': 'stated frame width spans the source X extent; unverified against the product'}
    origin, placement = bridge_origin(load_glb_bytes(_pack(doc, binary)).vertices)
    placement.update(_translate(doc, binary, -origin))
    doc['extras']['lensesPlacement'] = {k: placement[k] for k in ('method', 'origin_mm_before_placement', 'rule', 'via', 'convention')}
    output = _pack(doc, binary)
    placed_origin, _ = bridge_origin(load_glb_bytes(output).vertices)
    if not np.allclose(placed_origin, 0., atol=1e-6):
        raise ValueError('Placed model does not put the bridge at the origin')
    placement['origin_mm_after_placement'] = [float(x) for x in placed_origin * 1000.]
    check = np.asarray(load_glb_bytes(output).vertices, dtype=float)
    achieved = float(check[:, 0].max() - check[:, 0].min())
    if not np.isclose(achieved, frame_width_mm / 1000., rtol=1e-5, atol=1e-7):
        raise ValueError('Scaled model does not reach the stated width')
    receipt = {'schema_version': 1, 'method': METHOD, 'source_sha256': _sha(raw), 'output_sha256': _sha(output),
               'frame_width_mm': float(frame_width_mm), 'factor_source_units_to_meters': factor,
               'source_extent_units_xyz': [float(v) for v in extents], 'output_extent_meters_xyz': [float(v) for v in (check.max(axis=0) - check.min(axis=0))],
               'scaled_position_accessors': sorted(scaled_accessors), 'scaled_vertices': touched, 'placement': placement,
               'lateral_axis_assumption': 'X is lateral in the source frame; the refinement front camera looks along Z',
               'unchanged': ['normals', 'uvs', 'colours', 'materials', 'indices', 'node hierarchy'],
               'origin': 'bridge underside at the origin, front toward +Z (the AR asset contract); see placement',
               'accepted': False, 'quality_verdict': 'unmeasured',
               'limitations': ['The stated width is caller-supplied product data, not measured from the photographs.',
                               'The X extent includes whatever the model has at its sides (hinges, folded temples); a mismatch scales the whole model.']}
    return output, receipt


def run_scale_stage(model: Path, dimensions_mm: dict | None, output: Path) -> dict:
    """Write ``scaled.glb`` and ``report.json``; without a frame width, report that nothing applied."""
    model, output = Path(model).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw = model.read_bytes()
    width = (dimensions_mm or {}).get('frame_width')
    report = {'schema_version': 1, 'method': METHOD, 'source': {'path': str(model), 'sha256': _sha(raw)},
              'supplied_dimensions_mm': dict(dimensions_mm or {}), 'accepted': False, 'quality_verdict': 'unmeasured'}
    if width is None:
        report.update(status='not_applied', application='not_supplied' if not dimensions_mm else 'supplied_without_frame_width_unapplied',
                      model=None, reason='No stated frame width; the model keeps its source units.')
    else:
        scaled, receipt = scale_glb(raw, frame_width_mm=float(width))
        (output / 'scaled.glb').write_bytes(scaled)
        report.update(status='scaled', application='frame_width_applied_as_uniform_scale_to_meters',
                      model={'path': 'scaled.glb', 'sha256': receipt['output_sha256'], 'bytes': len(scaled)}, receipt=receipt)
    (output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return report
