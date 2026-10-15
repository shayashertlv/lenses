"""Temple builder checks with no other test file: the warnings W_FLAT_CREASE and W_ARM_NORMAL_SPREAD (g3d.temple/2).
The WP6 v1 instrument tests that shared this file were archived on 2026-10-10 (tag wp6-v1-final; plan/WP6_FACETS.md
§7); measure.decal_standoff and its test were deleted on 2026-10-13 (decals move by chart transfer)."""
from __future__ import annotations

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import report


# --------------------------------------------------------------------------------------------- warnings
def test_flat_crease_warning_names_the_crease():
    tb = build_temple(FX.pyramid(t_c=FX.T_ZONE + 0.3, t_h=FX.T_ZONE + 0.05))
    flat = [w for w in tb.warnings if w["code"] == "W_FLAT_CREASE"]
    assert flat and all(w["deg"] < 2.0 and len(w["regions"]) == 2 for w in flat)
    lines = report.crease_lines(tb)
    assert all(any(ln.startswith("|".join(w["regions"]) + " built angle ") for ln in lines) for w in flat)
    assert not any(w["code"] == "W_FLAT_CREASE" for w in build_temple(FX.pyramid()).warnings)


def test_arm_normal_spread_warning():
    drop = [[0.0, 5.0], [65.0, 5.0], [80.0, 5.0], [82.0, 1.6], [136.5, 1.6]]           # a 3.4 mm drop over 2 mm
    s = FX.base_spec(base={"top": drop, "bottom": drop})
    w = [w for w in build_temple(s).warnings if w["code"] == "W_ARM_NORMAL_SPREAD"]
    assert w and w[0]["deg"] > 30.0
    assert not any(w["code"] == "W_ARM_NORMAL_SPREAD" for w in build_temple(FX.base_spec()).warnings)
