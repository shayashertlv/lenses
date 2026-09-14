"""Report the worker's advisory lens-surface numbers for saved masters.

Runs inside Blender. It opens each packed master read-only, computes the same
metrics the worker writes into every revision's inspection, and writes one JSON
report. No edit, render, save or provider request happens here.
"""
import json
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'blender'))
import worker  # noqa: E402

arguments = sys.argv[sys.argv.index('--') + 1:]
output = Path(arguments[0]).resolve()
if not output.is_relative_to(ROOT):
    raise ValueError('Report path must stay inside modeling_auto')
report = {}
for value in arguments[1:]:
    source = Path(value).resolve()
    if not source.is_relative_to(ROOT) or source.suffix.lower() != '.blend':
        raise ValueError(f'Only packed masters inside modeling_auto are inspected: {value}')
    bpy.ops.wm.open_mainfile(filepath=str(source))
    report[str(source)] = worker.lens_metrics()
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2), encoding='utf-8')
print('LENS_METRICS_OK', len(report), flush=True)
