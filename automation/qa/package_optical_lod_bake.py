"""QA-only frame normal bake on the freshly prepared optical LOD candidate."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

from PIL import Image
from reconstruction.surface_transfer import _chunks,_pack
from reconstruction.optical_group_asset import _seal,read_optical_group_candidate
from reconstruction.compact_glb import run_compact_asset

root=Path(sys.argv[1]).resolve()
image_path=root/'normal-baked-q92.jpg'
Image.open(root/'normal-baked.png').convert('RGB').save(image_path,quality=92,subsampling=0,optimize=True)
receipt=json.loads((root/'candidate.export.json').read_bytes())
document,old=_chunks((root/'candidate.glb').read_bytes());binary=bytearray(old)
raw=image_path.read_bytes();binary.extend(b'\0'*(-len(binary)%4))
view=len(document['bufferViews']);document['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(raw)})
binary.extend(raw);document['buffers'][0]['byteLength']=len(binary)
image=len(document['images']);document['images'].append({'bufferView':view,'mimeType':'image/jpeg','name':'Complete source frame-normal bake'})
normal=document['materials'][0]['normalTexture'];old_texture=document['textures'][normal['index']]
texture=len(document['textures']);document['textures'].append({**old_texture,'source':image})
normal['index']=texture
output=root/'candidate-baked.glb';raw=_pack(document,binary);output.write_bytes(raw)
receipt=deepcopy(receipt);receipt.pop('receipt_sha256',None)
receipt.update(output=str(output),output_sha256=hashlib.sha256(raw).hexdigest(),
               frame_normal_bake={'method':'qa_complete_source_frame_normal_bake_v1',
                   'normal_texture_sha256':hashlib.sha256(image_path.read_bytes()).hexdigest(),
                   'bake_report_sha256':hashlib.sha256((root/'bake-report.json').read_bytes()).hexdigest()})
receipt=_seal(receipt);read_optical_group_candidate(output,receipt)
(root/'candidate-baked.export.json').write_text(json.dumps(receipt,indent=2))
compact=run_compact_asset(output,root/'compact-baked',receipt)
manifest=json.loads((root/'runtime-manifest.json').read_bytes())
export=json.loads((root/'compact-baked/compact.export.json').read_bytes())
manifest['cases'].append({'id':'optical-frame-lod-baked','path':str(root/'compact-baked/compact.glb'),
                          'model_sha256':export['output_sha256'],'groups':export['groups'],'width_mm':138.})
(root/'runtime-manifest-baked.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps({key:compact[key] for key in ('triangles','vertices','output_bytes')}))
