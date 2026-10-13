"""The deterministic JSON writer (WP5_DESIGN §9). Step 0's status and STOP A tests were deleted on 2026-10-13 with
the step-0 experiment; see git tag review-20261013-pre-cleanup."""
import json

import numpy as np

from glasses3d.g3d_host.photoprint import report as RP


def test_json_is_deterministic_and_strict(tmp_path):
    obj = {"b": np.float32(1.5), "a": [np.int64(3), np.bool_(True), float("nan")], "c": np.arange(3),
           "seconds": 4.2, "nested": {"seconds": 1.0, "x": 2}}
    p1 = RP.write_json(tmp_path / "a.json", RP.strip_timing(obj))
    p2 = RP.write_json(tmp_path / "b.json", RP.strip_timing(obj))
    t1 = open(p1, "rb").read()
    assert t1 == open(p2, "rb").read()
    d = json.loads(t1)
    assert d == {"b": 1.5, "a": [3, True, None], "c": [0, 1, 2], "nested": {"x": 2}}
