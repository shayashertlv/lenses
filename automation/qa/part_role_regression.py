"""Offline regression adapter for four saved segmentation outputs, no API calls."""
from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path

import numpy as np
from PIL import Image

from reconstruction.part_role_inference import infer_part_roles

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text())


def pin(path):
    return dict(path=str(Path(path).resolve()), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())


def run(output, policy=None):
    output.mkdir(parents=True, exist_ok=True)
    renders = read(ROOT/'data/provider-comparison-v1/render-final/report.json')
    results = []
    for product in ('oakley', 'miu'):
        source = next(c for c in renders['cases'] if c['id']==product+'-tripo')
        n = source['normalization']
        angle = np.radians(n['rotation_degrees'][1])
        matrix = np.eye(4)
        matrix[:3, :3] = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0],
                                  [-np.sin(angle), 0, np.cos(angle)]])*n['uniform_scale']
        matrix[:3, 3] = np.asarray(n['translation'])*n['uniform_scale']
        for operation in ('segment-auto', 'segment-guided'):
            case = product+'-'+operation
            audit = ROOT/'data/part-probes-v1/audits'/case
            views = []
            for view_name in ('front', 'angled'):
                mask_root = ROOT/'data/render-mask-probes-v1'/(product+'-'+view_name)
                request, masks = read(mask_root/'request.json'), read(mask_root/'artifacts.json')['masks']
                correspondence = read(ROOT/'data/part-mask-correspondence-v1'/(product+'-'+view_name)/'report.json')
                normal = next(r for r in source['renders'] if r['mode']=='normals' and r['background']=='light' and r['view']==view_name)
                normal_path = ROOT/'data/provider-comparison-v1/render-final'/normal['filename']
                assert pin(normal_path)['sha256'] == normal['sha256']
                pixels = np.asarray(Image.open(normal_path).convert('RGB')).astype(int)
                silhouette = (np.max(np.abs(pixels-pixels[0, 0]), axis=2)>3).astype(np.uint8)*255
                silhouette_path = output/'inputs'/f'{product}-{view_name}-gpu-silhouette.png'
                silhouette_path.parent.mkdir(exist_ok=True)
                if not silhouette_path.exists():
                    Image.fromarray(silhouette).save(silhouette_path)
                views.append(dict(id=view_name, model_sha256=source['model_sha256'], width=640, height=480,
                    camera=correspondence['camera'], world_to_render=matrix.tolist(), cull_mode='front',
                    image=request['image'], masks=[dict(id=f'mask-{m["index"]}', path=m['path'], sha256=m['sha256']) for m in masks],
                    mask_status='success' if masks else 'no_detection', silhouette=pin(silhouette_path),
                    geometry_correspondence=dict(source_model=pin(ROOT/'data/provider-comparison-v1/runs'/(product+'-tripo')/'artifacts/model.glb'),
                        candidate_to_source_faces=pin(audit/'candidate-to-original-bounded-face.npy'), max_relative_corner_error=1e-6)))
            report = infer_part_roles(ROOT/'data/part-probes-v1/runs'/case/'artifacts/model.glb', views, output/case, policy=policy)
            result = dict(case=case, selected=report['selected_part_indices'], groups=report['primary_groups'],
                fragment_candidates=[f for f in report['fragment_evidence'] if f['candidate']],
                hypotheses=report['hypotheses'], report=pin(output/case/'report.json'))
            results.append(result)
            print(json.dumps(result), flush=True)
            (output/'report.json').write_text(json.dumps(dict(cases=results), indent=2))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'data/part-role-inference-v1')
    parser.add_argument('--policy', type=Path)
    args = parser.parse_args()
    run(args.output, read(args.policy) if args.policy else None)
