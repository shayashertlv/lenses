"""Exercise repaired membership, normals, grounded optics and AR on a cached job.

No geometry provider is called. The optional final blind comparison consumes at
most two explicitly authorized semantic calls from a durable cache.
"""
import argparse
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path


def read(path):return json.loads(Path(path).read_bytes())
def write(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def run(args):
    from reconstruction.candidate_cameras import transfer_cameras_to_partition
    from reconstruction.region_engine import OfflineSAM2RegionEngine
    from reconstruction.region_proposals import run_region_stage
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    physical=args.physical.resolve();report=read(physical/'report.json')
    chosen=min(report['hypotheses'],key=lambda h:h['composition_rank'])
    bridge=physical/chosen['folder']/'bridge'
    partition=bridge/'partition/partitioned.glb'
    original=read(args.job/'stages/refinement/attempt_1/report.json')
    inventory=read(physical/'inventory/report.json')
    raw=(physical/'inventory'/inventory['source_snapshot']['path']).read_bytes()
    cameras=transfer_cameras_to_partition(original,raw,partition.read_bytes(),read(bridge/'partition/receipt.json'),partitioned_model_path=str(partition))
    write(output/'cameras.json',cameras)
    photos=[{'id':v['view_id'],'view':v['view_label'],'path':v['source'],'sha256':v['source_sha256']} for v in original['views'] if 'camera_fit' in v]
    region_report=output/'regions/report.json'
    if not region_report.exists():
        prior=read(args.job/'stages/hypothesis_regions/attempt_1/report.json')
        engine=OfflineSAM2RegionEngine(Path(prior['engine']['weights']['path']))
        run_region_stage(photos,region_report.parent,engine=engine,model=partition,refinement_report=cameras)
    if args.phase=='regions':return {'regions':str(region_report)}
    from reconstruction.prepare_optical_groups import run_optical_group_preparation
    from reconstruction.smooth_optical_geometry import run_smooth_optical_preparation
    from reconstruction.photo_apertures import OfflineLensApertureEngine
    from reconstruction.semantic_appearance_stage import prepare_semantic_appearance_inputs,run_semantic_appearance_stage
    from reconstruction.photo_lens_fit import PhotoLensFitPolicy
    from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy
    from reconstruction.joint_photo_lens_stage import run_joint_photo_lens_stage
    preparation=output/'membership-preparation/report.json'
    if not preparation.exists():
        run_optical_group_preparation(partition,preparation.parent,grouping_mode='explicit_declarations',declarations=bridge/'group-declarations.json')
    smooth=output/'smooth-preparation/report.json'
    if not smooth.exists():run_smooth_optical_preparation(preparation,smooth.parent)
    detector=OfflineLensApertureEngine(args.weights)
    request=read(args.source_request)
    semantic_photos=[*request['photos'],*request.get('initializer',{}).get('provider_views',[])]
    evidence=prepare_semantic_appearance_inputs(smooth,region_report,output/'evidence',semantic_report=args.semantic_report,
        photos=semantic_photos,aperture_engine=detector,maximum_samples=256,declared_facts=request.get('lens_facts'))
    joint=output/args.fit_directory/'report.json'
    if not joint.exists():
        photo=PhotoLensFitPolicy(lighting_families=('constant','semantic_softbox'),max_nfev=60,roughness_values=(.15,))
        policy=JointPhotoLensFitPolicy(photo_policy=photo,mask_search_mode='conditional_seed_beam',conditional_beam_width=4,
            maximum_joint_mask_branches=4,maximum_optimization_runs=240)
        run_joint_photo_lens_stage(smooth,region_report,joint.parent,policy=policy,maximum_samples_per_hypothesis=256,
                                  appearance_prior_report=evidence['priors_path'])
    if args.phase=='fit':return {'fit':str(joint),'preparation':str(smooth)}
    from reconstruction.semantic_transport import GeminiSemanticClient
    client=GeminiSemanticClient(api_key=os.environ[args.api_key_env],model='gemini-3.8-flash',
        cache_dir=output/'comparison-cache',maximum_calls=2)
    result=run_semantic_appearance_stage(smooth,region_report,joint,output/'appearance',client=client,
        prepared_evidence=evidence,maximum_samples=256,declared_facts=request.get('lens_facts'),
        width_mm=request.get('dimensions_mm',{}).get('frame_width',145.))
    write(output/'report.json',{'method':'cached_source_integrated_repair_v1','source_job':str(args.job.resolve()),
        'membership_hypothesis':chosen['index'],'membership_audit':chosen['membership_repair'],
        'source_positions_preserved':True,'material_scope':'bounded branch search, 60 evaluations/start; not exhaustive',
        'appearance':result,'accepted':False})
    return {'status':result['status'],'candidate':str(output/'appearance/candidate-appearance.glb')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('job','physical','output','weights','semantic-report','source-request'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--phase',choices=('regions','fit','all'),default='all')
    parser.add_argument('--api-key-env',default='GEMINI_API_KEY')
    parser.add_argument('--fit-directory',default='fit',choices=('fit','fit-final'),help='Preserve an interrupted old-code diagnostic fit')
    print(json.dumps(run(parser.parse_args())))


if __name__=='__main__':main()
