from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import reconstruction.partition_stage as stage
from reconstruction.mesh_components import ComponentCapacity
from reconstruction.partition_glb import inspect_partition_source, verify_partition_glb_bytes
import test_partition_glb as fixture_tools


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class PartitionStageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source, self.output = self.folder/'model.glb', self.folder/'output'
        self.raw, _ = fixture_tools.fixture()
        self.source.write_bytes(self.raw)
        self.implementation = {'source_sha256': {'fixture_runtime': 'frozen'}, 'packages': {'numpy': np.__version__}}
        self.manifest = patch.object(stage, 'implementation_manifest', return_value=self.implementation).start()
        self.addCleanup(patch.stopall)

    def assert_no_output(self):
        self.assertFalse(self.output.exists())

    def read_artifact(self, report, name):
        reference = report[name]
        raw = (self.output/reference['path']).read_bytes()
        self.assertEqual(sha(raw), reference['sha256'])
        return raw

    def test_inventory_retains_invisible_instances_and_full_face_membership(self):
        report = stage.run_component_inventory(self.source, self.output)
        self.assertEqual(report['status'], 'component_inventory_complete')
        self.assertEqual(report['primitive_count'], 4)
        self.assertEqual(report['source_face_count'], 16)
        self.assertEqual(report['semantic_identity'], 'not_inferred')
        self.assertFalse(report['accepted'])
        self.assertEqual(self.read_artifact(report, 'source_snapshot'), self.raw)
        actual = inspect_partition_source(self.raw)
        with np.load(io.BytesIO(self.read_artifact(report, 'labels')), allow_pickle=False) as labels:
            self.assertEqual(len(labels.files), 4)
            for row, instance in zip(report['primitives'], actual['instances'], strict=True):
                self.assertEqual(row['source_binding'], instance['source_binding'])
                values = labels[row['labels']['npz_key']]
                self.assertEqual(values.dtype, np.dtype('int64'))
                self.assertEqual(len(values), len(instance['indices']))
                self.assertEqual(sha(values.tobytes()), row['labels']['sha256'])
                for component in row['components']:
                    selected = np.flatnonzero(values == component['component_id'])
                    self.assertEqual(len(selected), component['face_count'])
                    self.assertEqual(int(selected[0]), component['first_source_face'])
                self.assertEqual(row['positions']['sha256'], sha(instance['positions'].tobytes()))
                self.assertEqual(row['indices']['sha256'], sha(instance['indices'].tobytes()))
        self.assertEqual(sum(p['source_material_index'] == 1 for p in report['primitives']), 2)
        self.assertEqual({p['source_binding']['node_index'] for p in report['primitives']}, {1, 2})
        self.assertEqual(json.loads((self.output/'report.json').read_bytes()), report)
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertEqual(set(p.name for p in self.output.iterdir()), {'source.glb', 'labels.npz', 'report.json'})

    def test_inventory_local_bounds_ignore_unused_vertices(self):
        doc, binary = fixture_tools.unpack(self.raw); binary = bytearray(binary)
        ai = doc['meshes'][0]['primitives'][0]['indices']
        acc = doc['accessors'][ai]; view = doc['bufferViews'][acc['bufferView']]
        start = view.get('byteOffset', 0)+acc.get('byteOffset', 0)
        values = np.tile([0, 1, 2], (4, 1)).astype('<u2').tobytes()
        binary[start:start+len(values)] = values
        self.source.write_bytes(fixture_tools.pack(doc, bytes(binary)))
        report = stage.run_component_inventory(self.source, self.output)
        for row in report['primitives']:
            self.assertEqual(row['source_vertex_count'], 6)
            self.assertEqual(row['referenced_vertex_count'], 3)
            self.assertEqual(row['components'][0]['local_bounds'], [[0., 0., 0.], [1., 1., 0.]])
            self.assertEqual(row['components'][0]['face_count'], 4)
        self.assertEqual(report['coordinate_space'], 'source_local')

    def test_inventory_modes_keep_nonindexed_attribute_seams_explicit(self):
        self.raw, _ = fixture_tools.fixture(indexed=False)
        self.source.write_bytes(self.raw)
        report = stage.run_component_inventory(self.source, self.output,
                                                connectivity='shared_index_vertex')
        self.assertEqual(report['component_count'], 16)
        self.assertTrue(all(row['component_count'] == 4 for row in report['primitives']))

    def test_inventory_capacity_failure_creates_no_artifacts(self):
        with self.assertRaisesRegex(ValueError, 'face count'):
            stage.run_component_inventory(self.source, self.output,
                                           capacity=ComponentCapacity(maximum_faces=3))
        self.assert_no_output()
        with patch.object(stage, 'MAXIMUM_RECORDED_COMPONENTS', 1):
            with self.assertRaisesRegex(ValueError, 'recorded-component capacity'):
                stage.run_component_inventory(self.source, self.output)
        self.assert_no_output()

    def test_partition_writes_exact_inputs_and_independently_verified_output(self):
        declared = fixture_tools.declaration(self.raw)
        path = self.folder/'declarations.json'
        declaration_raw = ('  '+json.dumps(declared, indent=4)+'\n\n').encode()
        path.write_bytes(declaration_raw)
        report = stage.run_face_partition(self.source, self.output, declarations=path)
        self.assertEqual(report['status'], 'face_partition_exported')
        self.assertFalse(report['accepted'])
        self.assertEqual(self.read_artifact(report, 'source_snapshot'), self.raw)
        self.assertEqual(self.read_artifact(report, 'declarations'), declaration_raw)
        receipt = json.loads(self.read_artifact(report, 'receipt'))
        model = self.read_artifact(report, 'model')
        verified = verify_partition_glb_bytes(self.raw, model, receipt)
        self.assertEqual(verified, report['verification'])
        self.assertEqual(verified['triangle_occurrences_checked'], 16)
        self.assertEqual(report['output_primitive_count'], 5)
        self.assertEqual(report['render_equivalence'], 'unmeasured')
        self.assertEqual(self.source.read_bytes(), self.raw)
        self.assertEqual(path.read_bytes(), declaration_raw)
        self.assertEqual(set(p.name for p in self.output.iterdir()),
                         {'source.glb', 'declarations.json', 'partitioned.glb', 'receipt.json', 'report.json'})

    def test_partition_source_mismatch_and_incomplete_membership_fail_before_write(self):
        for change in ('source', 'faces'):
            declared = fixture_tools.declaration(self.raw)
            if change == 'source':
                declared['source_sha256'] = '0'*64
            else:
                declared['partitions'][0]['pieces'][0]['source_face_indices'] = [3]
            with self.assertRaises(ValueError):
                stage.run_face_partition(self.source, self.output, declarations=declared)
            self.assert_no_output()

    def test_duplicate_json_keys_and_nonfinite_inline_declarations_reject(self):
        path = self.folder/'declarations.json'
        path.write_text('{"schema_version": 1, "schema_version": 1}')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON key'):
            stage.run_face_partition(self.source, self.output, declarations=path)
        self.assert_no_output()
        declared = fixture_tools.declaration(self.raw)
        declared['provenance']['invalid'] = float('nan')
        with self.assertRaises(ValueError):
            stage.run_face_partition(self.source, self.output, declarations=declared)
        self.assert_no_output()

    def test_source_mutation_during_inventory_computation_rejects_before_write(self):
        original = stage.inspect_partition_source
        def changed(raw):
            result = original(raw)
            self.source.write_bytes(raw+b'changed')
            return result
        with patch.object(stage, 'inspect_partition_source', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Source or declarations changed'):
                stage.run_component_inventory(self.source, self.output)
        self.assert_no_output()

    def test_declaration_mutation_during_partition_computation_rejects_before_write(self):
        path = self.folder/'declarations.json'
        path.write_text(json.dumps(fixture_tools.declaration(self.raw)))
        original = stage.partition_glb_bytes
        def changed(raw, declared):
            result = original(raw, declared)
            path.write_text(path.read_text()+' ')
            return result
        with patch.object(stage, 'partition_glb_bytes', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Source or declarations changed'):
                stage.run_face_partition(self.source, self.output, declarations=path)
        self.assert_no_output()

    def test_code_mutation_before_writes_rejects_without_output(self):
        self.manifest.side_effect = [self.implementation, {'changed': True}]
        with self.assertRaisesRegex(ValueError, 'Implementation changed'):
            stage.run_component_inventory(self.source, self.output)
        self.assert_no_output()

    def test_mutation_after_artifact_write_cannot_create_terminal_report(self):
        original = stage._write
        def changed(output, name, raw):
            result = original(output, name, raw)
            if name == 'source.glb':
                self.source.write_bytes(self.raw+b'changed')
            return result
        with patch.object(stage, '_write', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Source or declarations changed'):
                stage.run_face_partition(self.source, self.output,
                                         declarations=fixture_tools.declaration(self.raw))
        self.assertTrue((self.output/'source.glb').is_file())
        self.assertFalse((self.output/'report.json').exists())

    def test_changed_output_artifact_cannot_create_terminal_report(self):
        original = stage._write
        def changed(output, name, raw):
            result = original(output, name, raw)
            if name == 'labels.npz':
                (output/'source.glb').write_bytes(b'tampered')
            return result
        with patch.object(stage, '_write', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'artifact changed'):
                stage.run_component_inventory(self.source, self.output)
        self.assertFalse((self.output/'report.json').exists())

    def test_existing_output_and_source_containment_are_rejected(self):
        self.output.mkdir(); marker = self.output/'keep.txt'; marker.write_text('preserve')
        for function, options in ((stage.run_component_inventory, {}),
                                  (stage.run_face_partition, {'declarations': fixture_tools.declaration(self.raw)})):
            with self.assertRaises(ValueError):
                function(self.source, self.output, **options)
            self.assertEqual(marker.read_text(), 'preserve')
            with self.assertRaises(ValueError):
                function(self.source, self.folder, **options)
        self.assertEqual(set(p.name for p in self.output.iterdir()), {'keep.txt'})

    def test_both_cli_subcommands_execute_actual_stages(self):
        with redirect_stdout(io.StringIO()) as stdout:
            inventory = stage.main(['inventory', '--model', str(self.source), '--output', str(self.output)])
        self.assertIn('component_inventory_complete', stdout.getvalue())
        self.assertEqual(inventory['source_face_count'], 16)
        declaration_path = self.folder/'declaration.json'
        declaration_path.write_text(json.dumps(fixture_tools.declaration(self.raw)))
        with redirect_stdout(io.StringIO()) as stdout:
            partition = stage.main(['partition', '--model', str(self.source),
                                    '--output', str(self.folder/'partition'),
                                    '--declarations', str(declaration_path)])
        self.assertIn('face_partition_exported', stdout.getvalue())
        self.assertEqual(partition['verification']['status'], 'verified')


if __name__ == '__main__':
    unittest.main()
