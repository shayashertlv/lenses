"""Frame-only LOD proposals; actual AR image checks decide whether to retain one."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image

from .optical_group_asset import read_optical_group_candidate, _seal


def run_mobile_lod(model, output, *, optical_receipt, target_triangles=150_000):
    if type(target_triangles) is not int or not 4<=target_triangles<=10_000_000:
        raise ValueError('LOD target must be an integer triangle count in 4..10000000')
    model,output=Path(model).resolve(),Path(output).resolve()
    if output.exists() and any(output.iterdir()):raise ValueError('LOD output must be fresh')
    receipt=json.loads(Path(optical_receipt).read_bytes()) if not isinstance(optical_receipt,dict) else deepcopy(optical_receipt)
    read_optical_group_candidate(model,receipt)
    if receipt.get('compact_storage'):raise ValueError('Propose LOD before final lossless storage compaction')
    output.mkdir(parents=True,exist_ok=True)
    script=Path(__file__).resolve().parents[1]/'scripts'/'mesh_lod.mjs'
    dependency=script.parents[2]/'ar/node_modules/meshoptimizer/meshopt_simplifier.js'
    sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    pins={str(path):sha(path) for path in (model,script,dependency)}
    result=subprocess.run(['node',str(script),str(model),str(output/'lod.glb'),str(output/'geometry.json'),str(target_triangles)],capture_output=True,text=True,timeout=180)
    if result.returncode:raise ValueError('Frame simplification failed: '+result.stderr[-1800:])
    if any(sha(Path(path))!=expected for path,expected in pins.items()):
        raise ValueError('LOD source or simplifier changed during execution')
    report=json.loads((output/'geometry.json').read_bytes());updated=deepcopy(receipt)
    if report['source_sha256']!=pins[str(model)]:raise ValueError('LOD source receipt differs')
    report['implementation_sha256']={key:value for key,value in pins.items() if key!=str(model)}
    updated.pop('receipt_sha256',None);updated.update(output=str(output/'lod.glb'),output_sha256=report['output_sha256'])
    updated['frame_lod']={k:v for k,v in report.items() if k!='primitives'};updated=_seal(updated)
    read_optical_group_candidate(output/'lod.glb',updated,expected_sha256=report['output_sha256'])
    (output/'lod.export.json').write_text(json.dumps(updated,indent=2)+'\n',encoding='utf-8')
    report.update(model={'path':str(output/'lod.glb'),'sha256':report['output_sha256']},
                  export={'path':str(output/'lod.export.json'),'sha256':hashlib.sha256((output/'lod.export.json').read_bytes()).hexdigest()})
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


def compare_lod_cards(reference, candidate):
    """Fixed image gates, including worst occupied tile so small logos matter."""
    a=np.asarray(Image.open(reference).convert('RGB'),float);b=np.asarray(Image.open(candidate).convert('RGB'),float)
    if a.shape!=b.shape:raise ValueError('LOD cards use different pixel grids')
    error=np.max(np.abs(a-b),axis=2);tiles=[]
    for y in range(0,len(error),32):
        for x in range(0,error.shape[1],32):
            tile=error[y:y+32,x:x+32]
            if np.any(tile>0):tiles.append(float(tile.mean()))
    metrics={'mean_max_channel_codes':float(error.mean()),'pixels_over_12_fraction':float(np.mean(error>12)),
             'worst_32px_tile_mean_codes':max(tiles,default=0.)}
    return {'method':'fixed_multiview_lod_image_nonregression_v1','metrics':metrics,
            'limits':{'mean_max_channel_codes':.35,'pixels_over_12_fraction':.003,'worst_32px_tile_mean_codes':4.},
            'passed':metrics['mean_max_channel_codes']<=.35 and metrics['pixels_over_12_fraction']<=.003 and metrics['worst_32px_tile_mean_codes']<=4.}
