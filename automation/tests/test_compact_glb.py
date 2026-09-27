"""Lossless storage pruning, active-scene identity and portable optical reload."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from reconstruction.compact_glb import (compact_glb_bytes,run_compact_asset,active_semantics_sha256)
from reconstruction.surface_transfer import _chunks,_pack
from reconstruction.mesh import load_glb_bytes
from reconstruction.optical_group_asset import (write_optical_group_candidate,read_optical_group_candidate,_seal)
from test_surface_transfer import generation_glb, build_generation
from test_optical_group_asset import groups_for


class CompactGLBTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)

    def inflated(self):
        doc,binary=_chunks(generation_glb())
        doc['nodes'].append({'mesh':0,'name':'unreachable old generation'})
        doc['meshes'].append(deepcopy(doc['meshes'][0]))
        doc['materials'].append(deepcopy(doc['materials'][0]))
        doc['accessors'].append(deepcopy(doc['accessors'][0]))
        doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':25000})
        binary += b'X'*25000; doc['buffers'][0]['byteLength']=len(binary)
        return _pack(doc,binary)

    def test_prunes_unreachable_storage_and_roundtrips_live_scene_exactly(self):
        raw=self.inflated(); compact,proof,receipt=compact_glb_bytes(raw)
        self.assertIsNone(receipt)
        self.assertGreater(proof['bytes_removed'],24000)
        self.assertEqual(proof['triangles'],2)
        self.assertFalse(proof['geometry_decimation'])
        self.assertEqual(proof['records_after']['nodes'],1)
        self.assertEqual(proof['records_after']['meshes'],1)
        self.assertEqual(proof['records_after']['materials'],1)
        np.testing.assert_array_equal(load_glb_bytes(raw).vertices,load_glb_bytes(compact).vertices)
        np.testing.assert_array_equal(load_glb_bytes(raw).faces,load_glb_bytes(compact).faces)
        self.assertEqual(active_semantics_sha256(*_chunks(raw)),active_semantics_sha256(*_chunks(compact)))

    def test_deduplicates_shared_attributes_and_identical_texture_payloads(self):
        doc,binary=_chunks(generation_glb())
        doc['meshes'].append(deepcopy(doc['meshes'][0])); doc['nodes'].append({'mesh':1,'translation':[3,0,0]})
        doc['scenes'][0]['nodes'].append(1)
        doc['materials'].append(deepcopy(doc['materials'][0])); doc['meshes'][1]['primitives'][0]['material']=1
        doc['textures'].append(deepcopy(doc['textures'][0])); doc['materials'][1]['pbrMetallicRoughness']['baseColorTexture']['index']=1
        compact,proof,_=compact_glb_bytes(_pack(doc,binary)); actual,_=_chunks(compact)
        self.assertEqual(proof['records_after']['textures'],1)
        self.assertEqual(proof['records_after']['images'],1)
        self.assertEqual(actual['meshes'][0]['primitives'][0]['attributes'],actual['meshes'][1]['primitives'][0]['attributes'])
        self.assertEqual(actual['meshes'][0]['primitives'][0]['indices'],actual['meshes'][1]['primitives'][0]['indices'])

    def test_removes_only_unreferenced_frame_vertices_preserving_uv_corner_values(self):
        raw=build_generation([[0,0,0],[1,0,0],[1,1,0],[.2,.3,0]],[[0,0],[1,0],[1,1],[.2,.3]],[[0,1,2]])
        compact,proof,_=compact_glb_bytes(raw)
        self.assertEqual(proof['unreferenced_frame_vertices_removed'],1)
        self.assertEqual(proof['vertices'],3)
        self.assertEqual(active_semantics_sha256(*_chunks(raw)),active_semantics_sha256(*_chunks(compact)))

    def test_retains_unreferenced_extrema_used_by_actual_ar_bounds_consumers(self):
        raw=build_generation([[0,0,0],[1,0,0],[1,1,0],[10,20,30]],[[0,0],[1,0],[1,1],[.2,.3]],[[0,1,2]])
        compact,proof,_=compact_glb_bytes(raw)
        self.assertEqual(proof['unreferenced_frame_vertices_removed'],0)
        a,b=load_glb_bytes(raw),load_glb_bytes(compact)
        np.testing.assert_array_equal(a.vertices.min(axis=0),b.vertices.min(axis=0))
        np.testing.assert_array_equal(a.vertices.max(axis=0),b.vertices.max(axis=0))

    def test_alternate_scenes_and_transforms_are_preserved(self):
        doc,binary=_chunks(generation_glb()); doc['nodes'].append({'mesh':0,'scale':[-1,2,1]})
        doc['scenes'].append({'name':'other scene','nodes':[1]})
        raw=_pack(doc,binary); compact,proof,_=compact_glb_bytes(raw)
        self.assertEqual(proof['records_after']['nodes'],2)
        self.assertEqual(active_semantics_sha256(*_chunks(raw)),active_semantics_sha256(*_chunks(compact)))

    def optical(self):
        source=self.root/'source.glb'; source.write_bytes(self.inflated())
        doc,binary=_chunks(source.read_bytes()); doc['nodes'].append({'mesh':0,'translation':[3,0,0]})
        doc['scenes'][0]['nodes'].append(len(doc['nodes'])-1); source.write_bytes(_pack(doc,binary))
        output=self.root/'optical.glb'
        receipt=write_optical_group_candidate(source,output,groups_for(source,[('lens',[1])]),
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),provenance={'method':'compact fixture'})
        return output,receipt

    def test_optical_asset_prunes_source_prefix_and_reloads_with_strict_remapped_receipt(self):
        source,receipt=self.optical()
        report=run_compact_asset(source,self.root/'compact',optical_receipt=receipt)
        compact=Path(report['model']['path']); updated=json.loads(Path(report['export']['path']).read_bytes())
        before=read_optical_group_candidate(source,receipt); after=read_optical_group_candidate(compact,updated)
        self.assertGreater(report['bytes_removed'],24000)
        self.assertEqual(before['groups'][0]['appearance'],after['groups'][0]['appearance'])
        self.assertEqual(before['groups'][0]['group_id'],after['groups'][0]['group_id'])
        self.assertEqual(updated['groups'][0]['members'][0]['attribute_sha256'],receipt['groups'][0]['members'][0]['attribute_sha256'])
        self.assertEqual(updated['compact_storage']['source_receipt'],receipt)
        self.assertNotEqual(updated['groups'][0]['members'][0]['node_index'],receipt['groups'][0]['members'][0]['node_index'])
        self.assertTrue((self.root/'compact'/'report.json').is_file())

    def test_compact_receipt_does_not_allow_changed_frame_material_or_optical_coordinates(self):
        source,receipt=self.optical(); report=run_compact_asset(source,self.root/'compact',optical_receipt=receipt)
        path=Path(report['model']['path']); raw=path.read_bytes(); saved=json.loads(Path(report['export']['path']).read_bytes())
        for variant in ('frame_material','optical_positions'):
            doc,binary=_chunks(raw); binary=bytearray(binary)
            if variant=='frame_material':
                doc['materials'][0]['pbrMetallicRoughness']['roughnessFactor']=.13
            else:
                member=saved['groups'][0]['members'][0]
                primitive=doc['meshes'][member['mesh_index']]['primitives'][0]
                accessor=doc['accessors'][primitive['attributes']['POSITION']]
                start=doc['bufferViews'][accessor['bufferView']]['byteOffset']
                values=np.frombuffer(binary,dtype='<f4',count=3,offset=start); values[0]+=.1
            changed=_pack(doc,binary); path.write_bytes(changed)
            updated=deepcopy(saved); updated.pop('receipt_sha256'); updated['output_sha256']=hashlib.sha256(changed).hexdigest(); updated=_seal(updated)
            with self.subTest(variant=variant),self.assertRaisesRegex(ValueError,'active material/geometry semantics'):
                read_optical_group_candidate(path,updated)

    def test_unverified_compact_claim_cannot_skip_original_prefix_validation(self):
        source,receipt=self.optical()
        forged=deepcopy(receipt); forged.pop('receipt_sha256'); forged['compact_storage']={'method':'pretend'}; forged=_seal(forged)
        with self.assertRaisesRegex(ValueError,'Invalid compact optical storage proof'):
            read_optical_group_candidate(source,forged)
        with self.assertRaisesRegex(ValueError,'trusted export receipt'):
            compact_glb_bytes(source.read_bytes())


if __name__=='__main__':
    unittest.main()
