"""Promote owned provider side/back photographs to reconstruction evidence."""
import hashlib
import json
from pathlib import Path

from .input_bundle import _normalize
from .semantic_appearance_stage import collect_semantic_photo_inputs, _evidence_view_label


def run_multiview_intake(photos, initializer, output):
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()):raise ValueError('Multiview intake output must be fresh')
    captured=[];omitted=[];seen=set()
    for photo in collect_semantic_photo_inputs(photos,initializer):
        path=Path(photo['path']).resolve();raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=photo['sha256']:raise ValueError('Multiview source snapshot changed')
        view=_evidence_view_label(photo.get('view','unknown'))
        record,png=_normalize(raw,photo['id'],view,path)
        pixel=record['normalized']['pixel_sha256']
        if pixel in seen:
            omitted.append({'id':photo['id'],'reason':'duplicate_normalized_pixels','source_sha256':photo['sha256']});continue
        seen.add(pixel);captured.append((record,png))
    output.mkdir(parents=True,exist_ok=True);(output/'normalized').mkdir()
    rows=[]
    for record,png in captured:
        path=output/record['normalized']['path'];path.write_bytes(png)
        rows.append({'id':record['id'],'view':record['view'],'path':str(path),'sha256':record['normalized']['sha256'],
                     'color_space':record['normalized']['color_space'],
                     'normalization_provenance':{'original':record['original'],'normalized':record['normalized']}})
    report={'schema_version':1,'method':'all_owned_views_reconstruction_intake_v1','photos':rows,'omitted':omitted,
            'scope':'Every distinct owned photograph can supply camera/geometry/frame evidence; alpha does not imply a known optical background.'}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report
