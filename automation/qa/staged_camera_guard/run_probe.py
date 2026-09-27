"""Exercise the unapplied camera patch in one isolated Python process."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import types

from reconstruction import refine_photos
from qa.staged_camera_guard import camera_pose_selection


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refinement',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--views',nargs='+',default=['front','back'])
    args=parser.parse_args();here=Path(__file__).resolve().parent
    before=refine_photos.implementation_manifest()
    staged=here/'refine_photos.py.staged'
    if hashlib.sha256(Path(refine_photos.__file__).read_bytes()).hexdigest()!=json.loads((here/'patch-manifest.json').read_bytes())['source_refine_sha256']:
        raise ValueError('Production source differs from staged patch base')
    sys.modules['reconstruction.camera_pose_selection']=camera_pose_selection
    module=types.ModuleType('reconstruction._staged_refine_photos');module.__file__=str(staged);module.__package__='reconstruction'
    sys.modules[module.__name__]=module
    exec(compile(staged.read_text(encoding='utf-8'),str(staged),'exec'),module.__dict__)
    def manifest():
        result=refine_photos.implementation_manifest()
        result['staged_source_overrides']={name:{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            for name,path in [('refine_photos.py',staged),('camera_pose_selection.py',here/'camera_pose_selection.py')]}
        return result
    module.implementation_manifest=manifest
    cached=json.loads(args.refinement.read_bytes())
    photos=[module.PhotoInput(v['view_id'],Path(v['source']),v.get('view_label',v['view_id']))
            for v in cached['views'] if v['view_id'] in args.views]
    if len(photos)!=len(args.views):raise ValueError('Missing requested view')
    report=module.run(Path(cached['source_model']),photos,args.output,
        resolution=cached['settings']['resolution'],camera_evaluations=cached['settings']['camera_evaluations'])
    if before!=refine_photos.implementation_manifest():raise ValueError('Frozen production source changed')
    summary={'status':report['status'],'production_source_stable':True,'staged_patch':str(here/'camera-support-guard.patch'),
        'views':[{k:v.get(k) for k in ('view_id','camera_hypothesis','camera_fit','articulation_evidence')}
                 for v in report['views']],'export_rejection':(report.get('shared_shape') or {}).get('export_rejection')}
    (args.output/'staged-probe-summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'views':[{k:v.get(k) for k in ('view_id','camera_hypothesis')}
                     for v in report['views']],'production_source_stable':True}),flush=True)


if __name__=='__main__':main()
