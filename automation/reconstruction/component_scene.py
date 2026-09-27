"""Load a component inventory back into one world-space scene with labels.

The inventory stage records exact local-position components per primitive
instance. This loader rebuilds the complete world mesh from the captured
source bytes and those labels, verifies the recorded array hashes, and returns
the canonical component table used by projection, grouping and partitioning.
No component is dropped, merged or given a semantic label here.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import numpy as np

from .mesh import TriangleMesh
from .mesh_components import component_face_labels
from .partition_glb import inspect_partition_source


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _array_record(value):
    value = np.asarray(value)
    return {'sha256': _sha(np.ascontiguousarray(value).tobytes()), 'dtype': value.dtype.str,
            'shape': list(value.shape), 'encoding': 'C_order_numeric_array_bytes'}


def _check_array(value, record):
    if _array_record(value) != {key: record[key] for key in ('sha256', 'dtype', 'shape', 'encoding')}:
        raise ValueError('Inventory array differs from its recorded snapshot')


def load_component_scene(report_path: Path, *, recompute_labels=True) -> dict:
    """Return mesh, labels, component table, per-primitive labels and extent."""
    report_path = Path(report_path).resolve()
    inventory = json.loads(report_path.read_bytes())
    if (inventory.get('schema_version') != 1 or inventory.get('status') != 'component_inventory_complete'
            or inventory.get('coordinate_space') != 'source_local' or inventory.get('accepted') is not False):
        raise ValueError('A complete component inventory report is required')
    folder = report_path.parent
    raw = (folder/inventory['source_snapshot']['path']).read_bytes()
    if _sha(raw) != inventory['source_snapshot']['sha256'] or _sha(raw) != inventory['source_sha256']:
        raise ValueError('Inventory source snapshot differs from its recorded hash')
    label_raw = (folder/inventory['labels']['path']).read_bytes()
    if _sha(label_raw) != inventory['labels']['sha256']:
        raise ValueError('Inventory labels differ from their recorded hash')
    source = inspect_partition_source(raw)
    if len(source['instances']) != len(inventory['primitives']):
        raise ValueError('Inventory primitive coverage differs from the captured source')
    vertices, faces, face_components, table, primitive_labels = [], [], [], [], {}
    vertex_offset = face_offset = component_offset = 0
    with np.load(io.BytesIO(label_raw), allow_pickle=False) as saved:
        for ordinal, (instance, row) in enumerate(zip(source['instances'], inventory['primitives'], strict=True)):
            points, indices = instance['positions'], instance['indices']
            if (row['source_binding'] != instance['source_binding'] or row['source_vertex_count'] != len(points)
                    or row['source_face_count'] != len(indices) or row['source_world_matrix'] != instance['world_matrix'].tolist()):
                raise ValueError('Inventory binding or geometry counts differ from the captured source')
            _check_array(points, row['positions']); _check_array(indices, row['indices'])
            labels = saved[row['labels']['npz_key']]
            _check_array(labels, row['labels'])
            if recompute_labels and not np.array_equal(labels, component_face_labels(points, indices, connectivity=inventory['connectivity'])):
                raise ValueError('Inventory labels disagree with exact connectivity')
            count = int(labels.max())+1 if len(labels) else 0
            if count != row['component_count'] or len(row['components']) != count:
                raise ValueError('Inventory component coverage differs')
            binding = tuple(row['source_binding'][k] for k in ('node_index', 'mesh_index', 'primitive_index'))
            primitive_labels[binding] = labels.astype(np.int64)
            for local_id, component in enumerate(row['components']):
                table.append({'component_id': component_offset+local_id, 'source_primitive_ordinal': ordinal,
                              'source_binding': dict(row['source_binding']), 'local_component_id': local_id,
                              'source_face_count': component['face_count'], 'source_global_face_offset': face_offset,
                              'first_source_local_face': component['first_source_face'],
                              'local_bounds': component['local_bounds'], 'semantic_identity': 'not_inferred'})
            matrix = instance['world_matrix']
            vertices.append(points @ matrix[:3, :3].T + matrix[:3, 3]); faces.append(indices+vertex_offset)
            face_components.append(labels+component_offset)
            vertex_offset += len(points); face_offset += len(indices); component_offset += count
    if face_offset != inventory['source_face_count'] or component_offset != inventory['component_count']:
        raise ValueError('Inventory aggregate coverage differs')
    mesh = TriangleMesh(np.concatenate(vertices), np.concatenate(faces), [])
    labels = np.concatenate(face_components).astype(np.int64)
    used = mesh.vertices[np.unique(mesh.faces)]
    extent = float(np.ptp(used, axis=0).max())
    if not np.isfinite(extent) or extent <= 0:
        raise ValueError('Source referenced vertices need a positive reference extent')
    return {'mesh': mesh, 'face_components': labels, 'component_table': table, 'primitive_labels': primitive_labels,
            'source_sha256': inventory['source_sha256'], 'source_raw': raw, 'reference_extent': extent,
            'referenced_world_bounds': [used.min(axis=0).tolist(), used.max(axis=0).tolist()],
            'inventory_report_sha256': _sha(report_path.read_bytes())}
