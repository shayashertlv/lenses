"""Input integrity and real offline CLI integration, without provider/model mocks."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.camera import Camera, project
from reconstruction.deformation import CageField, ReprojectionConstraint, assess_proposal
from reconstruction.evidence import PhotoEvidence
from reconstruction.mesh import TriangleMesh, load_glb
from reconstruction.raster import rasterize
from reconstruction.refine_photos import (boundary_distance, export_backtracked, opaque_partition, preflight,
                                          run, view_geometry_diagnostics, PhotoInput, photo_inputs)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_mesh(path, mesh):
    positions = np.asarray(mesh.vertices, dtype='<f4').tobytes()
    indices = np.asarray(mesh.faces, dtype='<u4').tobytes()
    binary = positions + indices
    document = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(binary)}],
                'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(positions)},
                                {'buffer': 0, 'byteOffset': len(positions), 'byteLength': len(indices)}],
                'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': len(mesh.vertices), 'type': 'VEC3',
                               'min': mesh.vertices.min(axis=0).tolist(), 'max': mesh.vertices.max(axis=0).tolist()},
                              {'bufferView': 1, 'componentType': 5125, 'count': mesh.faces.size, 'type': 'SCALAR'}],
                'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
                'nodes': [{'mesh': 0}], 'scenes': [{'nodes': [0]}], 'scene': 0}
    payload = json.dumps(document).encode('utf-8')
    payload += b' ' * (-len(payload) % 4)
    binary += b'\0' * (-len(binary) % 4)
    path.write_bytes(struct.pack('<4sII', b'glTF', 2, 28 + len(payload) + len(binary))
                     + struct.pack('<II', len(payload), 0x4E4F534A) + payload
                     + struct.pack('<II', len(binary), 0x004E4942) + binary)


def _glasses_mesh():
    boxes = []
    for left, right in [(-.5, -.07), (.07, .5)]:
        boxes.extend([([left, -.19, 0], [left+.05, .19, .045]),
                      ([right-.05, -.19, 0], [right, .19, .045]),
                      ([left, .14, 0], [right, .19, .045]),
                      ([left, -.19, 0], [right, -.14, .045])])
    boxes.extend([([-.1, .06, 0], [.1, .10, .045]),
                  ([-.5, .08, -.45], [-.46, .13, .02]),
                  ([.46, .08, -.45], [.5, .13, .02])])
    faces = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5],
                      [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                      [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    vertices = [np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
                for lo, hi in boxes]
    return TriangleMesh(np.vstack(vertices), np.vstack([faces+8*i for i in range(len(boxes))]), [])


def _simple_inputs(folder):
    model = folder/'model.glb'
    _write_mesh(model, _glasses_mesh())
    first = np.full((128, 160, 3), 255, np.uint8)
    first[40:80, 30:130] = 0
    second = first.copy()
    second[40:45, 30:50] = 255
    paths = folder/'front.png', folder/'angled.png'
    for path, pixels in zip(paths, (first, second)):
        Image.fromarray(pixels).save(path)
    return model, [('front', paths[0]), ('angled', paths[1])]


class RefinePreflightTests(unittest.TestCase):
    def test_photo_ids_are_distinct_from_repeated_or_unknown_view_priors(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            model, photos = _simple_inputs(folder)
            for labels in (('front', 'front'), ('unknown', 'unknown')):
                entries = [PhotoInput('photo_a', photos[0][1], labels[0]), PhotoInput('photo_b', photos[1][1], labels[1])]
                _, hashes = preflight(model, entries, folder/'run', 128, 20)
                self.assertEqual(set(hashes), {'photo_a', 'photo_b'})
            with self.assertRaises(ValueError):
                photo_inputs([PhotoInput('../escape', photos[0][1]), PhotoInput('ok', photos[1][1])])
            with self.assertRaises(ValueError):
                photo_inputs([PhotoInput('con', photos[0][1]), PhotoInput('ok', photos[1][1])])

    def test_duplicate_bytes_are_rejected_before_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            model, photos = _simple_inputs(folder)
            photos[1][1].write_bytes(photos[0][1].read_bytes())
            output = folder/'run'
            with self.assertRaisesRegex(ValueError, 'Duplicate photos'):
                run(model, photos, output, resolution=128, camera_evaluations=20)
            self.assertFalse(output.exists())

    def test_identical_decoded_images_in_different_formats_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            model, photos = _simple_inputs(folder)
            bmp = folder/'same-pixels.bmp'
            with Image.open(photos[0][1]) as image:
                image.save(bmp)
            self.assertNotEqual(_sha(photos[0][1]), _sha(bmp))
            with self.assertRaisesRegex(ValueError, 'Duplicate photos'):
                preflight(model, [photos[0], ('angled', bmp)], folder/'run', 128, 20)
            self.assertFalse((folder/'run').exists())

    def test_nonempty_output_and_its_evidence_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            model, photos = _simple_inputs(folder)
            output = folder/'run'
            output.mkdir()
            original = b'{"prior_evidence": "must survive"}\n'
            (output/'report.json').write_bytes(original)
            with self.assertRaisesRegex(ValueError, 'must be empty'):
                run(model, photos, output, resolution=128, camera_evaluations=20)
            self.assertEqual((output/'report.json').read_bytes(), original)
            self.assertEqual([path.name for path in output.iterdir()], ['report.json'])

    def test_invalid_view_and_missing_photo_fail_before_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            model, photos = _simple_inputs(folder)
            for label, path, error in [('diagonal', photos[1][1], ValueError),
                                       ('angled', folder/'does-not-exist.png', FileNotFoundError)]:
                with self.subTest(label=label, path=path):
                    output = folder/'run'
                    with self.assertRaises(error):
                        run(model, [photos[0], (label, path)], output, resolution=128, camera_evaluations=20)
                    self.assertFalse(output.exists())

    def test_opaque_partition_compacts_only_referenced_vertices(self):
        vertices = np.arange(24, dtype=float).reshape(8, 3)
        mesh = TriangleMesh(vertices.copy(), np.array([[1, 4, 6], [0, 2, 5]]),
                            [{'face_start': 0, 'face_count': 1, 'transmission': 0, 'declared_role': 'frame'},
                             {'face_start': 1, 'face_count': 1, 'transmission': 1, 'declared_role': 'lens'}])
        partition = opaque_partition(mesh)
        np.testing.assert_array_equal(partition.vertices, vertices[[1, 4, 6]])
        np.testing.assert_array_equal(partition.faces, [[0, 1, 2]])
        np.testing.assert_array_equal(mesh.vertices, vertices)
        np.testing.assert_array_equal(mesh.faces, [[1, 4, 6], [0, 2, 5]])
        # An all-opaque hypothesis adds no independent partition.
        whole = TriangleMesh(vertices, mesh.faces, [{'face_start': 0, 'face_count': 2, 'transmission': 0}])
        self.assertIsNone(opaque_partition(whole))

    def test_front_back_orthographic_views_have_zero_geometric_baseline(self):
        def constraint(label, yaw):
            camera = Camera(yaw, 0, 0, 0, 100, 80, 60)
            points = np.array([[0., 0, 0]])
            return ReprojectionConstraint(label, 'a'*64, 'b'*64, camera, points,
                                          project(points, camera), np.ones((1, 2)))
        front, back, side = constraint('front', 0), constraint('back', 180), constraint('left', 90)
        ambiguous = view_geometry_diagnostics([front, back])
        self.assertAlmostEqual(ambiguous['maximum_axis_separation_degrees'], 0., places=9)
        self.assertTrue(ambiguous['essentially_parallel_axes'])
        self.assertEqual(ambiguous['shape_identification'], 'unmeasured')
        separate = view_geometry_diagnostics([front, side])
        self.assertAlmostEqual(separate['maximum_axis_separation_degrees'], 90., places=9)
        self.assertFalse(separate['essentially_parallel_axes'])


class RefineCliTests(unittest.TestCase):
    def test_thin_triangle_backtracking_reassesses_each_real_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source, candidate = folder/'thin.glb', folder/'candidate.glb'
            mesh = TriangleMesh(np.array([[-1., 0, 0], [1, 0, 0], [0, .01, 0]]), np.array([[0, 1, 2]]), [])
            _write_mesh(source, mesh)
            original = source.read_bytes()
            # An injective field with det(J)=1 flips the retained thin triangle
            # at full strength. Only genuine shared-field backtracking can pass.
            displacements = np.zeros((3, 2, 2, 3))
            displacements[1, :, :, 1] = -.1
            cage = CageField([-1, -1, -1], [1, 1, 1], displacements)
            base = CageField(cage.lower, cage.upper, np.zeros_like(displacements))
            points = load_glb(source).vertices
            target = cage.transform(points)[0]
            constraints = []
            for label, yaw, char in [('front', 0, 'a'), ('angled', 40, 'b')]:
                camera = Camera(yaw, 0, 0, 0, 100, 128, 96)
                constraints.append(ReprojectionConstraint(label, char*64, char*64, camera, points,
                                                          project(target, camera), np.ones((3, 2))))
            selected, export, report = export_backtracked(source, candidate, cage, np.zeros(3), 1.,
                                                          _sha(source), constraints)
            self.assertIsNotNone(selected)
            self.assertGreater(report['selected_scale'], 0)
            self.assertLess(report['selected_scale'], 1)
            self.assertGreater(len(report['attempts']), 1)
            self.assertFalse(report['attempts'][0]['exported'])
            self.assertIn('inverted', report['attempts'][0]['reason'])
            self.assertTrue(report['attempts'][-1]['exported'])
            self.assertEqual(report['attempts'][-1]['assessment'], assess_proposal(base, selected, constraints))
            self.assertGreater(report['attempts'][-1]['assessment']['fit_proposal'][0]['rms_sigma'],
                               report['attempts'][0]['assessment']['fit_proposal'][0]['rms_sigma'])
            np.testing.assert_allclose(selected.displacements, cage.displacements*report['selected_scale'])
            output_mesh = load_glb(candidate)
            np.testing.assert_allclose(output_mesh.vertices, selected.transform(points)[0], atol=1e-8)
            np.testing.assert_array_equal(output_mesh.faces, mesh.faces)
            self.assertEqual(export['discrete_geometry']['inverted_triangles'], 0)
            self.assertEqual(export['discrete_geometry']['collapsed_triangles'], 0)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(export['output_sha256'], _sha(candidate))

    def test_real_cli_pins_evidence_preserves_source_and_scores_exported_mesh(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            mesh = _glasses_mesh()
            source = folder/'source.glb'
            _write_mesh(source, mesh)
            # One modest nonuniform deformation generates BOTH photographs.
            # Rasterization and the entire subprocess pipeline are real.
            changed = mesh.vertices.copy()
            changed[:, 1] = changed[:, 1]*1.04 + .025*(1-4*changed[:, 0]**2)
            lo, hi = mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)
            center, extent = (lo+hi)/2, float(np.max(hi-lo))
            target = TriangleMesh((changed-center)/extent, mesh.faces, [])
            photos = []
            for label, camera in [('front', Camera(0, 0, 0, 0, 192, 128, 93)),
                                  ('angled', Camera(40, 15, 0, .12, 192, 128, 93))]:
                mask = rasterize(target, camera, (192, 256)).mask
                pixels = np.full((*mask.shape, 3), 255, np.uint8)
                pixels[mask] = 0
                path = folder/f'{label}.png'
                Image.fromarray(pixels).save(path)
                photos.append((label, path))
            original = {path: path.read_bytes() for path in [source]+[path for _, path in photos]}
            output = folder/'run'
            command = [sys.executable, '-m', 'reconstruction.refine_photos', '--model', str(source),
                       '--output', str(output), '--resolution', '160', '--camera-evaluations', '60', '--rigid-refinement']
            for label, path in photos:
                command.extend(['--photo', f'{label}={path}'])
            process = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=180)
            self.assertEqual(process.returncode, 0, process.stdout+'\n'+process.stderr)
            report = json.loads((output/'report.json').read_text())
            self.assertEqual(report['quality_verdict'], 'unmeasured')
            self.assertEqual(report['source_sha256'], _sha(source))
            self.assertIn('refine_photos.py', report['implementation']['source_sha256'])
            self.assertIn('numpy', report['implementation']['packages'])
            for path, content in original.items():
                self.assertEqual(path.read_bytes(), content)
            frozen = json.loads((output/'frozen-correspondences.json').read_text())
            self.assertEqual(frozen['source_model_sha256'], _sha(source))
            self.assertEqual(frozen['normalization'], report['normalization'])
            self.assertEqual(len(frozen['views']), 2)
            for row in frozen['views']:
                path = dict(photos)[row['view_id']]
                self.assertEqual(row['source_sha256'], _sha(path))
                evidence = json.loads((output/row['view_id']/'evidence.json').read_text())
                decoded = PhotoEvidence.from_dict(evidence, expected_hash=row['evidence_sha256'])
                self.assertGreaterEqual(len(row['points_xyz']), 12)
                self.assertEqual(len(row['targets_xy']), len(row['points_xyz']))
                self.assertTrue(row['tangent_unconstrained'])
                self.assertEqual(len(row['normal_xy']), len(row['points_xyz']))
                self.assertEqual(len(decoded.components[0].edges), len(row['points_xyz']))
                self.assertFalse(decoded.components[0].landmarks)
            self.assertIn('deformation_fit', report)
            # This fixture reaches a real export; do not let its rerender checks
            # silently become dead code if the pipeline starts exiting early.
            self.assertTrue(report['deformation_fit']['retained'])
            self.assertIn('export', report)
            artifact = report.get('proposal_artifact') or report.get('rejected_artifact')
            self.assertIsNotNone(artifact)
            candidate = load_glb(output/artifact)
            self.assertEqual(report['export']['output_sha256'], _sha(output/artifact))
            normalized = TriangleMesh((candidate.vertices-center)/extent, candidate.faces, candidate.parts)
            for row, summary in zip(frozen['views'], report['views']):
                self.assertEqual(row['view_id'], summary['view_id'])
                self.assertEqual(summary['rerender']['geometry_source'], 'exported_glb_float32')
                self.assertFalse(summary['rerender']['independent_validation'])
                width, height = summary['image_size_working']
                actual = rasterize(normalized, Camera(**row['camera']), (height, width))
                measured = boundary_distance(actual, np.asarray(row['targets_xy']))
                for key in ('mean_px', 'p95_px', 'maximum_px'):
                    self.assertAlmostEqual(measured[key], summary['rerender']['proposal'][key], places=10)


if __name__ == '__main__':
    unittest.main()
