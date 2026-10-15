"""$0 known-opening and real-photo evidence for M5-fix-5 B6.

The accepted export and six beige photos are copied and hash checked. No model,
material, photo or job is edited. Historical WP6 openings are an independent
reference from a different source geometry, not truth for this authored export.
Run after tests/test_hinge_fit.py; this proof consumes only the six owner-labelled
beige photos and never imports the black colourway records as fit inputs.
"""
from __future__ import annotations

import os
if __name__ == '__main__':
    os.environ.update({k:'1' for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS')})

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np

from PIL import Image

from glasses3d.agent import procs
from glasses3d.g3d_host import compare as C, fit_views, fitmesh, pose, matte
from glasses3d.g3d_host.camera import Camera

ROOT = Path(__file__).resolve().parents[3]
G3D = ROOT/'glasses3d'
ACCEPTED = G3D/'data/wp6v2/lab/package_008/work/exports/head-681a8190c2cd3fd0.glb'
JOB = G3D/'data/jobs/prada-beige-86248315'
OLD = G3D/'data/wp6v2/basis/cameras.json'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--workers',type=int,default=6)
    ap.add_argument('--model',type=Path,default=ACCEPTED)
    ap.add_argument('--only',nargs='+',help='Explicit subset of photo filenames for a separately labelled proof')
    ap.add_argument('--reuse',type=Path,help='Prior proof directory; reuse only identical model/photo/matte successful records')
    ap.add_argument('--reuse-records',type=Path,action='append',default=[],
                    help='Additional saved camera records under the same byte/mode/status reuse rule')
    args = ap.parse_args()
    start = time.perf_counter()
    args.out.mkdir(parents=True,exist_ok=False)
    model_hash, job_hash, old_hash = sha(args.model),sha(JOB/'job.json'),sha(OLD)
    job = json.loads((JOB/'job.json').read_bytes())
    original = json.loads(OLD.read_bytes())
    assert len(job['images']) == 6 and all('black' not in row['file'].lower() for row in job['images'])
    if args.only and set(args.only)-{Path(row['file']).name for row in job['images']}:
        raise ValueError('Unknown proof photo names')
    model = args.out/'source.glb'
    shutil.copyfile(args.model,model)
    (args.out/'images').mkdir()
    images, views, candidates, source_photos = {}, {}, {}, {}
    for row in job['images']:
        src = JOB/row['file']
        if args.only and src.name not in args.only:
            continue
        assert sha(src) == row['sha256']
        target = args.out/'images'/src.name
        shutil.copyfile(src,target)
        images[src.name],views[src.name],candidates[src.name] = target,row['view_kind'],tuple(row['fit_kinds'])
        mode = 'glossy_floor' if src.stem in ('reference-01','reference-03','reference-05','reference-06') else 'default'
        source_photos[src.name] = {'source':str(src),'sha256':sha(src),'view_kind':row['view_kind'],
                                  'side':row['side'],'matte_mode':mode}
    controls = {'started_at':datetime.now(timezone.utc).isoformat(),'paid_calls':0,
        'model':{'source':str(args.model),'sha256':model_hash},'photos':source_photos,
        'explicit_photo_subset':args.only,
        'historical_reference':{'path':str(OLD),'sha256':old_hash,'source_model_sha256':original['model']['sha256'],
             'meaning':'Independent historical calibration on a different authored model; angle differences are not errors.'},
        'quality_policy':'Existing view-sign and camera-usability checks unchanged; real opening differences and uncertainty are reports.',
        'synthetic_predeclared':'19 tests: 16 occlusion controls; 3 joint cases IoU>=.98, opening error<=.8deg, yaw/pitch<=1deg.',
        'matte_declaration_source':'Independent eye check before fit results: visible reflective floors 01/03/05/06; plain white 02/04.',
        'source_code_sha256':{name:sha(G3D/'g3d_host'/name) for name in ('hinge_fit.py','hinge_axes.py','fit_views.py','compare.py')},
        'workers':args.workers}
    C.write_json(args.out/'inputs.json',controls)
    basis_dir = args.out/'cameras'/'basis-01'
    self_qos = procs.opt_out_self()
    modes = {name:row['matte_mode'] for name,row in source_photos.items()}
    with procs.DescendantsOptOut() as qos:
        if args.reuse is None:
            result = fit_views.fit_views(model,images,views,basis_dir,workers=args.workers,candidates=candidates,
                                          matte_modes=modes,log=lambda line:print(line,flush=True))
        else:
            # Declarations were fixed by eye before reading optimizer scores.
            # Reuse is based only on bytes/mode/status, never on an angle or IoU.
            prior_path = args.reuse/'cameras/basis-01/cameras.json'
            prior = json.loads(prior_path.read_bytes())
            assert prior['model']['sha256'] == model_hash
            context = fit_views._fit_context(model,(),None)
            basis_dir.mkdir(parents=True)
            records, lineage = {}, {}
            for input_path in [prior_path,*args.reuse_records]:
                source_doc = json.loads(input_path.read_bytes())
                source_report_path = input_path.parents[2]/'report.json'
                source_report = (json.loads(source_report_path.read_bytes()) if source_report_path.is_file() else {})
                assert source_doc['model']['sha256'] == model_hash
                assert all(source_doc['model'].get(key)==context['model'].get(key) for key in
                           ('skip','front_parts','frame','front_pivot_mm'))
                for name,row in source_doc['images'].items():
                    if (name in images and row['image_sha256']==sha(images[name]) and row['matte_mode']==modes[name]
                            and row['status']=='ok' and row.get('candidates')==list(candidates[name])):
                        records[name] = row
                        lineage[name] = {'source_cameras':str(input_path),'source_sha256':sha(input_path),
                                         'reason':'identical model/photo bytes, view candidates and declared matte; successful original record',
                                         'source_code_sha256':source_doc.get('code_sha256',source_report.get('source_code_sha256')),
                                         'prior_lineage':source_doc.get('reuse_lineage',source_report.get('reuse_lineage',{})).get(name),
                                         'legacy_search_visibility':'original stored values retain their original search-grid stride; current native final visibility is checked separately'}
                        if row.get('overlay'):
                            target = basis_dir/f'overlay_{Path(name).stem}.png'
                            shutil.copyfile(row['overlay'],target)
                            records[name] = {**row,'overlay':str(target)}
            # These are reused results, not a claim that historical workers ran
            # the final source. Independently reevaluate their actual final pose
            # at native pixels under the final visibility implementation.
            authored = fitmesh.load_fit_mesh(model)
            roles = pose.part_roles(model)
            face_sides = pose.side_of_faces(authored,roles)
            for name,row in list(records.items()):
                angles, axes = row['theta_deg'],row['hinge_axis_xz_mm']
                posed = pose.posed(authored,{k:{'theta_deg':v,'axis_xz_mm':axes[k]} for k,v in angles.items()},roles)
                fg = matte.matte(matte.load_photo(images[name]),mode=modes[name])
                drawing = posed.scene().render(Camera.from_dict(row['camera']),fg.shape)
                fid = drawing['face_id']; hit = fid>=0
                counts = {k:int(np.count_nonzero(hit & (face_sides[np.maximum(fid,0)]==int(k)))) for k in angles}
                native = {'pixel_stride':1,'visibility_domain':'full_photo','visible_pixels':counts,'camera':row['camera'],
                          'theta_deg':angles,'model_sha256':model_hash,
                          'checked_with_source_sha256':controls['source_code_sha256']['hinge_fit.py']}
                if any(row['theta_observed'][k] and counts[k]==0 for k in angles):
                    del records[name]
                    del lineage[name]
                    continue  # Refit under the final hidden-arm rule; never patch an angle by hand.
                row['opening_fit'] = {**row['opening_fit'],'native_visibility_recheck':native}
                lineage[name]['native_visibility_recheck'] = native
                if row['opening_fit'].get('selected') == 'input_pose':
                    # Preserve the old source JSON. Repair only copied evidence
                    # that described a rejected proposal as the selected pose.
                    old_sides = row['opening_fit']['per_side']
                    corrected = {}
                    for key, old_side in old_sides.items():
                        side = dict(old_side)
                        proposal_sensitivity = {field:side.pop(field) for field in
                            ('camera_coupling','independent_px_per_degree','one_pixel_sensitivity_deg','sensitivity_note')
                            if field in side}
                        side.update(visible_pixels_final=counts[key], observed=False,
                                    identifiability='unobserved',theta_deg=angles[key])
                        side.setdefault('proposed_visible_pixels_final',old_side.get('visible_pixels_final'))
                        side.setdefault('proposal_sensitivity',proposal_sensitivity)
                        corrected[key] = side
                    if old_sides != corrected:
                        row['opening_fit']['per_side'] = corrected
                        lineage[name]['selected_pose_report_correction'] = {
                            'original_per_side':old_sides,'corrected_per_side':corrected,
                            'reason':'Selected input pose is unobserved; rejected proposal sensitivity is separate. Final visible counts were independently rendered from the unchanged selected camera and theta.',
                            'source_cameras_sha256':lineage[name]['source_sha256'],
                            'checked_with_source_sha256':controls['source_code_sha256']['fit_views.py'],
                            'numerical_fit_unchanged':True}
            fresh = [name for name in images if name not in records]
            first = [name for name in fresh if not any(v in fit_views.CROSS_RECEIVERS for v in candidates[name])]
            second = [name for name in fresh if name not in first]
            def task(name,winners):
                return ((model,images[name],views[name]),{'candidates':candidates[name],
                    'matte_mode':modes[name],'_model_context':context,'cross_winners':winners,
                    'overlay_path':basis_dir/f'overlay_{Path(name).stem}.png'})
            records.update(zip(first,fit_views._map([task(name,None) for name in first],args.workers)))
            winners = {}
            for name in images:
                row = records.get(name,{})
                if row.get('status')=='ok' and row['view'] not in fit_views.cameras.NOT_CROSS_SOURCES:
                    winners.setdefault(row['view'],row['camera'])
            records.update(zip(second,fit_views._map([task(name,winners) for name in second],args.workers)))
            for name,row in records.items():
                row.pop('model',None)
                print(fit_views._row(name,row),flush=True)
            result = {'model':context['model'],'images':{name:records[name] for name in images},
                      'reuse_lineage':lineage,'newly_fitted':fresh,'cross_winners':winners,
                      'code_sha256':controls['source_code_sha256']}
            C.write_json(basis_dir/'cameras.json',result)
            controls['reuse_lineage'] = lineage
            controls['newly_fitted'] = fresh
    basis = C.freeze_basis(args.out,basis_dir,images,reason='M5-fix-5 independent accepted-Prada opening proof',
                           call_id='m5-hinge-proof',source='fit_cameras')
    rows, tiles = {}, []
    # Front photo first, then all remaining owner-labelled photos in stable order.
    order = sorted(images,key=lambda name:(views[name]!='front',name))
    for name in order:
        row, prior = result['images'][name],original['images'][name]
        record = {'status':row['status'],'camera':row['camera'],'theta_deg':row.get('theta_deg'),
                  'theta_observed':row.get('theta_observed'),'opening_fit':row.get('opening_fit'),
                  'iou':row.get('iou'),'contour_mean_px':row.get('contour_mean_px'),
                  'reference_theta_deg':prior.get('theta_deg'),'reference_theta_observed':prior.get('theta_observed'),
                  'reference_camera':prior.get('camera'),'overlay':row.get('overlay')}
        if row.get('theta_deg'):
            record['delta_from_historical_reference_deg'] = {
                k:row['theta_deg'][k]-prior['theta_deg'][k] for k in row['theta_deg']}
        rows[name] = record
        image_path = row.get('overlay') or images[name]
        label = f"{name}: {views[name]}, {source_photos[name]['side']}; {row['status']}"
        if row.get('iou') is not None:
            label += f"; IoU {row['iou']:.3f}; openings {row.get('theta_deg')}"
        tiles.append(C.labelled(C.fit_into(Image.open(image_path).convert('RGBA'),1000,750),label))
    sheet = args.out/'accepted-prada-opening-overlays.png'
    C.grid(tiles,2).convert('RGB').save(sheet)
    assert sha(args.model) == model_hash == sha(model)
    assert sha(JOB/'job.json') == job_hash and sha(OLD) == old_hash
    for name,row in source_photos.items():
        assert sha(row['source']) == row['sha256'] == sha(images[name])
    report = {**controls,'status':'passed' if len(basis.usable_refs())==len(images) else 'failed',
        'ended_at':datetime.now(timezone.utc).isoformat(),'seconds':time.perf_counter()-start,
        'source_bytes_unchanged':True,'basis':str(basis_dir),'sheet':str(sheet),'images':rows,
        'ecoqos':{'host':self_qos,'workers':qos.opted_out}}
    C.write_json(args.out/'report.json',report)
    print(json.dumps({'status':report['status'],'report':str(args.out/'report.json'),'seconds':report['seconds']}),flush=True)
    return 0 if report['status']=='passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
