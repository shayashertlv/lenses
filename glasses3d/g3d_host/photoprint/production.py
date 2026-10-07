"""Reusable host print driver. Explicit job, parts, thresholds and calibration; no fixture loading.

The independent test driver may add knockout/held-out proofs to this result. A successful bake is not a claim
that those quality gates passed, and cannot silently upgrade single-source print to pixel-verified print.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from . import inputs as IN
from . import project as PR
from .report import jsonable, write_json

# Frozen WP5 registration/edge settings, formerly owned by tests/photoprint/print_proof.py. A caller must still
# explicitly supply thresholds. These settings do not change the independent quality gates.
THRESHOLDS = {'print_reg_mm': 0.8, 'edge_mean_mm': 1.0, 'edge_p90_mm': 2.5}


def validate_thresholds(thresholds):
    if not isinstance(thresholds, dict) or set(thresholds) != set(THRESHOLDS):
        raise PR.RefusedError('explicit print_reg_mm, edge_mean_mm and edge_p90_mm thresholds are required')
    values = {k: float(v) for k, v in thresholds.items()}
    if any(not math.isfinite(v) or v <= 0 for v in values.values()):
        raise PR.RefusedError('print thresholds must be finite and positive')
    return values


def bake_print(job, parts, *, out_dir, thresholds, calibration=None, workers=None, **options):
    """Run the production bake with explicit inputs; return the full arrays and a compact host receipt.

    Optional calibrated level records preserve a bound owner selection. Material attachment is a separate
    Blender tool operation, so baking never modifies the input model or overrides Astra's materials.
    """
    thresholds = validate_thresholds(thresholds)
    if not parts or isinstance(parts, str):
        raise PR.RefusedError('explicit print parts are required')
    parts = list(parts)
    if len(set(parts)) != len(parts):
        raise PR.RefusedError('print parts must be unique')
    PR.refuse_parts(job, parts)
    if workers is not None and (isinstance(workers, bool) or int(workers) != workers or workers < 1):
        raise PR.RefusedError('workers must be a positive integer')
    validate_registration_inputs(job, options.get('step0_report'))
    if calibration is not None and ('level' in options or 'level_source' in options):
        raise PR.RefusedError('provide either a calibration record or an explicit level, not both')
    level, source = read_level(calibration)
    if calibration is not None:
        options.update(level=level, level_source=source)
    result = PR.project_photo_texture(job, parts, out_dir=out_dir, workers=workers, **thresholds, **options)
    report = result['report']
    receipt = {'schema': 'g3d.print_production/1', 'parts': parts, 'inputs': report['inputs'],
               'thresholds': thresholds, 'mode': report['mode'], 'layout': report['layout'],
               'area_table': report['area_table'], 'warnings': report['warnings'],
               'calibration': {'path': str(calibration), 'sha256': IN.sha256_file(calibration),
                               'reason': source} if calibration is not None else None,
               'quality_proof': {'status': 'not_run', 'note': 'Independent A/B proof required; baking is not acceptance.'}}
    if options.get('write', True):
        out = Path(out_dir)
        receipt['print_sha256'] = IN.sha256_file(out / 'print.png')
        receipt['manifest'] = str(out / 'print_manifest.json')
        write_json(out / 'production.json', jsonable(receipt))
    result['production'] = receipt
    return result


def validate_registration_inputs(job, path):
    """A frozen registration report cannot certify a different model, camera basis or set of photos."""
    if path is None:
        return
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    source = record.get('inputs') or {}
    source = source.get('sources', source)  # bake receipts wrap the same source identities
    for key in ('glb_sha256', 'cameras_sha256'):
        if not source.get(key) or source[key] != job.sources.get(key):
            raise PR.RefusedError(f'registration report {key} differs from this job')
    photos = {k: v.get('sha256') for k, v in source.get('images', {}).items()}
    if photos != {k: v.sha256 for k, v in job.views.items()}:
        raise PR.RefusedError('registration report photos differ from this job')


def calibrate_level(job, groups, *, bake, glb, out_dir, chroma_family, temple_z=None,
                    reference_point_mm=None, **options):
    """Measure an unlevelled bake through arview. Preserve prior captures and owner choices.

    ``groups`` maps semantic labels (e.g. brow/temple) to actual job part names. ``chroma_family`` is an explicit
    level-map policy (an empty mapping permits all hues). The result recommends a level; it never promotes it.
    """
    from .ar_calibrate import Calibrator

    bake, glb, out = Path(bake).resolve(), Path(glb).resolve(), Path(out_dir).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('calibration output must be new or empty; preserve prior captures and owner selections')
    report = json.loads((bake / 'report.json').read_text(encoding='utf-8'))
    if (report.get('level') or {}).get('applied'):
        raise ValueError('calibration requires an unlevelled bake; do not apply a level twice')
    validate_registration_inputs(job, bake / 'report.json')
    calibrator = Calibrator(job, groups, chroma_family=chroma_family, temple_z=temple_z,
                            reference_point_mm=reference_point_mm)
    if set(report['parts']) != set(calibrator.PRINT_PART_NAMES):
        raise ValueError('calibration groups must cover exactly the baked parts')
    from .. import glb as glbmod
    model = glbmod.load(glb)
    image_index = calibrator.print_image_index(model.doc)
    if hashlib.sha256(model.image_bytes(image_index)).hexdigest() != IN.sha256_file(bake / 'print.png'):
        raise ValueError('calibration template does not contain this unlevelled print')
    from .manifest import check_glb
    attachment = check_glb(glb, bake)
    if not attachment['ok']:
        raise ValueError(f'calibration template has stale geometry or UVs: {attachment["reasons"]}')
    result = calibrator.calibrate(bake, glb, out, **options)
    result['inputs'] = {'job': job.sources, 'groups': calibrator.measurements.GROUPS,
                        'chroma_family': chroma_family, 'template_sha256': IN.sha256_file(glb),
                        'bake_print_sha256': IN.sha256_file(bake / 'print.png'), 'attachment': attachment}
    write_json(out / 'calibration.json', jsonable(result))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--job', required=True, type=Path)
    ap.add_argument('--basis', required=True)
    ap.add_argument('--glb', required=True, type=Path)
    ap.add_argument('--parts', required=True, nargs='+')
    ap.add_argument('--thresholds', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--calibration', type=Path)
    ap.add_argument('--workers', type=int)
    a = ap.parse_args(argv)
    if a.out.exists() and any(a.out.iterdir()):
        ap.error('--out must be a new or empty directory; preserve earlier bakes')
    job = IN.job_from_dir(a.job, glb=a.glb, basis=a.basis)
    res = bake_print(job, a.parts, out_dir=a.out, thresholds=json.loads(a.thresholds.read_text(encoding='utf-8')),
                     calibration=a.calibration, workers=a.workers)
    print(json.dumps(res['production'], default=str))
    return 0


def read_level(path):
    """Read the owner's bound production choice, else the calibration's fitted recommendation.

    Keep the fitted top-level ``level`` and its diagnostics intact. A production_selection names a candidate and
    repeats its approved level; a changed/missing candidate refuses instead of silently reverting to the fit.
    """
    if not path:
        return None, None
    raw = Path(path).read_bytes()
    d = json.loads(raw)
    selected = d["level"]
    reason = "fitted recommendation"
    if "production_selection" in d:
        choice = d["production_selection"]
        if (not isinstance(choice, dict) or not isinstance(choice.get("candidate"), str)
                or not isinstance(choice.get("reason"), str) or not choice["reason"].strip()
                or not isinstance(choice.get("owner_words"), str) or not choice["owner_words"].strip()):
            raise ValueError("production_selection requires a candidate, reason and exact owner words")
        candidate = (d.get("candidates") or {}).get(choice["candidate"])
        if not isinstance(candidate, dict) or not isinstance(candidate.get("level"), dict):
            raise ValueError("the owner's selected production candidate is missing")
        if choice.get("level") != candidate["level"]:
            raise ValueError("the owner's selected level changed; a new owner choice is required")
        selected, reason = candidate["level"], choice["reason"]
    lv = {"gain": float(selected["gain"]), "gamma": float(selected["gamma"])}
    if "sat" in selected:
        lv["sat"] = float(selected["sat"])
    if "chroma_power" in selected:
        lv["chroma_power"] = float(selected["chroma_power"])
    if selected.get("chroma_hold"):
        lv["chroma_hold"] = True
    if selected.get("chroma_hue") is not None:
        lv["chroma_hue"] = [float(x) for x in selected["chroma_hue"]]
    if any(not math.isfinite(lv[k]) or lv[k] <= 0 for k in ('gain', 'gamma', 'sat', 'chroma_power') if k in lv):
        raise ValueError('calibration level parameters must be finite and positive')
    if 'chroma_hue' in lv and (len(lv['chroma_hue']) != 2 or not all(map(math.isfinite, lv['chroma_hue']))):
        raise ValueError('calibration hue band must contain two finite angles')
    return lv, f"{reason}; {path} (sha256 {hashlib.sha256(raw).hexdigest()[:12]})"


if __name__ == "__main__":
    raise SystemExit(main())
