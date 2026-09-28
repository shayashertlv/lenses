"""Real frame simplification, optical preservation and image regression controls."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image
import pytest

from reconstruction.mobile_lod import run_mobile_lod, compare_lod_cards
from reconstruction.compact_glb import run_compact_asset
from reconstruction.optical_group_asset import write_optical_group_candidate, read_optical_group_candidate
from reconstruction.surface_transfer import _chunks, _pack
from test_surface_transfer import build_generation
from test_optical_group_asset import groups_for


class MobileLODTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)

    @pytest.mark.slow   # ~34 s
    def test_real_simplification_preserves_optics_and_every_authored_frame_attribute(self):
        n=20
        vertices=[[x/n,y/n,0.] for y in range(n+1) for x in range(n+1)]
        uv=[[x/n,y/n] for y in range(n+1) for x in range(n+1)]
        faces=[]
        for y in range(n):
            for x in range(n):
                i=y*(n+1)+x;faces.extend([[i,i+1,i+n+2],[i,i+n+2,i+n+1]])
        doc,binary=_chunks(build_generation(vertices,uv,faces))
        # The second instance becomes optical; the first remains a UV frame.
        doc['nodes'].append({'mesh':0,'translation':[2.,0.,0.]});doc['scenes'][0]['nodes'].append(1)
        source=self.root/'source.glb';source.write_bytes(_pack(doc,binary));model=self.root/'optical.glb'
        receipt=write_optical_group_candidate(source,model,groups_for(source,[('lens',[1])]),
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),provenance={'method':'LOD control'})
        report=run_mobile_lod(model,self.root/'lod',optical_receipt=receipt,target_triangles=1000)
        self.assertTrue(any(p['lod_triangles']<p['source_triangles'] for p in report['primitives']))
        before=read_optical_group_candidate(model,receipt)
        saved=json.loads(Path(report['export']['path']).read_bytes())
        after=read_optical_group_candidate(report['model']['path'],saved)
        for key in ('appearance_sha256','members'):
            self.assertEqual(receipt['groups'][0][key],saved['groups'][0][key])
        before_doc,before_bin=_chunks(model.read_bytes());after_doc,after_bin=_chunks(Path(report['model']['path']).read_bytes())
        self.assertEqual(after_bin[:len(before_bin)],before_bin)
        self.assertEqual(before_doc['meshes'][0]['primitives'][0]['attributes'],after_doc['meshes'][0]['primitives'][0]['attributes'])
        self.assertEqual(before_doc['materials'],after_doc['materials'])
        self.assertEqual(before['groups'][0]['appearance'],after['groups'][0]['appearance'])
        compact=run_compact_asset(report['model']['path'],self.root/'compact',optical_receipt=saved)
        self.assertLess(compact['triangles'],report['source_triangles'])

    def test_small_logo_loss_fails_even_when_whole_card_average_is_small(self):
        reference=np.full((1024,1024,3),128,np.uint8);candidate=reference.copy()
        candidate[100:112,100:112]=0
        a,b=self.root/'a.png',self.root/'b.png';Image.fromarray(reference).save(a);Image.fromarray(candidate).save(b)
        self.assertTrue(compare_lod_cards(a,a)['passed'])
        result=compare_lod_cards(a,b)
        self.assertLess(result['metrics']['mean_max_channel_codes'],.35)
        self.assertFalse(result['passed'])
        self.assertGreater(result['metrics']['worst_32px_tile_mean_codes'],4.)


if __name__=='__main__':unittest.main()
