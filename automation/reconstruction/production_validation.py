"""Measured delivery gates, independent of AI preference and export success.

Thresholds are versioned engineering targets, not a claimed calibrated product
success probability. Missing evidence blocks automatic delivery. A caller can
provide held-out photographs only with their reconstruction-use hash ledger.
"""
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .camera import Camera, fit_camera
from .deform_glb import _read_bytes
from .mesh import load_glb_bytes
from .metrics import score_masks
from .observations import observe_image
from .raster import rasterize


@dataclass(frozen=True)
class AcceptancePolicy:
    minimum_heldout_views: int = 1
    minimum_heldout_iou: float = .90
    maximum_contour_p95_width_fraction: float = .015
    maximum_opaque_lens_core_fraction: float = .02
    maximum_optical_contamination_fraction: float = .025
    maximum_material_error_codes: float = 10.
    maximum_color_error_codes: float = 12.
    maximum_transmission_error: float = .12
    minimum_frame_observation_coverage: float = .60
    maximum_asset_bytes: int = 15_000_000
    maximum_triangles: int = 150_000
    maximum_draw_calls: int = 48


def _number(value):
    return isinstance(value,(float,int,np.floating,np.integer)) and not isinstance(value,bool) and np.isfinite(value)


def evaluate_acceptance(evidence, *, policy=AcceptancePolicy()):
    """All gates must pass. Missing/ambiguous data is distinct from a failure."""
    gates=[]
    def boolean(name,value,reason):
        gates.append({'id':name,'status':'unmeasured' if value is None else 'pass' if value is True else 'fail','reason':reason})
    def bound(name,value,limit,*,minimum=False):
        gates.append({'id':name,'status':'unmeasured' if not _number(value) else
                      'pass' if (value>=limit if minimum else value<=limit) else 'fail',
                      'value':float(value) if _number(value) else None,'limit':limit,'comparison':'>=' if minimum else '<='})
    boolean('source_and_artifact_integrity',evidence.get('integrity_verified'),'All measurements must identify this candidate and pinned photo bytes.')
    boolean('actual_ar_runtime',evidence.get('runtime_passed'),'Actual runtime loading/rendering, with stable renderer source snapshot.')
    boolean('required_parts_present',evidence.get('required_parts_present'),'Visible front and both temples have independent photographic support.')
    heldout=evidence.get('heldout',{})
    bound('heldout_photo_count',heldout.get('views'),policy.minimum_heldout_views,minimum=True)
    boolean('heldout_not_used_for_reconstruction',heldout.get('independent'),'Photo hashes must be absent from geometry/material/provider input ledgers.')
    bound('heldout_silhouette_iou',heldout.get('minimum_iou'),policy.minimum_heldout_iou,minimum=True)
    bound('heldout_contour_p95',heldout.get('maximum_contour_p95'),policy.maximum_contour_p95_width_fraction)
    optical=evidence.get('optical',{})
    bound('opaque_lens_core',optical.get('opaque_core_fraction'),policy.maximum_opaque_lens_core_fraction)
    bound('optical_frame_contamination',optical.get('contamination_fraction'),policy.maximum_optical_contamination_fraction)
    bound('photo_material_residual',optical.get('maximum_validation_error_codes'),policy.maximum_material_error_codes)
    appearance=evidence.get('appearance',{})
    bound('heldout_color',appearance.get('color_error_codes'),policy.maximum_color_error_codes)
    bound('heldout_transmission',appearance.get('transmission_error'),policy.maximum_transmission_error)
    boolean('gradient_direction',appearance.get('gradient_direction_matches'),'Reversed gradients fail even when mean color matches.')
    bound('frame_photo_coverage',evidence.get('frame',{}).get('observed_fraction'),policy.minimum_frame_observation_coverage,minimum=True)
    asset=evidence.get('asset',{})
    for name,key,limit in [('asset_bytes','bytes',policy.maximum_asset_bytes),('triangle_budget','triangles',policy.maximum_triangles),('draw_call_budget','draw_calls',policy.maximum_draw_calls)]:
        bound(name,asset.get(key),limit)
    accepted=all(g['status']=='pass' for g in gates)
    failed=[g['id'] for g in gates if g['status']=='fail'];missing=[g['id'] for g in gates if g['status']=='unmeasured']
    return {'schema_version':1,'method':'measured_ar_delivery_gate_v1','policy':asdict(policy),
            'candidate_sha256':evidence.get('candidate_sha256'),'accepted':accepted,
            'verdict':'ready_for_ar' if accepted else 'needs_repair' if failed else 'needs_review',
            'gates':gates,'failed_gates':failed,'unmeasured_gates':missing,
            'scope':'Appearance and artifact delivery; physical wearer dimensions are not a quality gate.',
            'calibration':'Versioned engineering targets; not yet a statistically calibrated success probability.'}


def artifact_metrics(raw):
    _,doc,_=_read_bytes(raw);mesh=load_glb_bytes(raw)
    return {'bytes':len(raw),'triangles':len(mesh.faces),'draw_calls':len(mesh.parts),
            'vertices':len(mesh.vertices),'materials':len(doc.get('materials',[]))}


def _pinned_json(reference):
    raw=Path(reference['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=reference['sha256']:
        raise ValueError('Pinned validation measurement report changed')
    return json.loads(raw)


def _photo_history(receipts):
    """Verify the current job's complete intake, including decoded duplicates."""
    from .input_bundle import _normalize
    hashes,pixels=set(),set()
    complete=False
    for reference in receipts:
        report=_pinned_json(reference)
        if report.get('method')!='all_owned_views_reconstruction_intake_v1' or not report.get('photos'):
            raise ValueError('Reconstruction history requires the complete owned-view intake receipt')
        # Intake describes this run's owned images, not the unknown history of
        # an imported model or a reused semantic interpretation.
        for photo in report['photos']:
            path=Path(photo['path']);raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
            if sha!=photo['sha256']:raise ValueError('Reconstruction history photo changed')
            normalized,_=_normalize(raw,photo['id'],photo.get('view','unknown'),path)
            pixels.add(normalized['normalized']['pixel_sha256']);hashes.add(sha)
            original=photo.get('normalization_provenance',{}).get('original',{})
            if original.get('sha256'):hashes.add(original['sha256'])
        hashes.update(row['source_sha256'] for row in report.get('omitted',[]) if row.get('source_sha256'))
    return hashes,pixels,complete


def measure_heldout_views(model, photos, *, used_photo_sha256=(), resolution=256,
                          source_history_complete=False, source_history_receipts=(), evaluation_context=None):
    """Fit nuisance cameras on frozen geometry; report independent-view geometry.

    Camera estimation still uses each held-out silhouette. The geometry and
    materials are never fitted to these photos. This is explicit in the receipt.
    The legacy source_history_complete boolean cannot establish independence;
    intake receipts establish the local reconstruction-use set, while a reserved
    evaluation ledger and verified initializer provenance establish independence.
    """
    from .input_bundle import _normalize
    if type(resolution) is not int or not 128<=resolution<=768:
        raise ValueError('Held-out resolution must be 128 through 768')
    raw=Path(model).read_bytes();mesh=load_glb_bytes(raw).normalized();rows=[];inputs=[];seen=set()
    history_hashes,history_pixels,complete=_photo_history(source_history_receipts)
    evaluation_usage = None
    reserved_pixels = set()
    if evaluation_context is not None:
        from .evaluation_stage import replay_evaluation_context
        reservation, evaluation_usage = replay_evaluation_context(evaluation_context, model)
        complete = evaluation_usage['independent_evaluation_eligible']
        reserved_pixels = {r['normalization']['normalized']['pixel_sha256'] for r in reservation['photos']
                           if r['purpose'] == 'evaluation'}
    used=set(used_photo_sha256)|history_hashes
    for photo in photos:
        path=Path(photo['path']).resolve();data=path.read_bytes();sha=hashlib.sha256(data).hexdigest()
        if photo.get('sha256',sha)!=sha:raise ValueError('Held-out photo hash changed')
        normalized,_=_normalize(data,photo['id'],photo.get('view','unknown'),path)
        pixels=normalized['normalized']['pixel_sha256']
        if evaluation_context is not None and pixels not in reserved_pixels:
            raise ValueError('Evaluation photo was not reserved before reconstruction')
        if sha in used or pixels in history_pixels:
            raise ValueError('Held-out photograph or its normalized pixels were used for reconstruction')
        if pixels in seen:raise ValueError('Held-out photographs must have distinct normalized pixels')
        seen.add(pixels)
        inputs.append({'id':photo['id'],'path':str(path),'sha256':sha,'view':photo.get('view','unknown')})
        image=Image.open(io.BytesIO(data));image.thumbnail((resolution,resolution),Image.Resampling.LANCZOS)
        observation=observe_image(image)
        if observation.status!='measured':
            rows.append({'photo_id':photo['id'],'source_sha256':sha,'status':'unmeasured','reasons':list(observation.reasons)});continue
        fitted=fit_camera(mesh,observation.mask,view=photo.get('view','unknown'),max_evaluations=100)
        raster=rasterize(mesh,Camera(**fitted['camera']),observation.mask.shape)
        score=score_masks(observation.mask,raster.mask)
        rows.append({'photo_id':photo['id'],'source_sha256':sha,'status':'measured','camera':fitted,'score':score})
    measured=[r for r in rows if r['status']=='measured']
    if Path(model).read_bytes()!=raw:raise ValueError('Held-out candidate changed during measurement')
    if any(hashlib.sha256(Path(p['path']).read_bytes()).hexdigest()!=p['sha256'] for p in inputs):
        raise ValueError('Held-out photo changed during measurement')
    for reference in source_history_receipts:_pinned_json(reference)
    return {'schema_version':1,'method':'source_bound_heldout_geometry_v2' if evaluation_context is not None else 'source_bound_heldout_geometry_v1',
            'candidate_sha256':hashlib.sha256(raw).hexdigest(),'views':len(measured),
            'independent':bool(measured) and complete,'reconstruction_used_photo_sha256':sorted(used),
            'source_history_receipts':list(source_history_receipts),'input_photos':inputs,'resolution':resolution,
            'evaluation_context':evaluation_context, 'evaluation_usage':evaluation_usage,
            'minimum_iou':min([r['score']['foreground_iou'] for r in measured],default=None),
            'maximum_contour_p95':max([r['score']['boundary']['symmetric']['p95'] for r in measured if r['score']['boundary'].get('symmetric')],default=None),
            'records':rows,'camera_scope':'Nuisance camera fitted to held-out silhouette; geometry/material unchanged.',
            'mask_scope':'Foreground contrast/alpha evidence; transparent regions are not complete object masks.'}


def bind_independent_measurements(model, evidence):
    """Recompute supported measurements; scalar wrappers never grant acceptance."""
    captured=dict(evidence)
    captured.update(heldout={},appearance={},required_parts_present=None)
    receipt_refs=evidence.get('measurement_receipts',{})
    trusted=evidence.get('reconstruction_history_receipts',[])
    sha=hashlib.sha256(Path(model).read_bytes()).hexdigest()
    audit=[]
    for kind,reference in receipt_refs.items():
        receipt=_pinned_json(reference)
        if receipt.get('candidate_sha256')!=sha:
            raise ValueError('Independent measurement receipt refers to another candidate')
        if kind == 'parts':
            from .required_parts import replay_required_parts
            trusted_refinement = evidence.get('trusted_refinement_reference')
            if not trusted_refinement:
                audit.append({'kind':kind,'status':'unmeasured','reason':'No matching current-job role and camera history'})
                continue
            parts = replay_required_parts(model, reference, trusted_refinement,
                trusted_region_references=evidence.get('trusted_region_references',[]))
            captured['required_parts_present'] = parts['required_parts_present']
            captured['required_parts_measurement'] = parts
            audit.append({'kind':kind,'status':'recomputed_from_pinned_inputs','receipt':reference})
            continue
        if kind!='heldout':
            audit.append({'kind':kind,'status':'unmeasured','reason':'No supported independent measurement receipt producer'})
            continue
        if receipt.get('method') not in ('source_bound_heldout_geometry_v1', 'source_bound_heldout_geometry_v2'):
            raise ValueError('Unsupported held-out measurement receipt')
        key=lambda rows:sorted((str(Path(r['path']).resolve()),r['sha256']) for r in rows)
        if not trusted or key(receipt.get('source_history_receipts',[]))!=key(trusted):
            audit.append({'kind':kind,'status':'unmeasured','reason':'Measurement has no matching current-job reconstruction history'})
            continue
        context = None
        if receipt['method'] == 'source_bound_heldout_geometry_v2':
            context = evidence.get('evaluation_context')
            if not context or receipt.get('evaluation_context') != context:
                audit.append({'kind':kind,'status':'unmeasured','reason':'No matching current-job evaluation reservation/usage context'})
                continue
        recomputed=measure_heldout_views(model,receipt['input_photos'],resolution=receipt['resolution'],
            source_history_receipts=trusted, evaluation_context=context)
        captured['heldout']=recomputed
        audit.append({'kind':kind,'status':'recomputed_from_pinned_inputs','receipt':reference})
    if any(key in evidence for key in ('heldout','appearance','required_parts_present')):
        audit.append({'status':'scalar_claims_not_accepted','reason':'Only supported source-bound measurement receipts contribute independent gates'})
    captured['independent_measurement_audit']=audit
    return captured


def measure_appearance_controls(reference_rgb, candidate_rgb, *, mask, reference_transmission=None,
                                candidate_transmission=None, gradient_axis=0):
    reference=np.asarray(reference_rgb,float);candidate=np.asarray(candidate_rgb,float);mask=np.asarray(mask,bool)
    if reference.shape!=candidate.shape or reference.shape[:2]!=mask.shape or reference.shape[-1]!=3 or not mask.any():
        raise ValueError('Appearance controls need matching RGB arrays and measured support')
    error=float(np.sqrt(np.mean((reference[mask]-candidate[mask])**2)))
    coordinates=np.indices(mask.shape)[gradient_axis];q=np.quantile(coordinates[mask],[.25,.75])
    low=mask&(coordinates<=q[0]);high=mask&(coordinates>=q[1])
    reference_delta=reference[high].mean(axis=0)-reference[low].mean(axis=0)
    candidate_delta=candidate[high].mean(axis=0)-candidate[low].mean(axis=0)
    gradient=bool(np.linalg.norm(reference_delta)<4 or reference_delta @ candidate_delta>0)
    transmission=None
    if reference_transmission is not None and candidate_transmission is not None:
        a,b=np.asarray(reference_transmission,float),np.asarray(candidate_transmission,float)
        if a.shape!=b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():raise ValueError('Invalid transmission control')
        transmission=float(np.max(np.abs(a-b)))
    return {'color_error_codes':error,'transmission_error':transmission,'gradient_direction_matches':gradient}


def run_delivery_validation(model, output, *, evidence, policy=AcceptancePolicy()):
    path=Path(model);raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest();output=Path(output)
    if output.exists() and any(output.iterdir()):raise ValueError('Delivery validation output must be fresh')
    if evidence.get('candidate_sha256')!=sha:raise ValueError('Validation measurements refer to another model')
    # Pin every external report that contributes to the gate, not just a boolean.
    refs=evidence.get('source_reports',[])
    for reference in refs:
        if hashlib.sha256(Path(reference['path']).read_bytes()).hexdigest()!=reference['sha256']:
            raise ValueError('Delivery evidence report changed')
    captured={**bind_independent_measurements(path,evidence),'asset':artifact_metrics(raw)}
    report=evaluate_acceptance(captured,policy=policy)
    output.mkdir(parents=True,exist_ok=True)
    (output/'evidence.json').write_text(json.dumps(captured,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    report['evidence_sha256']=hashlib.sha256((output/'evidence.json').read_bytes()).hexdigest()
    (output/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return report
