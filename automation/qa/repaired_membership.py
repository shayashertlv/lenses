"""Replay physical grouping on saved camera/photo evidence without a provider call."""
import argparse
import json
from pathlib import Path
from reconstruction.physical_group_stage import run_physical_group_stage
from reconstruction.photo_apertures import OfflineLensApertureEngine


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--weights',type=Path,required=True)
    args=parser.parse_args()
    physical=args.job/'stages/physical_groups/attempt_1'
    inventory=json.loads((physical/'inventory/report.json').read_bytes())
    model=physical/'inventory'/inventory['source_snapshot']['path']
    refined=json.loads((args.job/'stages/refinement/attempt_1/report.json').read_bytes())
    photos=[{'id':v['view_id'],'path':v['source'],'sha256':v['source_sha256']} for v in refined['views'] if 'camera_fit' in v]
    result=run_physical_group_stage(model,refined,photos,args.output,aperture_engine=OfflineLensApertureEngine(args.weights))
    print(json.dumps({'status':result['status'],'selected':result['selected_hypothesis'],'hypotheses':[
        {'index':h['index'],'variant':h['role_variant'],'bridge':h['bridge']['status'], 'rank':h['composition_rank']} for h in result['hypotheses']]}))


if __name__=='__main__': main()
