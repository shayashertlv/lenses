"""Deterministic JSON outputs of WP5: ``jsonable`` (numpy converted, non-finite floats as null), ``write_json``
(strict: no NaN, UTF-8, LF) and ``strip_timing`` (timing fields dropped, so two runs compare byte for byte).

Step 0's region statuses, B1 / B2, STOP A and stability reports (``region_statuses``, ``finalize`` and their helpers)
were deleted on 2026-10-13 with the step-0 experiment; see git tag review-20261013-pre-cleanup. The bake still reads a
recorded step-0 report.json (``project.registered_from_step0``).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

TIMING_KEYS = ("seconds",)


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(x) for x in o]
    if isinstance(o, (np.floating,)):
        o = float(o)
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o


def strip_timing(o):
    if isinstance(o, dict):
        return {k: strip_timing(v) for k, v in o.items() if k not in TIMING_KEYS}
    if isinstance(o, list):
        return [strip_timing(x) for x in o]
    return o


def write_json(path, obj) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(jsonable(obj), indent=1, ensure_ascii=False, allow_nan=False)
    p.write_text(text + "\n", encoding="utf-8", newline="\n")
    return str(p)
