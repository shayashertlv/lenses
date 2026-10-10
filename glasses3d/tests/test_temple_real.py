"""Temple data on the real Prada job (skipped without the reference data or the glossy WP3b cameras): the
re-recorded glossy WP3b table matches its measurement. WP6 v2 copies that camera basis's frame and skip verbatim
(plan/WP6_FACETS.md §3.1). The v1 tests that read the delivered temples through relief_views / relief_check and the
v1 proof's guides file were archived on 2026-10-10 (tag wp6-v1-final)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

G3D = Path(__file__).resolve().parents[1]
REF = G3D / "data" / "reference" / "prada" / "manifest.json"
TABLE = G3D / "data" / "wp6" / "wp3b_glossy" / "table.json"

pytestmark = pytest.mark.skipif(not REF.exists(), reason="Prada reference data not imported")


@pytest.mark.skipif(not TABLE.exists(), reason="glossy WP3b cameras not recorded (archived wp6_wp3b_glossy.py)")
def test_glossy_wp3b_table_matches_the_measurement():
    t = json.loads(TABLE.read_text(encoding="utf-8"))["table"]
    want = {"reference-01.webp": 1.43, "reference-02.webp": 1.13, "reference-03.png": 0.97, "reference-04.png": 0.31,
            "reference-05.png": 1.25, "reference-06.png": 2.93}
    for name, mm in want.items():
        assert t[name]["glossy_floor"]["contour_mean_mm"] == pytest.approx(mm, abs=0.01)
    assert t["reference-01.webp"]["old"]["contour_mean_mm"] == pytest.approx(5.58, abs=0.01)
