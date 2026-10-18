"""Geometric opening domain: rearward arms and no crossing of the fixed front/lenses.

This cannot infer a hinge's mechanical stop from a mesh. It excludes the forward
half-turn and actual surface crossings. Existing hinge contacts are allowed only
within the first five percent of the arm's reach, never along the arm body.
"""
import math

import numpy as np
import open3d as o3d

from . import pose as P


def _edges(faces):
    return np.unique(np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1), axis=0)


def _scene(vertices, faces):
    scene = o3d.t.geometry.RaycastingScene(nthreads=1)
    scene.add_triangles(o3d.core.Tensor(np.asarray(vertices, np.float32)),
                        o3d.core.Tensor(np.asarray(faces, np.uint32)))
    return scene


def _hits(scene, points, edges):
    if not len(edges):
        return np.empty((0, 3))
    a, b = points[edges[:, 0]], points[edges[:, 1]]
    delta = b-a
    valid = np.linalg.norm(delta, axis=1) > 1e-7
    a, delta = a[valid], delta[valid]
    if not len(a):
        return np.empty((0, 3))
    rays = np.column_stack([a, delta]).astype(np.float32)
    hits = scene.list_intersections(o3d.core.Tensor(rays), nthreads=1)
    t, ids = hits['t_hit'].numpy(), hits['ray_ids'].numpy()
    keep = (t > 1e-6) & (t < 1-1e-6)
    return a[ids[keep]] + t[keep, None]*delta[ids[keep]]


class OpeningDomain:
    def __init__(self, mesh, roles, axes):
        self.axes = axes
        self.sides = {}
        tags = P.side_of_faces(mesh, roles)
        fixed = mesh.F[tags == 0]
        self.fixed_edges = _edges(fixed)
        self.fixed_vertices = mesh.V
        self.fixed_scene = _scene(mesh.V, fixed) if len(fixed) else None
        for key, axis in axes.items():
            side = int(key)
            faces = mesh.F[tags == side]
            if not len(faces):
                continue
            indices, inverse = np.unique(faces, return_inverse=True)
            vertices = mesh.V[indices]
            local_faces = inverse.reshape(-1, 3)
            rel = vertices[:, [0, 2]]-axis
            radius = np.linalg.norm(rel, axis=1)
            reach = float(radius.max())
            distal = rel[radius >= .8*reach].mean(axis=0)
            direction = math.degrees(math.atan2(side*distal[0], -distal[1]))
            # The projected transverse width defines a grid narrower than the arm's angular footprint.
            unit = distal/max(np.linalg.norm(distal), 1e-12)
            transverse = rel @ np.array([-unit[1], unit[0]])
            width = float(np.ptp(transverse))
            self.sides[key] = dict(vertices=vertices, faces=local_faces, edges=_edges(local_faces),
                                   scene=_scene(vertices, local_faces), reach=reach,
                                   contact_radius=.05*reach, bounds=(-90-direction, 90-direction),
                                   step=min(2., max(.1, math.degrees(math.atan2(width, reach))*.5)))
        self.cache = {}
        self.reachable = {}

    def bounds(self, key):
        return self.sides[key]['bounds']

    def valid_side(self, key, angle):
        if not self._geometry_valid(key, angle):
            return False
        if angle == 0.:
            return True
        if not self._geometry_valid(key, 0.):
            return False
        # A clear endpoint beyond an obstacle is not reachable by a real hinge.
        # Walk from the authored opening, stopping at the first front/lens crossing.
        direction = 1 if angle > 0 else -1
        step = min(.5, self.sides[key]['step'])
        state = self.reachable.setdefault((key, direction), {'steps': 0, 'blocked': None})
        count = int(abs(angle)//step)
        while state['steps'] < count and state['blocked'] is None:
            state['steps'] += 1
            if not self._geometry_valid(key, direction*state['steps']*step):
                state['blocked'] = state['steps']*step
        return state['blocked'] is None or abs(angle) < state['blocked']

    def _geometry_valid(self, key, angle):
        token = key, float(angle)
        if token in self.cache:
            return self.cache[token]
        data, axis, side = self.sides[key], self.axes[key], int(key)
        lo, hi = data['bounds']
        valid = lo-1e-9 <= angle <= hi+1e-9
        if valid and self.fixed_scene is not None:
            moved = P.rot_about_hinge(data['vertices'], side, angle, (abs(axis[0]), axis[1]))
            # Edges in both directions detect a triangle cutting through a larger face.
            hits = _hits(self.fixed_scene, moved, data['edges'])
            fixed_in_arm = P.rot_about_hinge(self.fixed_vertices, side, -angle, (abs(axis[0]), axis[1]))
            back = _hits(data['scene'], fixed_in_arm, self.fixed_edges)
            if len(back):
                hits = np.concatenate([hits, P.rot_about_hinge(back, side, angle, (abs(axis[0]), axis[1]))])
            if len(hits):
                valid = not np.any(np.linalg.norm(hits[:, [0, 2]]-axis, axis=1) > data['contact_radius'])
        self.cache[token] = bool(valid)
        return bool(valid)

    def valid(self, theta):
        return all(self.valid_side(k, v) for k, v in theta.items() if k in self.sides)
