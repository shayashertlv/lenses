"""Bind explicit piece-group hypotheses to a verified partitioned GLB.

This adapter does not select optical pieces. Source roles do not become labels,
and old source part ordinals are never reused as partitioned-asset ordinals.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib

from .mesh import load_glb_bytes
from .partition_glb import verify_partition_glb_bytes
from .prepare_optical_groups import validate_group_declarations


def declarations_for_partition(source_raw: bytes, partitioned_raw: bytes, receipt: dict,
                               groups: list[dict], *, coordinate_frame: dict,
                               provenance: dict) -> dict:
    """Resolve [{group_id, piece_ids}] to fresh whole-primitive declarations.

    Every selected piece must be observable to the existing mesh loader. An
    alpha-zero ordinary primitive cannot silently disappear from a requested
    group. Unselected pieces retain source materials and unverified semantics.
    """
    verification = verify_partition_glb_bytes(source_raw, partitioned_raw, receipt)
    if not isinstance(provenance, dict) or not provenance:
        raise ValueError('Explicit grouping-hypothesis provenance is required')
    if not isinstance(groups, list) or not groups:
        raise ValueError('Nonempty explicit piece groups are required')
    pieces = {}
    for primitive in receipt['primitives']:
        for output in primitive['outputs']:
            if output['piece_id'] is not None:
                pieces.setdefault(output['piece_id'], []).append(output)
    mesh = load_glb_bytes(partitioned_raw)
    keys = ('node_index', 'mesh_index', 'primitive_index')
    parts = {tuple(part[k] for k in keys): i for i, part in enumerate(mesh.parts)}
    requested, declarations = set(), []
    for group in groups:
        if not isinstance(group, dict) or set(group) != {'group_id', 'piece_ids'}:
            raise ValueError('Piece groups require group_id and piece_ids only')
        ids = group['piece_ids']
        if not isinstance(ids, list) or not ids or any(not isinstance(pid, str) for pid in ids):
            raise ValueError('Every group requires explicit nonempty piece IDs')
        members = []
        for pid in ids:
            if pid not in pieces or pid in requested:
                raise ValueError('Unknown or repeated optical piece membership')
            requested.add(pid)
            for output in pieces[pid]:
                binding = output['output_binding']
                key = tuple(binding[k] for k in keys)
                if key not in parts:
                    raise ValueError('Requested piece is absent from the optical consumer inventory; membership cannot be dropped')
                members.append({'id': pid, 'source_part_index': parts[key], 'source_binding': deepcopy(binding)})
        declarations.append({'group_id': group['group_id'], 'members': members})
    source_hash = hashlib.sha256(source_raw).hexdigest()
    output_hash = hashlib.sha256(partitioned_raw).hexdigest()
    return validate_group_declarations({'schema_version': 1, 'source_sha256': output_hash,
        'coordinate_frame': deepcopy(coordinate_frame), 'groups': declarations,
        'provenance': {'method': 'explicit_piece_groups_from_verified_face_partition',
            'identity_status': 'unverified', 'hypothesis': deepcopy(provenance),
            'partition_lineage': {'original_source_sha256': source_hash,
                'partitioned_source_sha256': output_hash, 'receipt_sha256': receipt['receipt_sha256'],
                'verification_scope': verification['status'],
                'source_face_lineage': 'bound by original source bytes, partitioned bytes and complete verified receipt'},
            'unselected_piece_ids': sorted(set(pieces)-requested),
            'observation_binding': 'old source-bound camera/region part priors require explicit translation or regeneration'}})
