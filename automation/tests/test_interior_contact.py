"""Interior contact faces: exact cross-part coincidences, and nothing else."""
import json
import unittest

import numpy as np

from reconstruction.interior_contact import cut_continuity, interior_contact_faces
from reconstruction.mesh import TriangleMesh
from test_camera_mesh import boxes_mesh


def with_parts(mesh, counts):
    parts, start, vertex = [], 0, 0
    for i, count in enumerate(counts):
        parts.append({'name': f'part-{i}', 'face_start': start, 'face_count': count, 'vertex_start': vertex, 'vertex_count': 8})
        start += count; vertex += 8
    return TriangleMesh(mesh.vertices, mesh.faces, parts)


def quads_mesh(parts):
    """parts: per body, a list of quads (four corners, counter-clockwise seen from outside); returns (mesh, owners)."""
    vertices, faces, owners = [], [], []
    for body, quads in enumerate(parts):
        for corners in quads:
            base = len(vertices); vertices.extend(corners)
            faces += [[base, base + 1, base + 2], [base, base + 2, base + 3]]; owners += [body, body]
    return TriangleMesh(np.asarray(vertices, float), np.asarray(faces, np.int64), []), np.asarray(owners, np.int64)


def box_quads(x0, y0, z0, x1, y1, z1, z_cuts=()):
    """Outward quads of a box; its -x, -y and +y faces are split at the given z levels."""
    levels = [z0, *z_cuts, z1]
    quads = [[(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
             [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)],
             [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]]
    for za, zb in zip(levels, levels[1:]):
        quads += [[(x0, y0, za), (x0, y0, zb), (x0, y1, zb), (x0, y1, za)],
                  [(x0, y0, za), (x1, y0, za), (x1, y0, zb), (x0, y0, zb)],
                  [(x0, y1, za), (x0, y1, zb), (x1, y1, zb), (x1, y1, za)]]
    return quads


class InteriorContactTests(unittest.TestCase):
    def test_cut_continuity_tells_a_planar_cut_from_a_step(self):
        # Two unit boxes glued at x = 1: every outer surface continues across the shared square.
        mesh, owners = quads_mesh([box_quads(0, 0, 0, 1, 1, 1), box_quads(1, 0, 0, 2, 1, 1)])
        mask, receipt = interior_contact_faces(mesh, owners=owners)
        self.assertEqual(receipt['part_pairs'], [{'parts': [0, 1], 'faces': 4}])
        cut = cut_continuity(mesh, mask, owners, minimum_edges=1)
        json.dumps(cut, allow_nan=False)
        self.assertEqual(len(cut['pairs']), 1)
        pair = cut['pairs'][0]
        self.assertEqual((pair['bodies'], pair['boundary_edges'], pair['continuous_fraction'], pair['enough_edges']), ([0, 1], 4, 1.0, True))
        self.assertLess(pair['median_angle_degrees'], 1e-6)
        self.assertFalse(cut_continuity(mesh, mask, owners)['pairs'][0]['enough_edges'], 'four edges are below the default minimum')
        # A unit box against the middle of a bar three times as tall (its faces split at the box's top and bottom):
        # the front and back edges of the shared square continue, the top and bottom edges meet the bar at ninety degrees.
        mesh, owners = quads_mesh([box_quads(0, 0, 0, 1, 1, 1), box_quads(1, 0, -1, 2, 1, 2, z_cuts=(0, 1))])
        mask, receipt = interior_contact_faces(mesh, owners=owners)
        self.assertEqual(receipt['part_pairs'], [{'parts': [0, 1], 'faces': 4}])
        pair = cut_continuity(mesh, mask, owners, minimum_edges=1)['pairs'][0]
        self.assertEqual((pair['bodies'], pair['boundary_edges'], pair['continuous_fraction']), ([0, 1], 4, .5))
        self.assertAlmostEqual(pair['median_angle_degrees'], 60., places=6)
        # No contact: no pairs. Bad inputs are refused.
        apart, owners_apart = quads_mesh([box_quads(0, 0, 0, 1, 1, 1), box_quads(2, 0, 0, 3, 1, 1)])
        self.assertEqual(cut_continuity(apart, np.zeros(len(apart.faces), bool), owners_apart)['pairs'], [])
        for bad in (dict(angle_degrees=0), dict(angle_degrees=90), dict(minimum_edges=0)):
            with self.assertRaises(ValueError):
                cut_continuity(mesh, mask, owners, **bad)
        with self.assertRaises(ValueError):
            cut_continuity(mesh, mask, owners[:-1])

    def test_shared_box_face_is_interior_and_only_across_parts(self):
        # Two unit boxes glued along x = 1: the two squares at x = 1 (four triangles) coincide.
        mesh = with_parts(boxes_mesh([((0, 0, 0), (1, 1, 1)), ((1, 0, 0), (2, 1, 1))]), [12, 12])
        mask, receipt = interior_contact_faces(mesh)
        self.assertEqual(int(mask.sum()), 4)
        flagged = mesh.faces[mask]
        self.assertTrue(np.all(np.isclose(mesh.vertices[flagged][:, :, 0], 1.0)), 'only faces on the shared plane are interior')
        self.assertEqual(receipt['part_pairs'], [{'parts': [0, 1], 'faces': 4}])
        self.assertEqual(receipt['per_part_interior_faces'], [{'part': 0, 'faces': 2}, {'part': 1, 'faces': 2}])
        json.dumps(receipt, allow_nan=False)
        # The same two boxes in ONE part: duplicates within a part are not contacts.
        single = TriangleMesh(mesh.vertices, mesh.faces, [{'name': 'all', 'face_start': 0, 'face_count': 24, 'vertex_start': 0, 'vertex_count': 16}])
        self.assertEqual(int(interior_contact_faces(single)[0].sum()), 0)
        # Boxes that only touch along an edge share no face.
        apart = with_parts(boxes_mesh([((0, 0, 0), (1, 1, 1)), ((1, 1, 0), (2, 2, 1))]), [12, 12])
        self.assertEqual(int(interior_contact_faces(apart)[0].sum()), 0)
        # Near-coincidence is not detected: a 1e-6 offset keeps every face visible.
        shifted = with_parts(boxes_mesh([((0, 0, 0), (1, 1, 1)), ((1 + 1e-6, 0, 0), (2, 1, 1))]), [12, 12])
        self.assertEqual(int(interior_contact_faces(shifted)[0].sum()), 0)

    def test_corner_order_does_not_matter_and_inputs_are_checked(self):
        mesh = with_parts(boxes_mesh([((0, 0, 0), (1, 1, 1)), ((1, 0, 0), (2, 1, 1))]), [12, 12])
        rolled = TriangleMesh(mesh.vertices, np.roll(mesh.faces, 1, axis=1), mesh.parts)
        self.assertEqual(int(interior_contact_faces(rolled)[0].sum()), 4)
        with self.assertRaises(ValueError):
            interior_contact_faces(TriangleMesh(mesh.vertices, np.zeros((0, 3), np.int64), []))


if __name__ == '__main__':
    unittest.main()
