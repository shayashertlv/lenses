"""Exact source-face connectivity, without welding or semantic classification.

The result is a partition of every source triangle, including duplicate and
degenerate triangles. Equality of positions is numerical equality in the input
floating dtype (+0 and -0 compare equal); positions are never rounded or moved.
Disconnected components are candidate pieces, not physical lens identities.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


@dataclass(frozen=True)
class ComponentCapacity:
    """Explicit input bounds for the temporary sparse graphs and sort arrays."""

    maximum_vertices: int = 2_000_000
    maximum_faces: int = 1_000_000

    def __post_init__(self):
        for name in ('maximum_vertices', 'maximum_faces'):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} must be a positive integer')


DEFAULT_CAPACITY = ComponentCapacity()
CONNECTIVITY_MODES = (
    'shared_index_vertex', 'exact_position_vertex', 'exact_position_edge',
)


def _validated(positions, indices, capacity):
    if not isinstance(capacity, ComponentCapacity):
        raise ValueError('capacity must be a ComponentCapacity')
    positions, indices = np.asarray(positions), np.asarray(indices)
    if positions.ndim != 2 or positions.shape[1] != 3 or positions.dtype.kind != 'f':
        raise ValueError('positions must be a floating N-by-3 array')
    if indices.ndim != 2 or indices.shape[1] != 3 or indices.dtype.kind not in 'iu':
        raise ValueError('indices must be an integer M-by-3 array')
    if len(positions) > capacity.maximum_vertices:
        raise ValueError('Source vertex count exceeds component capacity')
    if len(indices) > capacity.maximum_faces:
        raise ValueError('Source face count exceeds component capacity')
    if not np.isfinite(positions).all():
        raise ValueError('positions must be finite, including unused vertices')
    if indices.size and (indices.min() < 0 or indices.max() >= len(positions)):
        raise ValueError('indices must refer to existing source vertices')
    return positions, indices


def _canonical_labels(labels):
    """Order components by their first source face, independent of graph order."""
    _, first, inverse = np.unique(labels, return_index=True, return_inverse=True)
    order = np.argsort(first, kind='stable')
    canonical = np.empty(len(order), dtype=np.int64)
    canonical[order] = np.arange(len(order), dtype=np.int64)
    return canonical[inverse]


def component_face_labels(positions, indices, *,
                          connectivity='exact_position_vertex',
                          capacity=DEFAULT_CAPACITY) -> np.ndarray:
    """Return contiguous int64 component labels in first-source-face order.

    ``positions`` is finite floating N-by-3 data, preferably in source local
    coordinates. ``indices`` is signed/unsigned integer M-by-3 data. Unused
    vertices do not create components. No input array is modified. Empty face
    arrays yield empty labels; invalid inputs or capacity excess raise ValueError.

    Modes:
    * ``shared_index_vertex``: triangles connect through a shared source index.
    * ``exact_position_vertex``: triangles connect through any numerically equal
      position triple, even if the source uses different vertex indices.
    * ``exact_position_edge``: triangles connect through an equal unordered pair
      of exact positions. A degenerate edge (p,p) matches another (p,p); it does
      not match an ordinary edge (p,q). Degenerate faces remain in the partition.

    The vertex modes use at most two sparse adjacency entries per face; the edge
    mode sorts three edge records per face and connects consecutive owners of
    each shared edge. A component may contain many disconnected-looking patches
    joined at a single vertex. Connectivity does not establish manifoldness,
    watertightness, physical thickness, optical identity or group membership.
    """
    if not isinstance(connectivity, str) or connectivity not in CONNECTIVITY_MODES:
        raise ValueError('Unknown source component connectivity mode')
    positions, indices = _validated(positions, indices, capacity)
    if not len(indices):
        return np.empty(0, dtype=np.int64)

    # Compact only referenced indices before constructing a graph. This also
    # preserves unsigned index validation before any signed conversion occurs.
    used, inverse = np.unique(indices, return_inverse=True)
    faces = inverse.reshape(indices.shape)
    vertex_count = len(used)
    if connectivity != 'shared_index_vertex':
        # np.unique retains the floating dtype. Casting to float32/64 here could
        # merge distinct source positions, which would change the partition.
        exact, position_ids = np.unique(positions[used], axis=0, return_inverse=True)
        faces = position_ids[faces]
        vertex_count = len(exact)

    if connectivity == 'exact_position_edge':
        edges = np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]))
        edges.sort(axis=1)
        owners = np.tile(np.arange(len(faces), dtype=np.int64), 3)
        order = np.lexsort((edges[:, 1], edges[:, 0]))
        edges, owners = edges[order], owners[order]
        shared = np.all(edges[1:] == edges[:-1], axis=1)
        rows, cols = owners[:-1][shared], owners[1:][shared]
        graph_size = len(faces)
    else:
        rows = np.tile(faces[:, 0], 2)
        cols = np.concatenate((faces[:, 1], faces[:, 2]))
        graph_size = vertex_count

    # Boolean adjacency avoids integer overflow when duplicate faces repeat the
    # same graph entry. Numerical edge weights are irrelevant to connectivity.
    graph = coo_matrix((np.ones(len(rows), dtype=bool), (rows, cols)),
                       shape=(graph_size, graph_size)).tocsr()
    _, labels = connected_components(graph, directed=False, return_labels=True)
    face_labels = labels if connectivity == 'exact_position_edge' else labels[faces[:, 0]]
    return _canonical_labels(face_labels)
