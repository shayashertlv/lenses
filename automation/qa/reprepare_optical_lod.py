"""Prepare an explicitly new LOD geometry candidate; never reuse old bindings."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

from reconstruction.mesh import load_glb
from reconstruction.optical_groups import prepare_optical_group
from reconstruction.optical_group_asset import write_optical_group_candidate,read_optical_group_candidate
from reconstruction.surface_transfer import _chunks,_rows
from reconstruction.compact_glb import run_compact_asset


root=Path(sys.argv[1]).resolve()
source=root/'variant-source.glb'
selected=json.loads((root/'source-selection.json').read_bytes())
original_receipt=json.loads(Path(selected['export']['path']).read_bytes())
read_optical_group_candidate(Path(selected['path']),original_receipt)
raw=source.read_bytes();source_sha=hashlib.sha256(raw).hexdigest()
doc,binary=_chunks(raw);mesh=load_glb(source)
preparations=[]
for group in original_receipt['groups']:
    items=[]
    for member in group['members']:
        part=next(p for p in mesh.parts if p['node_index']==member['node_index'] and p['mesh_index']==member['mesh_index'])
        primitive=doc['meshes'][part['mesh_index']]['primitives'][part['primitive_index']]
        start,count=part['vertex_start'],part['vertex_count']
        # Exported group nodes already carry common-frame positions and identity matrices.
        if any(k in doc['nodes'][part['node_index']] for k in ('matrix','rotation','translation','scale')):
            raise ValueError('QA helper requires the exported common-frame optical nodes')
        normal=_rows(doc,binary,primitive['attributes']['NORMAL'])[1].astype(float)
        item={'id':'lod-'+member['id'],'source_binding':{'asset_sha256':source_sha,
              'node_index':part['node_index'],'mesh_index':part['mesh_index'],'primitive_index':part['primitive_index']},
              'coordinate_frame_id':group['coordinate_frame']['id'],
              'positions':mesh.vertices[start:start+count].copy(),
              'indices':mesh.faces[part['face_start']:part['face_start']+part['face_count']]-start,
              'normals':normal,'normal_transform':{'method':'identity','source_to_common_matrix':np.eye(4).tolist(),
                 'provenance':{'method':'retained_exported_smooth_optical_normal_attributes','source_sha256':source_sha}}}
        items.append(item)
    prepared=prepare_optical_group(group['group_id'],items,
        identity={'status':'unverified','provenance':{'method':'new_optical_lod_geometry_hypothesis',
            'prior_candidate_sha256':selected['sha256'],'prior_geometry_lineage_preserved':False}},
        coordinate_frame=deepcopy(group['coordinate_frame']))
    if prepared['report']['status']!='prepared_candidate':raise ValueError(prepared['report'])
    (root/(group['group_id']+'-preparation.json')).write_text(json.dumps(prepared['report'],indent=2))
    preparations.append({'prepared':prepared,'appearance':group['appearance']})
receipt=write_optical_group_candidate(source,root/'candidate.glb',preparations,source_sha256=source_sha,
    provenance={'method':'qa_new_optical_and_frame_geometry_lod_v1','prior_candidate_sha256':selected['sha256'],
        'prior_geometry_lineage_preserved':False,'photo_fit_transferred_without_refitting':True,
        'geometry_receipt_sha256':hashlib.sha256((root/'geometry.json').read_bytes()).hexdigest()})
(root/'candidate.export.json').write_text(json.dumps(receipt,indent=2))
compact=run_compact_asset(root/'candidate.glb',root/'compact',receipt)
new_receipt=json.loads((root/'compact/compact.export.json').read_bytes())
cases=[{'id':'baseline','path':selected['path'],'model_sha256':selected['sha256'],
        'groups':original_receipt['groups'],'width_mm':138.},
       {'id':'optical-frame-lod','path':str(root/'compact/compact.glb'),
        'model_sha256':new_receipt['output_sha256'],'groups':new_receipt['groups'],'width_mm':138.}]
(root/'runtime-manifest.json').write_text(json.dumps({'schema_version':1,'cases':cases},indent=2))
print(json.dumps({key:compact[key] for key in ('triangles','vertices','output_bytes')}))
