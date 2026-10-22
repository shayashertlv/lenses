"""Blender adapter for g3d.contacts: evaluated world-space meshes and BVH nearest-surface queries (read-only)."""
from __future__ import annotations

import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

from glasses3d.g3d import contacts

KINDS = {"MESH", "CURVE", "FONT", "SURFACE"}


def _world_mesh(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        if mesh is None or not len(mesh.vertices):
            return None
        co = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get("co", co)
        matrix = np.array(evaluated.matrix_world, dtype=np.float64)
        co = co.reshape(-1, 3).astype(np.float64) @ matrix[:3, :3].T + matrix[:3, 3]
        mesh.calc_loop_triangles()
        triangles = np.empty(len(mesh.loop_triangles) * 3, np.int32)
        mesh.loop_triangles.foreach_get("vertices", triangles)
        return co, triangles.reshape(-1, 3).astype(np.int64)
    finally:
        evaluated.to_mesh_clear()


def report(names=None, *, budget_s=12.0):
    """Every render-enabled surface part; names keeps only rows that involve one of them."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    parts, trees = [], []
    for obj in bpy.context.scene.objects:
        if obj.type not in KINDS or obj.hide_render:
            continue
        world = _world_mesh(obj, depsgraph)
        if world is None or not len(world[1]):
            continue
        vertices, triangles = world
        trees.append(BVHTree.FromPolygons(vertices.tolist(), triangles.tolist(), all_triangles=True))
        parts.append({"name": obj.name, "role": obj.get("partRole"), "points": contacts.samples(vertices, triangles)})

    def nearest(j, points, limit):
        tree, distance = trees[j], np.full(len(points), np.inf)
        for k, point in enumerate(points.tolist()):
            hit = tree.find_nearest(point, limit)
            if hit[0] is not None:
                distance[k] = hit[3]
        return distance

    result = contacts.near_misses(parts, nearest, intersects=lambda i, j: bool(trees[i].overlap(trees[j])),
                                  budget_s=budget_s)
    if names is not None:
        wanted = set(names)
        result["near_misses"] = [row for row in result["near_misses"] if wanted & set(row["parts"])]
    return result
