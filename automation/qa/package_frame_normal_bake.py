"""QA-only packaging of frame normal bake; optical arrays stay byte-exact."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

from reconstruction.surface_transfer import _chunks,_pack
from reconstruction.optical_group_asset import read_optical_group_candidate,_seal
from reconstruction.compact_glb import run_compact_asset

p=Path(sys.argv[3] if len(sys.argv)>3 else 'data/build-five/oakley-normal-bake-pilot').resolve()
tag=sys.argv[2] if len(sys.argv)>2 else 'png'
image_file=p/(sys.argv[1] if len(sys.argv)>1 else 'normal-baked.png')
source=Path('data/frame-compact-pilot-v1/oakley/frame/baseline.glb').resolve()
receipt=json.loads(source.with_suffix('.export.json').read_text())
doc,raw=_chunks((p/(sys.argv[4] if len(sys.argv)>4 else 'lod.glb')).read_bytes());binary=bytearray(raw)
png=image_file.read_bytes();binary.extend(b'\0'*(-len(binary)%4))
view=len(doc['bufferViews']);doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(png)})
binary.extend(png);doc['buffers'][0]['byteLength']=len(binary)
image=len(doc['images']);doc['images'].append({'bufferView':view,'mimeType':'image/jpeg' if image_file.suffix=='.jpg' else 'image/png','name':'Baked complete source frame normals'})
texture=len(doc['textures']);sampler=doc['textures'][doc['materials'][0]['normalTexture']['index']].get('sampler')
doc['textures'].append({'source':image,**({'sampler':sampler} if sampler is not None else {})})
doc['materials'][0]['normalTexture']={**doc['materials'][0]['normalTexture'],'index':texture}
result=_pack(doc,binary);model=p/f'lod-normal-{tag}.glb';model.write_bytes(result)
updated=deepcopy(receipt);updated.pop('receipt_sha256',None)
updated.update(output=str(model),output_sha256=hashlib.sha256(result).hexdigest(),
               frame_lod={'method':'qa_source_normal_baked_frame_lod','geometry_receipt':'geometry.json','bake_receipt':'bake-report.json'})
updated=_seal(updated);read_optical_group_candidate(model,updated)
(p/f'lod-normal-{tag}.export.json').write_text(json.dumps(updated,indent=2))
compact=run_compact_asset(model,p/f'compact-normal-{tag}',updated)
cases=[]
baseline=json.loads(Path('data/build-five/oakley-delivery-v3/runtime-manifest.json').read_text())['cases'][0]
cases.append(baseline)
for name,model,export in [('lod-normal-'+tag,p/f'compact-normal-{tag}'/'compact.glb',p/f'compact-normal-{tag}'/'compact.export.json')]:
    r=json.loads(export.read_text());cases.append({'id':name,'path':str(model),'model_sha256':r['output_sha256'],'groups':r['groups'],'width_mm':138.})
(p/f'runtime-manifest-{tag}.json').write_text(json.dumps({'schema_version':1,'cases':cases},indent=2))
print(json.dumps({k:compact[k] for k in ('output_bytes','triangles','vertices')}))
