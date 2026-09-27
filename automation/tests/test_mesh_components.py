import unittest

import numpy as np

from reconstruction.mesh_components import (
    CONNECTIVITY_MODES, ComponentCapacity, component_face_labels,
)


def independent_face_graph(positions, indices, mode):
    """Small pairwise set-intersection oracle, independent of sparse graph code."""
    if mode == 'shared_index_vertex':
        entries = [set(map(int, face)) for face in indices]
    elif mode == 'exact_position_vertex':
        entries = [set(tuple(positions[i]) for i in face) for face in indices]
    else:
        entries = []
        for face in indices:
            vertices = [tuple(positions[i]) for i in face]
            entries.append({tuple(sorted((vertices[i], vertices[(i+1) % 3])))
                            for i in range(3)})
    labels = np.full(len(indices), -1, dtype=np.int64)
    label = 0
    for first in range(len(indices)):
        if labels[first] >= 0:
            continue
        labels[first] = label
        queue = [first]
        while queue:
            current = queue.pop()
            for other in range(len(indices)):
                if labels[other] < 0 and entries[current] & entries[other]:
                    labels[other] = label
                    queue.append(other)
        label += 1
    return labels


class MeshComponentTests(unittest.TestCase):
    def test_attribute_seams_merge_only_under_exact_position_modes(self):
        positions = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0],
                              [0, 0, 0], [1, 0, 0], [1, -1, 0]])
        faces = np.array([[3, 4, 5], [0, 1, 2]])
        np.testing.assert_array_equal(component_face_labels(positions, faces,
            connectivity='shared_index_vertex'), [0, 1])
        for mode in ('exact_position_vertex', 'exact_position_edge'):
            np.testing.assert_array_equal(component_face_labels(positions, faces,
                connectivity=mode), [0, 0])

    def test_point_contact_differs_from_shared_edge(self):
        positions = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0],
                              [3, 0, 0], [3, 1, 0], [4, 0, 0]])
        faces = np.array([[3, 4, 5], [0, 1, 2], [2, 4, 5]])
        np.testing.assert_array_equal(component_face_labels(positions, faces), [0, 0, 0])
        np.testing.assert_array_equal(component_face_labels(positions, faces,
            connectivity='exact_position_edge'), [0, 1, 0])

    def test_labels_follow_first_face_not_vertex_or_coordinate_order(self):
        positions = np.arange(36, dtype=float).reshape(12, 3)
        faces = np.array([[9, 10, 11], [0, 1, 2], [3, 4, 5], [9, 11, 10], [1, 2, 0]])
        for mode in CONNECTIVITY_MODES:
            result = component_face_labels(positions, faces, connectivity=mode)
            self.assertEqual(result.dtype, np.dtype('int64'))
            np.testing.assert_array_equal(result, [0, 1, 2, 0, 1])

    def test_exact_numeric_equality_includes_signed_zero_without_quantization(self):
        positions = np.array([[0., 0, 0], [-0., 0, -0.], [1., 0, 0],
                              [1. + 1e-10, 0, 0], [np.nextafter(0., 1.), 0, 0]])
        faces = np.array([[0, 0, 0], [1, 1, 1], [2, 2, 2], [3, 3, 3], [4, 4, 4]])
        for mode in ('exact_position_vertex', 'exact_position_edge'):
            np.testing.assert_array_equal(component_face_labels(positions, faces,
                connectivity=mode), [0, 0, 1, 2, 3])
        np.testing.assert_array_equal(component_face_labels(positions, faces,
            connectivity='shared_index_vertex'), [0, 1, 2, 3, 4])

    def test_duplicate_and_degenerate_faces_are_retained(self):
        positions = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [8, 8, 8]])
        faces = np.array([[0, 0, 0], [0, 1, 2], [0, 1, 1], [3, 3, 3],
                          [0, 0, 0], [2, 1, 0]])
        np.testing.assert_array_equal(component_face_labels(positions, faces), [0, 0, 0, 1, 0, 0])
        np.testing.assert_array_equal(component_face_labels(positions, faces,
            connectivity='exact_position_edge'), [0, 1, 1, 2, 0, 1])

    def test_many_duplicate_faces_do_not_overflow_adjacency(self):
        positions = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [8, 8, 8]])
        faces = np.vstack((np.tile([0, 1, 2], (512, 1)), [3, 3, 3]))
        for mode in CONNECTIVITY_MODES:
            np.testing.assert_array_equal(component_face_labels(positions, faces,
                connectivity=mode), np.r_[np.zeros(512, dtype=np.int64), 1])

    def test_readonly_strided_arrays_and_unsigned_indices_are_unchanged(self):
        positions = np.arange(72, dtype=np.float32).reshape(12, 6)[:, ::2]
        indices = np.array([[5, 6, 7, 99], [0, 1, 2, 99]], dtype=np.uint64)[:, :3]
        before_positions, before_indices = positions.copy(), indices.copy()
        positions.setflags(write=False); indices.setflags(write=False)
        for mode in CONNECTIVITY_MODES:
            np.testing.assert_array_equal(component_face_labels(positions, indices,
                connectivity=mode), [0, 1])
        np.testing.assert_array_equal(positions, before_positions)
        np.testing.assert_array_equal(indices, before_indices)

    def test_unused_vertices_do_not_create_components(self):
        positions = np.zeros((25, 3), dtype=float)
        positions[0] = [9, 9, 9]
        positions[24] = [8, 8, 8]
        for mode in CONNECTIVITY_MODES:
            np.testing.assert_array_equal(component_face_labels(positions,
                np.array([[11, 12, 13]]), connectivity=mode), [0])

    def test_empty_face_inventory(self):
        for positions in (np.empty((0, 3), float), np.ones((10, 3), np.float16)):
            for mode in CONNECTIVITY_MODES:
                result = component_face_labels(positions, np.empty((0, 3), np.int32),
                                               connectivity=mode)
                self.assertEqual(result.shape, (0,))
                self.assertEqual(result.dtype, np.dtype('int64'))

    def test_invalid_inputs_are_rejected(self):
        good_positions, good_faces = np.zeros((3, 3)), np.array([[0, 1, 2]])
        invalid_positions = [np.zeros((3, 2)), np.zeros((3, 3), int),
                             np.zeros((3, 3), complex), np.full((3, 3), np.nan),
                             np.array([[0., 0, 0], [1, 1, 1], [np.inf, 0, 0]]),
                             np.array([[0., 0, 0]], dtype=object)]
        for positions in invalid_positions:
            with self.subTest(positions=positions):
                with self.assertRaises(ValueError):
                    component_face_labels(positions, good_faces)
        invalid_faces = [np.array([0, 1, 2]), np.array([[0, 1]]),
                         np.array([[0., 1., 2.]]), np.array([[True, False, True]]),
                         np.array([[0, -1, 2]]), np.array([[0, 1, 3]]),
                         np.array([[0, 1, np.iinfo(np.uint64).max]], dtype=np.uint64)]
        for faces in invalid_faces:
            with self.subTest(faces=faces):
                with self.assertRaises(ValueError):
                    component_face_labels(good_positions, faces)
        for mode in ('approximate', None, 1, []):
            with self.assertRaises(ValueError):
                component_face_labels(good_positions, good_faces, connectivity=mode)

    def test_nonfinite_unused_vertices_still_reject_invalid_source(self):
        positions = np.array([[0., 0, 0], [np.nan, 1, 2]])
        with self.assertRaises(ValueError):
            component_face_labels(positions, np.array([[0, 0, 0]]))

    def test_capacity_is_explicit_and_validated(self):
        positions, faces = np.zeros((3, 3)), np.array([[0, 1, 2]])
        with self.assertRaisesRegex(ValueError, 'vertex count'):
            component_face_labels(positions, faces, capacity=ComponentCapacity(maximum_vertices=2))
        with self.assertRaisesRegex(ValueError, 'face count'):
            component_face_labels(positions, np.vstack((faces, faces)),
                                  capacity=ComponentCapacity(maximum_faces=1))
        for invalid in (None, {}, 1):
            with self.assertRaises(ValueError):
                component_face_labels(positions, faces, capacity=invalid)
        for value in (0, -1, 1., True):
            with self.assertRaises(ValueError):
                ComponentCapacity(maximum_faces=value)
            with self.assertRaises(ValueError):
                ComponentCapacity(maximum_vertices=value)

    def test_seeded_small_graphs_match_independent_face_intersection_oracle(self):
        random = np.random.default_rng(71729)
        for trial in range(90):
            count = int(random.integers(1, 15))
            positions = random.integers(-2, 3, size=(count, 3)).astype(float)
            if count > 2:
                positions[-1] = positions[0]
                positions[-2] = positions[1]
            faces = random.integers(0, count, size=(int(random.integers(0, 18)), 3))
            for mode in CONNECTIVITY_MODES:
                with self.subTest(trial=trial, mode=mode):
                    actual = component_face_labels(positions, faces, connectivity=mode)
                    expected = independent_face_graph(positions, faces, mode)
                    np.testing.assert_array_equal(actual, expected)


if __name__ == '__main__':
    unittest.main()
