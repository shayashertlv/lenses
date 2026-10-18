"""No new surface intersections on the actual, unnormalized mesh coordinates.

The BVH only rejects disjoint closed bounding boxes. All remaining predicates
use the exact dyadic values represented by the input floats; no length, area,
angle or coordinate tolerance can hide a crossing. Unchanged triangles need no
pairwise recheck, so a small edit does not become an all-pairs source proof.
"""
from collections import defaultdict, deque
from fractions import Fraction

import numpy as np


def _sub(a, b):
    return tuple(x-y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _normal(triangle):
    return _cross(_sub(triangle[1], triangle[0]), _sub(triangle[2], triangle[0]))


def _along(a, b, numerator, denominator):
    t = Fraction(numerator, denominator)
    return tuple(x+(y-x)*t for x, y in zip(a, b))


def _canonical(points):
    """Unique extreme points of a convex planar locus (including a segment)."""
    points = list(dict.fromkeys(points))
    if len(points) < 3:
        return tuple(sorted(points))
    normal = next((_cross(_sub(p, points[0]), _sub(q, points[0]))
                   for p, q in zip(points[1:], points[2:])
                   if any(_cross(_sub(p, points[0]), _sub(q, points[0])))), None)
    if normal is None:
        return (min(points), max(points))
    # Clipping preserves cyclic order; discard redundant collinear vertices.
    result = [p for i, p in enumerate(points)
              if any(_cross(_sub(p, points[i-1]), _sub(points[(i+1) % len(points)], p)))]
    return tuple(sorted(result))


def _coplanar(a, b, normal):
    drop = max(range(3), key=lambda i: abs(normal[i]))
    axes = [i for i in range(3) if i != drop]

    def side(p, q, r):
        u, v = axes
        return (q[u]-p[u])*(r[v]-p[v])-(q[v]-p[v])*(r[u]-p[u])

    if side(*b) < 0:
        b = b[::-1]
    result = list(a)
    for p, q in zip(b, b[1:]+b[:1]):
        if not result:
            break
        old, result = result, []
        previous = old[-1]
        dp = side(p, q, previous)
        for current in old:
            dc = side(p, q, current)
            if (dc >= 0) != (dp >= 0):
                result.append(_along(previous, current, dp, dp-dc))
            if dc >= 0:
                result.append(current)
            previous, dp = current, dc
    return _canonical(result)


def _slice(triangle, distances):
    points = [p for p, d in zip(triangle, distances) if d == 0]
    for i, j in ((0, 1), (1, 2), (2, 0)):
        if distances[i]*distances[j] < 0:
            points.append(_along(triangle[i], triangle[j], distances[i], distances[i]-distances[j]))
    return list(dict.fromkeys(points))


def _intersection(a, b):
    """Exact convex intersection locus of two nondegenerate 3D triangles."""
    na, nb = _normal(a), _normal(b)
    if not any(na) or not any(nb):
        raise ValueError('a changed surface meets a degenerate triangle; no-crossing proof is ambiguous')
    da = [_dot(nb, _sub(p, b[0])) for p in a]
    db = [_dot(na, _sub(p, a[0])) for p in b]
    if (all(x > 0 for x in da) or all(x < 0 for x in da) or
            all(x > 0 for x in db) or all(x < 0 for x in db)):
        return ()
    if not any(da):
        return _coplanar(a, b, na)
    direction = _cross(na, nb)
    if not any(direction):
        return ()
    axis = max(range(3), key=lambda i: abs(direction[i]))
    sa, sb = _slice(a, da), _slice(b, db)
    if not sa or not sb:
        return ()
    lo = max(min(sa, key=lambda p: p[axis]), min(sb, key=lambda p: p[axis]), key=lambda p: p[axis])
    hi = min(max(sa, key=lambda p: p[axis]), max(sb, key=lambda p: p[axis]), key=lambda p: p[axis])
    if lo[axis] > hi[axis]:
        return ()
    return tuple(sorted({lo, hi}))


def _on_segment(p, a, b):
    return (not any(_cross(_sub(p, a), _sub(b, a))) and
            all(min(x, y) <= z <= max(x, y) for x, y, z in zip(a, b, p)))


def _topological_contact(locus, shared):
    if len(shared) == 1:
        return len(locus) == 1 and locus[0] == shared[0]
    if len(shared) == 2:
        return all(_on_segment(p, *shared) for p in locus)
    # Coincident triangles are never an allowed shared-edge contact.
    return False


class _Tree:
    """Median AABB tree; storage is linear even for long, thin triangles."""
    def __init__(self, lo, hi):
        self.lo, self.hi = lo, hi
        self.centers = lo+(hi-lo)*.5
        self.root = self._build(np.arange(len(lo)))

    def _build(self, ids):
        low, high = self.lo[ids].min(0), self.hi[ids].max(0)
        if len(ids) <= 16:
            return low, high, ids, None
        axis = int(np.argmax(np.ptp(self.centers[ids], axis=0)))
        order = ids[np.argsort(self.centers[ids, axis], kind='stable')]
        split = len(order)//2
        return low, high, self._build(order[:split]), self._build(order[split:])

    def candidates(self, lo, hi):
        pending = [self.root]
        while pending:
            low, high, left, right = pending.pop()
            if np.any(high < lo) or np.any(low > hi):
                continue
            if right is None:
                overlap = np.all(self.hi[left] >= lo, axis=1) & np.all(self.lo[left] <= hi, axis=1)
                yield from left[overlap]
            else:
                pending.extend((left, right))


def _lattice(source, target):
    """One exact integer lattice per world axis; no coordinate rounding."""
    ratios = [[tuple(float(x).as_integer_ratio() for x in p) for p in v] for v in (source, target)]
    powers = [max(d.bit_length()-1 for rows in ratios for p in rows for _, d in [p[k]]) for k in range(3)]
    return [tuple(tuple(n << (powers[k]-(d.bit_length()-1)) for k, (n, d) in enumerate(p)) for p in rows)
            for rows in ratios]


def no_new_crossings(source, target):
    """Refuse any new non-topological intersection, including coplanar overlap.

    Existing non-topological contacts are accepted only when the corresponding
    original triangle pair has exactly the same intersection locus. A changed
    triangulation with no source correspondence must prove its contacts afresh.
    Parent-polygon membership is deliberately not an intersection exemption.
    """
    from .owner_mesh import OwnerMeshError

    before = np.asarray(source['vertices_world_mm'], dtype=float)
    after = np.asarray(target['vertices_world_mm'], dtype=float)
    old_tri = np.asarray(source['triangles'], dtype=int)
    new_tri = np.asarray(target['triangles'], dtype=int)
    if (before.ndim != 2 or after.ndim != 2 or before.shape[1:] != (3,) or after.shape[1:] != (3,)
            or not np.isfinite(before).all() or not np.isfinite(after).all()
            or old_tri.ndim != 2 or new_tri.ndim != 2 or old_tri.shape[1:] != (3,) or new_tri.shape[1:] != (3,)
            or not len(new_tri) or not len(old_tri) or np.any(old_tri < 0) or np.any(new_tri < 0)
            or np.any(old_tri >= len(before)) or np.any(new_tri >= len(after))):
        raise OwnerMeshError('E_OWNER_CROSSING', 'no-crossing proof requires finite triangles with valid indices')

    # Geometric multiplicity matters. Reindexing/reversing an unchanged triangle
    # creates no new intersection, but adding a duplicate triangle does.
    old_points = [tuple(p) for p in before]
    new_points = [tuple(p) for p in after]
    unchanged = defaultdict(deque)
    old_indices = defaultdict(list)
    for i, ids in enumerate(old_tri):
        unchanged[tuple(sorted(old_points[v] for v in ids))].append(i)
        old_indices[tuple(sorted(map(int, ids)))].append(i)
    changed, correspondence = [], {}
    same_indices = len(before) == len(after) and np.array_equal(old_tri, new_tri)
    # A retained ancestry attribute may describe an earlier topology operation.
    # Actual unchanged connectivity binds this round's IDs directly to source.
    parents = (list(range(len(after))) if same_indices else
               target.get('_surface_vertex_parents', list(range(len(after)))))
    for i, ids in enumerate(new_tri):
        key = tuple(sorted(new_points[v] for v in ids))
        if unchanged[key]:
            correspondence[i] = unchanged[key].popleft()
        else:
            changed.append(i)
            mapped = [parents[v] if v < len(parents) else None for v in ids]
            if all(isinstance(v, (int, np.integer)) for v in mapped):
                original = old_indices.get(tuple(sorted(map(int, mapped))), ())
                if len(original) == 1:
                    correspondence[i] = original[0]
    report = {'method': 'exact unnormalized triangle intersections', 'changed_triangles': len(changed),
              'candidate_pairs': 0, 'inherited_contacts': 0, 'unchanged_remainder_proven': True}
    if not changed:
        return report
    exact_old, exact_new = _lattice(before, after)
    xyz = after[new_tri]
    lo, hi = xyz.min(1), xyz.max(1)
    tree = _Tree(lo, hi)
    changed_set = set(changed)
    cached = {}

    def points(i):
        if i not in cached:
            cached[i] = tuple(exact_new[v] for v in new_tri[i])
        return cached[i]

    try:
        for i in changed:
            a = points(i)
            if not any(_normal(a)):
                raise ValueError('the changed surface contains a degenerate triangle; no-crossing proof is ambiguous')
            for raw_j in tree.candidates(lo[i], hi[i]):
                j = int(raw_j)
                if j == i or (j in changed_set and j < i):
                    continue
                report['candidate_pairs'] += 1
                locus = _intersection(a, points(j))
                if not locus:
                    continue
                shared = [exact_new[v] for v in sorted(set(new_tri[i]) & set(new_tri[j]))]
                if _topological_contact(locus, shared):
                    continue
                old_i, old_j = correspondence.get(i), correspondence.get(j)
                if old_i is not None and old_j is not None and old_i != old_j:
                    old_locus = _intersection(tuple(exact_old[v] for v in old_tri[old_i]),
                                              tuple(exact_old[v] for v in old_tri[old_j]))
                    if old_locus == locus:
                        report['inherited_contacts'] += 1
                        continue
                raise OwnerMeshError('E_OWNER_CROSSING', 'the edited surface creates a new triangle crossing or overlap',
                                     triangle_ids=[i, j], intersection_vertices=len(locus))
    except ValueError as exc:
        raise OwnerMeshError('E_OWNER_CROSSING', str(exc)) from exc
    return report
