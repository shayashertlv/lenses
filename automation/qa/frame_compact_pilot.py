"""Replay frame support and lossless packaging on the three saved product cases."""
import argparse
import json
from pathlib import Path

from reconstruction.intrinsic_frame_appearance import run_intrinsic_frame_stage
from reconstruction.compact_glb import run_compact_asset


def run(output):
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    cases=[('vb','data/semantic-implementation/vb-search-fixed-renderer/selection.json',
            'data/jobs/victoria-beckham-fresh-v10/stages/hypothesis_regions/attempt_1/report.json'),
           ('miu','data/semantic-implementation/miumiu-search-v1/selection.json',
            'data/jobs/miumiu-fresh-v16/stages/hypothesis_regions/attempt_1/report.json'),
           ('oakley','data/semantic-repair/oakley-review-final/selection.json',
            'data/semantic-repair/oakley-integrated/regions/report.json')]
    rows=[]
    for name,selection,regions in cases:
        selected=json.loads(Path(selection).read_bytes())['selected']
        frame=run_intrinsic_frame_stage(selected['path'],selected['export']['path'],regions,output/name/'frame')
        chosen=frame['selected']
        compact=run_compact_asset(chosen['path'],output/name/'compact',optical_receipt=chosen['export']['path'])
        row={'case':name,'frame_status':frame['status'],'photos':frame['photo_ids'],'frame_materials':frame['materials'],
             'skipped':frame['skipped'],'source_bytes':compact['source_bytes'],'compact_bytes':compact['output_bytes'],
             'triangles':compact['triangles'],'compact':compact['model'],'export':compact['export']}
        rows.append(row); print(json.dumps(row),flush=True)
    (output/'report.json').write_text(json.dumps(rows,indent=2)+'\n')
    return rows


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True,type=Path)
    run(parser.parse_args().output)
