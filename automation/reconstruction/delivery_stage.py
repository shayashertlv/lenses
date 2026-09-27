"""Frame reconstruction, lossless mobile packaging, AR parity and quality gates."""
import hashlib
import json
from pathlib import Path

from .intrinsic_frame_appearance import run_intrinsic_frame_stage
from .production_validation import run_delivery_validation


def _read(path):return json.loads(Path(path).read_bytes())
def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def _write(path,value):Path(path).write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def run_delivery_stage(model, export_receipt, region_report, output, *, renderer,
                       physical_report=None, selection=None, validation_evidence=None, width_mm=145.,
                       reconstruction_history_receipts=(), evaluation_context=None, refinement_reference=None):
    from .compact_glb import run_compact_asset
    output=Path(output).resolve()
    if output.exists() and any(output.iterdir()):raise ValueError('Delivery output must be fresh')
    output.mkdir(parents=True,exist_ok=True)
    from .evaluation_stage import capture_delivery_usage, measure_reserved_views
    evaluation_context = capture_delivery_usage(evaluation_context, region_report, output/'frame-photo-usage')
    frame=run_intrinsic_frame_stage(model,export_receipt,region_report,output/'frame')
    chosen=frame['selected']
    compact=run_compact_asset(chosen['path'],output/'compact',optical_receipt=chosen['export']['path'])
    selected_compact_folder='compact'
    lod=compact_lod=None
    if compact['triangles']>150_000:
        from .mobile_lod import run_mobile_lod
        # The geometric error bound can stop before the requested count. Leave
        # headroom; the measured asset budget remains the authoritative gate.
        lod=run_mobile_lod(chosen['path'],output/'lod',optical_receipt=chosen['export']['path'],target_triangles=135_000)
        compact_lod=run_compact_asset(lod['model']['path'],output/'compact-lod',optical_receipt=lod['export']['path'])
    cases=[]
    for candidate in frame['candidates']:
        receipt=_read(candidate['export']['path'])
        cases.append({'id':candidate['id'].replace('_','-'),'path':candidate['path'],'model_sha256':candidate['sha256'],
                      'groups':receipt['groups'],'width_mm':width_mm})
    compact_receipt=_read(compact['export']['path'])
    cases.append({'id':'compact-selected','path':compact['model']['path'],'model_sha256':compact['model']['sha256'],
                  'groups':compact_receipt['groups'],'width_mm':width_mm})
    if lod:
        for label,asset in [('lod-control',lod),('compact-lod',compact_lod)]:
            receipt=_read(asset['export']['path'])
            cases.append({'id':label,'path':asset['model']['path'],'model_sha256':asset['model']['sha256'],
                          'groups':receipt['groups'],'width_mm':width_mm})
    _write(output/'runtime-manifest.json',{'schema_version':1,'cases':cases})
    runtime=renderer(output/'runtime-manifest.json',output/'renders')
    rows={r['id']:r for r in runtime['cases']}
    before=rows[chosen['id'].replace('_','-')];after=rows['compact-selected']
    parity=bool(before.get('renders') and len(before['renders'])==len(after.get('renders',[])) and
                all(a['sha256']==b['sha256'] for a,b in zip(before['renders'],after['renders'])))
    if not parity:raise ValueError('Lossless packaged asset changed actual AR render pixels')
    lod_comparison=None
    if lod:
        a,b=rows['lod-control'],rows['compact-lod']
        lod_parity=bool(a.get('renders') and len(a['renders'])==len(b.get('renders',[])) and
                        all(x['sha256']==y['sha256'] for x,y in zip(a['renders'],b['renders'])))
        from .mobile_lod import compare_lod_cards
        lod_comparison=compare_lod_cards(output/'renders'/before['card']['path'],output/'renders'/b['card']['path'])
        lod_comparison['packaged_render_parity']=lod_parity
        lod_comparison['retained']=bool(lod_parity and lod_comparison['passed'] and compact_lod['triangles']<compact['triangles'])
        if not lod_parity:lod_comparison['rejection_reason']='LOD packaging changed rendered pixels; verified original compact asset retained'
        if lod_comparison['retained']:
            compact=compact_lod;selected_compact_folder='compact-lod'
        _write(output/'lod-comparison.json',lod_comparison)
    refs=[{'path':str(output/'frame'/'report.json'),'sha256':_sha(output/'frame'/'report.json')},
          {'path':str(output/'compact'/'report.json'),'sha256':_sha(output/'compact'/'report.json')},
          {'path':str(output/'renders'/'report.json'),'sha256':_sha(output/'renders'/'report.json')}]
    if lod:
        refs += [{'path':str(output/folder/'report.json'),'sha256':_sha(output/folder/'report.json')} for folder in ('lod','compact-lod')]
    optical={}
    if physical_report is not None:
        physical=_read(physical_report);refs.append({'path':str(Path(physical_report).resolve()),'sha256':_sha(physical_report)})
        selected=physical.get('selected_hypothesis')
        hypothesis=next((h for h in physical['hypotheses'] if selected and h['index']==selected['index']),None)
        if hypothesis:
            optical['contamination_fraction']=hypothesis.get('composition_contamination')
            optical['opaque_core_fraction']=max([v['opaque_core_fraction'] for v in hypothesis.get('lens_core_audit',[])
                                                if v['opaque_core_fraction'] is not None],default=None)
    if selection:
        optical['maximum_validation_error_codes']=selection.get('score_codes')
    tracks=sum(m['tracks'] for m in frame['materials'])
    observed=sum(m['three_view_tracks'] for m in frame['materials'])
    coverage=frame.get('surface_coverage',{})
    evidence={'candidate_sha256':compact['model']['sha256'],'integrity_verified':True,
              'runtime_passed':runtime.get('status')=='passed' and runtime.get('source_snapshot_stable') is True,
              'optical':optical,'frame':{'observed_fraction':coverage.get('observed_fraction') if coverage.get('denominator_complete') is True else None,
                  'eligible_texture_track_fraction':observed/tracks if tracks else None,
                  'surface_coverage':coverage,
                  'coverage_scope':'Complete non-optical triangle area with angularly diverse barycentric support.'},
              'source_reports':refs,'packaged_render_parity':parity,
              'reconstruction_history_receipts':list(reconstruction_history_receipts)}
    if refinement_reference is not None:
        from .required_parts import run_required_parts_stage
        region_reference = {'path':str(Path(region_report).resolve()),'sha256':_sha(region_report)}
        parts = run_required_parts_stage(compact['model']['path'], refinement_reference,
                                        region_reference, output/'required-parts')
        evidence['measurement_receipts'] = {'parts':parts['measurement']}
        evidence['trusted_refinement_reference'] = refinement_reference
        evidence['trusted_region_references'] = [region_reference]
        evidence['source_reports'] += [refinement_reference,region_reference,parts['measurement']]
    if evaluation_context is not None:
        evidence['evaluation_context'] = evaluation_context
        evidence.setdefault('measurement_receipts',{})['heldout'] = measure_reserved_views(
            evaluation_context, compact['model']['path'], output/'reserved-evaluation',
            history_receipts=reconstruction_history_receipts)
        evidence['source_reports'] += [evaluation_context['reservation'], *evaluation_context['usage_receipts']]
        evidence['source_reports'].append(evaluation_context['candidate_commitment'])
    if validation_evidence is not None:
        supplied=_read(validation_evidence) if not isinstance(validation_evidence,dict) else validation_evidence
        if supplied.get('candidate_sha256')!=compact['model']['sha256']:
            raise ValueError('Independent validation evidence belongs to another final asset')
        extra = supplied.get('measurement_receipts',{})
        if set(extra) & set(evidence.get('measurement_receipts',{})):
            raise ValueError('External measurement cannot replace a current-job evaluation')
        evidence.setdefault('measurement_receipts',{}).update(extra)
        evidence['source_reports']+=supplied.get('source_reports',[])
    quality=run_delivery_validation(compact['model']['path'],output/'validation',evidence=evidence)
    report={'schema_version':1,'method':'photo_reconstruction_delivery_v1','status':'delivery_evaluated',
            'model':compact['model'],'export':compact['export'],'frame_report':'frame/report.json',
            'compact_report':selected_compact_folder+'/report.json','runtime_report':'renders/report.json','validation_report':'validation/report.json',
            'render_parity':parity,'lod_comparison':lod_comparison,'quality':quality,'accepted':quality['accepted'],'quality_verdict':quality['verdict']}
    if refinement_reference is not None: report['required_parts_report'] = 'required-parts/report.json'
    if evaluation_context is not None: report['reserved_evaluation_report'] = 'reserved-evaluation/report.json'
    _write(output/'report.json',report)
    return report
