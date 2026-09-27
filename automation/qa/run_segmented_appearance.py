"""Authorized bounded appearance experiment; never prints or stores credentials."""
import argparse
import json
import os
from pathlib import Path

from dotenv import dotenv_values

from reconstruction.photo_apertures import OfflineLensApertureEngine
from reconstruction.segmented_appearance import run_segmented_appearance
from reconstruction.semantic_transport import GeminiSemanticClient


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--product',choices=['oakley','miu'],required=True)
    parser.add_argument('--preparation',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--sampling-report',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    product=json.loads((root/'data/provider-comparison-v1/inputs.json').read_bytes())['products'][args.product]
    photos=[{'id':view,'view':view,'path':record['path'],'sha256':record['sha256']} for view,record in product['photos'].items()]
    preparation=args.preparation or root/f'data/part-probes-v1/optics/{args.product}-lod-compact-neutral/optics/report.json'
    output=args.output or root/f'data/segmented-appearance-v1/{args.product}'
    configuration=dotenv_values(root/'.env')
    credential=configuration.get('GEMINI_API_KEY') or os.environ.get('GEMINI_API_KEY')
    if not credential:raise ValueError('Configured Gemini credential is unavailable; no call sent')
    client=GeminiSemanticClient(api_key=credential,model='gemini-3.8-flash',
        cache_dir=root/f'data/segmented-appearance-v1/api-cache/{args.product}',maximum_calls=2,
        maximum_output_tokens=8192,timeout_seconds=120)
    detector=OfflineLensApertureEngine(root/'data/models/glasses-detector-v1')
    semantic=root/f'data/semantic-repair/{args.product}-grounded-evidence/semantics.json'
    report=run_segmented_appearance(preparation,photos,output,semantic_report=semantic,
        client=client,maximum_candidates=6,aperture_engine=detector,sampling_report=args.sampling_report)
    print(json.dumps({'status':report['status'],'output':str(output),'candidates':[
        {k:c[k] for k in ('candidate_id','origin','family','path','bytes')} for c in report['candidates']]}))


if __name__=='__main__':main()
