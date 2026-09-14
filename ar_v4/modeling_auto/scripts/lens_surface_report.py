"""Print the advisory lens-surface numbers for saved revisions; zero paid calls.

    python scripts/lens_surface_report.py --job <job id> [--job <job id>]
    python scripts/lens_surface_report.py <master.blend> [<master.blend> ...]

Every revision with tagged lenses is inspected in one owned Blender process.
The numbers are the same ones the worker stores under inspection["lenses"].
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.config import find_blender  # noqa: E402


def revisions_of(job_id: str):
    job = json.loads((ROOT / 'data' / 'jobs' / job_id / 'job.json').read_text(encoding='utf-8'))
    for revision in job['revisions']:
        if revision.get('inspection', {}).get('objects') and any(
                item.get('auto_role') in ('lens_left', 'lens_right') for item in revision['inspection']['objects']):
            yield job['name'], revision['id'], revision['stage'], Path(revision['blend_path'])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('masters', nargs='*', help='packed .blend masters inside modeling_auto')
    parser.add_argument('--job', action='append', default=[], help='inspect every lens-bearing revision of this saved job')
    parser.add_argument('--timeout', type=int, default=1200)
    args = parser.parse_args()
    targets = []
    for job_id in args.job:
        targets.extend(revisions_of(job_id))
    targets.extend(('', Path(m).stem, '', Path(m)) for m in args.masters)
    if not targets:
        parser.error('Give at least one master or --job')
    blender = find_blender()
    if not blender:
        raise SystemExit('No Blender executable found; set MODELING_AUTO_BLENDER')
    output = ROOT / 'data' / 'selftest' / ('lens-metrics-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True)
    report_path = output / 'report.json'
    with (output / 'blender.log').open('wb') as log:
        result = subprocess.run([blender, '--background', '--factory-startup', '--disable-autoexec',
                                 '--python', str(ROOT / 'blender' / 'lens_metrics_probe.py'), '--',
                                 str(report_path), *[str(t[3]) for t in targets]],
                                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
    if result.returncode or not report_path.is_file():
        raise SystemExit(f'Blender probe failed; see {output / "blender.log"}')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    header = f"{'job':14} {'rev':5} {'stage':12} {'lens':18} {'ripple p90':>10} {'sign mix':>9} {'curv cv':>8} {'sphere rms':>10} {'crease':>7}"
    print(header)
    print('-' * len(header))
    for name, revision, stage, master in targets:
        lenses = report.get(str(master.resolve()), {})
        for lens_name, metrics in sorted(lenses.items()):
            summary = (metrics or {}).get('summary') or {}
            def fmt(key, scale=1.0, digits=2):
                value = summary.get(key)
                return f'{value * scale:.{digits}f}' if isinstance(value, (int, float)) else '-'
            crease = metrics.get('crease_edge_fraction') if metrics else None
            print(f"{name[:14]:14} {revision:5} {stage[:12]:12} {lens_name[:18]:18} {fmt('worst_ripple_p90_deg'):>10} "
                  f"{fmt('worst_sign_mix', 100, 0):>8}% {fmt('worst_curvature_cv'):>8} {fmt('worst_sphere_fit_rms_fraction', 1, 4):>10} "
                  f"{(f'{crease * 100:.1f}%' if isinstance(crease, (int, float)) else '-'):>7}")
    print(f'\nReport: {report_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
