"""Re-evaluate a sealed semantic candidate with the current delivery implementation."""
import argparse
import json
from pathlib import Path

from reconstruction.delivery_stage import run_delivery_stage
from reconstruction.evaluation_stage import pin
from reconstruction.job import _completed
from reconstruction.refine_photos import implementation_manifest
from reconstruction.semantic_appearance_stage import render_material_cards


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=args.job.resolve();journal=json.loads((root/'job.json').read_bytes())
    snapshot=implementation_manifest()
    folder=lambda name:_completed(root,journal,name)[1]
    semantic=folder('semantic_appearance')
    selection=json.loads((semantic/'selection.json').read_bytes())['selected']
    refinement=_completed(root,journal,'completed_geometry_refinement') or _completed(root,journal,'refinement')
    result=run_delivery_stage(semantic/'candidate-appearance.glb',selection['export']['path'],
        folder('hypothesis_regions')/'report.json',args.output,renderer=render_material_cards,
        physical_report=folder('physical_groups')/'report.json',selection=selection,
        reconstruction_history_receipts=[pin(folder('multiview_intake')/'report.json')],
        refinement_reference=pin(refinement[1]/'report.json'))
    if implementation_manifest()!=snapshot:
        raise ValueError('Source changed during delivery replay')
    (args.output/'implementation.json').write_text(json.dumps(snapshot,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'quality':result['quality_verdict'],'model':result['model'],
                      'failed':result['quality']['failed_gates'],'unmeasured':result['quality']['unmeasured_gates']}))


if __name__=='__main__':main()
