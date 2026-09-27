"""Carry fitted cameras from an original candidate to its verified face partition.

A refinement report binds its cameras to one candidate SHA. A face partition
of that candidate changes bytes and primitive structure but not one world
triangle, so the same cameras and normalization describe it. This module
makes that transfer explicit: it verifies the partition receipt, reloads both
models through the same loader the region stage uses, checks that the world
triangle multisets are identical, and emits a derived report whose provenance
names the original SHA, the receipt and the check. Nothing is transferred by
part order, name or assumption.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import numpy as np

from .mesh import load_glb_bytes
from .partition_glb import verify_partition_glb_bytes


METHOD = 'verified_face_partition_camera_transfer_v1'


def _canonical_triangles(mesh):
    triangles = mesh.vertices[mesh.faces]
    if not np.isfinite(triangles).all():
        raise ValueError('Candidate geometry must be finite')
    # Sort the corners of each triangle, then the triangles, so winding and
    # emission order cannot hide or fake a geometric difference.
    flat = triangles.reshape(len(triangles), 3, 3)
    order = np.lexsort((flat[:, :, 2], flat[:, :, 1], flat[:, :, 0]), axis=1)
    sorted_corners = np.take_along_axis(flat, order[:, :, None], axis=1).reshape(len(flat), 9)
    rows = np.lexsort(sorted_corners.T[::-1])
    return sorted_corners[rows]


def transfer_cameras_to_partition(refinement: dict, source_raw: bytes, partitioned_raw: bytes,
                                  receipt: dict, *, partitioned_model_path: str) -> dict:
    """Return a refinement report rebound to the partitioned candidate.

    The result keeps every view, camera and the original normalization. Its
    ``source_sha256`` names the partitioned bytes because that is what the
    region and observation stages bind to; ``camera_transfer`` records the
    original SHA, the receipt digest and the geometric identity check.
    """
    if not isinstance(refinement, dict) or not isinstance(refinement.get('views'), list) or not refinement['views']:
        raise ValueError('A refinement report with fitted views is required')
    source_hash = hashlib.sha256(source_raw).hexdigest()
    # The same two bindings the region stage accepts: the report's original
    # source, or its exported nonregressing proposal (the retained candidate).
    if refinement.get('source_sha256') == source_hash:
        binding = 'original_source'
    elif (refinement.get('status') == 'proposal_exported' and refinement.get('rerender_nonregression') is True
          and isinstance(refinement.get('export'), dict) and refinement['export'].get('output_sha256') == source_hash):
        binding = 'exported_proposal'
    else:
        raise ValueError('Refinement report is bound to a different original candidate')
    if (not all(isinstance(v, dict) for v in refinement['views'])
            or not any('camera_fit' in v for v in refinement['views'])):
        raise ValueError('Refinement must contain fitted camera views')
    normalization = refinement.get('normalization')
    if (not isinstance(normalization, dict) or np.shape(normalization.get('center')) != (3,)
            or not np.isfinite(normalization['center']).all()
            or not np.isfinite(normalization.get('extent', np.nan)) or normalization['extent'] <= 0):
        raise ValueError('Refinement normalization is invalid')
    verification = verify_partition_glb_bytes(source_raw, partitioned_raw, receipt)
    if verification.get('status') != 'verified':
        raise ValueError('Partition receipt did not verify')
    original, partitioned = load_glb_bytes(source_raw), load_glb_bytes(partitioned_raw)
    left, right = _canonical_triangles(original), _canonical_triangles(partitioned)
    if left.shape != right.shape or not np.array_equal(left, right):
        raise ValueError('Partitioned candidate does not contain exactly the original world triangles')
    partitioned_hash = hashlib.sha256(partitioned_raw).hexdigest()
    result = deepcopy(refinement)
    result['source_sha256'] = partitioned_hash
    result['source_model'] = str(partitioned_model_path)
    if refinement.get('view_scene') is not None:
        from .view_scene import (read_view_scene_contract, transfer_face_roles, PartBinding,
                                 ViewState, make_view_scene_contract, mesh_geometry_sha256)
        rest, roles, _ = read_view_scene_contract(refinement['view_scene'])
        original_roles, initial_transfer = transfer_face_roles(rest, roles, original)
        if initial_transfer['unmatched_faces'] or initial_transfer['ambiguous_faces']:
            raise ValueError('Articulation binding does not cover the partition source')
        key = lambda row: tuple(row[k] for k in ('node_index', 'mesh_index', 'primitive_index'))
        original_parts = {key(part): part for part in original.parts}
        output_parts = {key(part): part for part in partitioned.parts}
        target_roles = np.full(len(partitioned.faces), '', dtype='<U12')
        for row in receipt['primitives']:
            source_part = original_parts.get(key(row['source_binding']))
            if source_part is None:
                continue  # Invisible source primitives have no loaded faces.
            for emitted in row['outputs']:
                target_part = output_parts.get(key(emitted['output_binding']))
                if target_part is None:
                    continue
                source_ids = np.concatenate([np.arange(start, stop) for start, stop in emitted['source_face_ranges']])
                if len(source_ids) != target_part['face_count']:
                    raise ValueError('Partition articulation lineage count mismatch')
                source_ids += source_part['face_start']
                begin = target_part['face_start']
                target_roles[begin:begin + len(source_ids)] = np.asarray(original_roles.face_roles)[source_ids]
        if np.any(target_roles == ''):
            raise ValueError('Partition articulation lineage omits loaded faces')
        rebound = PartBinding(mesh_geometry_sha256(partitioned), tuple(target_roles), original_roles.hinges,
                              original_roles.provenance + '; verified exact face-partition lineage')
        old_contract = refinement['view_scene']
        states = {view: ViewState(view, row['left_degrees'], row['right_degrees']) for view, row in old_contract['views'].items()}
        result['view_scene'] = make_view_scene_contract(partitioned, rebound, states,
            source_model=partitioned_model_path, source_sha256=partitioned_hash,
            photo_sha256={view: row['source_image_sha256'] for view, row in old_contract['views'].items()},
            evidence={'parent_contract_sha256': old_contract['contract_sha256'],
                      'verified_partition_receipt_sha256': receipt['receipt_sha256'],
                      'transfer': 'exact source face ranges from independently verified partition receipt'})
        result['view_scene_transfer'] = {'method': 'verified_partition_source_face_ranges',
                                        'transferred_faces': len(target_roles), 'unmatched_faces': 0, 'ambiguous_faces': 0}
    result['camera_transfer'] = {
        'method': METHOD, 'original_source_sha256': source_hash, 'partitioned_source_sha256': partitioned_hash,
        'refinement_binding': binding, 'refinement_source_sha256': refinement.get('source_sha256'),
        'partition_receipt_sha256': receipt.get('receipt_sha256'), 'partition_verification': verification['status'],
        'world_triangle_multiset_identical': True, 'triangle_count': int(len(left)),
        'loader': 'reconstruction.mesh.load_glb_bytes; same primitive visibility rules as the region stage',
        'cameras': 'unchanged fitted cameras and original normalization; no refit',
        'scope': 'Geometric identity of the loaded triangles, not camera calibration or semantic identity.'}
    json.dumps(result, allow_nan=False)
    return result
