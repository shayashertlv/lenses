"""Source component inventories and explicit lossless face-partition artifacts.

Inventory is read-only geometry evidence. Partition compiles supplied complete
face sets; neither command assigns optical identities or changes materials.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import platform

import numpy as np

from .mesh_components import (
    CONNECTIVITY_MODES, DEFAULT_CAPACITY, component_face_labels,
)
from .partition_glb import (
    LIMITS, inspect_partition_source, partition_glb_bytes, verify_partition_glb_bytes,
)
from .prepare_optical_groups import _json_bytes, _ordinary_path, _parse, _write
from .refine_photos import implementation_manifest


MAXIMUM_RECORDED_COMPONENTS = 100_000


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _artifact(name, raw):
    return {'path': name, 'sha256': _sha(raw)}


def _array_record(values):
    return {'sha256': _sha(np.ascontiguousarray(values).tobytes()),
            'dtype': values.dtype.str, 'shape': list(values.shape),
            'encoding': 'C_order_numeric_array_bytes'}


def _preflight(model, output):
    model, output = _ordinary_path(model), _ordinary_path(output)
    if not model.is_file() or model == output or model.is_relative_to(output):
        raise ValueError('Source must be an ordinary file outside stage output')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError('Partition stage output must be new or empty')
    return model, output


def _guard(model, source_sha256, implementation, declaration_path=None, declaration_raw=None):
    try:
        unchanged = _sha(_ordinary_path(model).read_bytes()) == source_sha256
        if declaration_path is not None:
            unchanged = unchanged and _ordinary_path(declaration_path).read_bytes() == declaration_raw
    except OSError as error:
        raise ValueError('Source or declarations became unavailable during partition stage') from error
    if not unchanged:
        raise ValueError('Source or declarations changed during partition stage')
    if implementation_manifest() != implementation:
        raise ValueError('Implementation changed during partition stage')


def _emit(model, output, payloads, report, *, declaration_path=None, declaration_raw=None):
    # All geometry, declarations, resource checks and verification finish before
    # this first mutation. Recheck output emptiness against concurrent writes.
    _guard(model, report['source_sha256'], report['implementation'], declaration_path, declaration_raw)
    _preflight(model, output)
    output.mkdir(parents=True, exist_ok=True)
    written = []
    for name, raw in payloads:
        reference = _write(output, name, raw)
        if reference != _artifact(name, raw):
            raise ValueError('Partition artifact write receipt mismatch')
        written.append(reference)
    for reference in written:
        if _sha(_ordinary_path(output/reference['path']).read_bytes()) != reference['sha256']:
            raise ValueError('Partition artifact changed during stage')
    _guard(model, report['source_sha256'], report['implementation'], declaration_path, declaration_raw)
    _write(output, 'report.json', _json_bytes(report))
    return report


def _base_report(model, raw, implementation):
    return {'schema_version': 1, 'source': str(model), 'source_sha256': _sha(raw),
            'source_snapshot': _artifact('source.glb', raw),
            'implementation': implementation,
            'runtime': {'python_version': platform.python_version(),
                        'python_implementation': platform.python_implementation()},
            'semantic_identity': 'not_inferred', 'accepted': False,
            'quality_verdict': 'unmeasured', 'render_equivalence': 'unmeasured'}


def run_component_inventory(model, output, *, connectivity='exact_position_vertex',
                            capacity=DEFAULT_CAPACITY) -> dict:
    """Inventory every selected-scene primitive, including alpha-zero geometry.

    Arrays use the captured source's local coordinates. Complete per-face labels
    are retained in one NPZ, not exported as one GLB per component. Capacity or
    invalid-input errors raise before any output artifact is created. A changed
    input/code/artifact during writing leaves an incomplete directory without a
    terminal report; existing directories are never overwritten or resumed.
    """
    if not isinstance(connectivity, str) or connectivity not in CONNECTIVITY_MODES:
        raise ValueError('Unknown source component connectivity mode')
    model, output = _preflight(model, output)
    implementation = implementation_manifest()
    raw = model.read_bytes()
    source = inspect_partition_source(raw)
    rows, arrays, total_components = [], {}, 0
    for instance in source['instances']:
        positions, indices = instance['positions'], instance['indices']
        labels = component_face_labels(positions, indices, connectivity=connectivity, capacity=capacity)
        counts = np.bincount(labels)
        total_components += len(counts)
        if total_components > MAXIMUM_RECORDED_COMPONENTS:
            raise ValueError('Component inventory exceeds recorded-component capacity')
        first = np.full(len(counts), len(indices), dtype=np.int64)
        np.minimum.at(first, labels, np.arange(len(indices), dtype=np.int64))
        low, high = np.full((len(counts), 3), np.inf), np.full((len(counts), 3), -np.inf)
        # Only referenced vertices contribute to bounds; source attribute hashes
        # still bind all accessor entries, including unused vertices.
        for corner in range(3):
            points = positions[indices[:, corner]]
            np.minimum.at(low, labels, points)
            np.maximum.at(high, labels, points)
        binding = instance['source_binding']
        key = f"n{binding['node_index']}_m{binding['mesh_index']}_p{binding['primitive_index']}"
        arrays[key] = labels
        primitive = instance['primitive']
        rows.append({'source_binding': binding,
            'source_vertex_count': len(positions), 'source_face_count': len(indices),
            'referenced_vertex_count': int(np.unique(indices).size),
            'source_attribute_accessors': primitive['attributes'],
            'source_index_accessor': primitive.get('indices'),
            'source_material_index': primitive.get('material'),
            'source_world_matrix': instance['world_matrix'].tolist(),
            'positions': _array_record(positions), 'indices': _array_record(indices),
            'labels': {'npz_key': key, **_array_record(labels)},
            'component_count': len(counts),
            'components': [{'component_id': i, 'first_source_face': int(first[i]),
                            'face_count': int(counts[i]),
                            'local_bounds': [low[i].tolist(), high[i].tolist()]}
                           for i in range(len(counts))]})
    stream = io.BytesIO()
    np.savez_compressed(stream, **arrays)
    label_raw = stream.getvalue()
    report = {**_base_report(model, raw, implementation),
        'method': 'source_component_inventory_v1', 'status': 'component_inventory_complete',
        'selected_scene': source['selected_scene'], 'coordinate_space': 'source_local',
        'connectivity': connectivity,
        'limits': {'component_arrays': asdict(capacity),
                   'maximum_recorded_components': MAXIMUM_RECORDED_COMPONENTS,
                   'source_glb': dict(LIMITS)},
        'primitive_count': len(rows), 'source_face_count': sum(row['source_face_count'] for row in rows),
        'component_count': total_components, 'labels': _artifact('labels.npz', label_raw),
        'primitives': rows,
        'limitations': ['Connectivity is a source-face partition, not physical or optical identity.',
            'All selected-scene primitive instances are retained, including alpha-zero and unreferenced-attribute entries.',
            'Other scenes remain in the source snapshot but are not part of this selected-scene inventory.',
            'Exact local position equality does not weld geometry or use a distance tolerance.',
            'Duplicate and degenerate faces remain; topology does not establish optical thickness or material identity.']}
    return _emit(model, output, [('source.glb', raw), ('labels.npz', label_raw)], report)


def run_face_partition(model, output, *, declarations) -> dict:
    """Compile and verify explicit face partitions before writing any artifact.

    The declaration schema is exactly partition_glb's schema: schema_version,
    source_sha256, provenance and partitions. Dict inputs are captured as finite
    JSON; file inputs retain their exact original bytes in declarations.json.
    """
    model, output = _preflight(model, output)
    implementation = implementation_manifest()
    declaration_path = None
    if isinstance(declarations, dict):
        declaration_raw = _json_bytes(declarations)
    elif isinstance(declarations, (str, Path)):
        declaration_path = _ordinary_path(declarations)
        if not declaration_path.is_file() or declaration_path.is_relative_to(output):
            raise ValueError('Declarations must be an ordinary JSON file outside stage output')
        declaration_raw = declaration_path.read_bytes()
    else:
        raise ValueError('Declarations must be a dict or JSON path')
    declared = _parse(declaration_raw)
    raw = model.read_bytes()
    compiled, receipt = partition_glb_bytes(raw, declared)
    verification = verify_partition_glb_bytes(raw, compiled, receipt)
    receipt_raw = _json_bytes(receipt)
    report = {**_base_report(model, raw, implementation),
        'method': 'source_face_partition_stage_v1', 'status': 'face_partition_exported',
        'declarations': {**_artifact('declarations.json', declaration_raw),
                         'original_path': str(declaration_path) if declaration_path else None},
        'model': _artifact('partitioned.glb', compiled), 'receipt': _artifact('receipt.json', receipt_raw),
        'verification': verification, 'selected_scene': receipt['selected_scene'],
        'source_face_count': sum(row['source_face_count'] for row in receipt['primitives']),
        'source_primitive_count': len(receipt['primitives']),
        'output_primitive_count': receipt['output_primitives'],
        'limitations': list(receipt['limitations'])}
    payloads = [('source.glb', raw), ('declarations.json', declaration_raw),
                ('partitioned.glb', compiled), ('receipt.json', receipt_raw)]
    return _emit(model, output, payloads, report,
                 declaration_path=declaration_path, declaration_raw=declaration_raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    inventory = subparsers.add_parser('inventory', help='Record exact source components without changing the GLB')
    inventory.add_argument('--model', required=True, type=Path)
    inventory.add_argument('--output', required=True, type=Path)
    inventory.add_argument('--connectivity', choices=CONNECTIVITY_MODES, default='exact_position_vertex')
    partition = subparsers.add_parser('partition', help='Compile supplied complete source-face partitions')
    partition.add_argument('--model', required=True, type=Path)
    partition.add_argument('--output', required=True, type=Path)
    partition.add_argument('--declarations', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == 'inventory':
        report = run_component_inventory(args.model, args.output, connectivity=args.connectivity)
    else:
        report = run_face_partition(args.model, args.output, declarations=args.declarations)
    print(json.dumps({'status': report['status'], 'report': str(args.output.resolve()/'report.json'),
                      'accepted': False, 'semantic_identity': 'not_inferred'}, indent=2))
    return report


if __name__ == '__main__':
    main()
