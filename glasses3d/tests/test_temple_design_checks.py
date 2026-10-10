"""Temple builder checks with no other test file: the warnings W_FLAT_CREASE and W_ARM_NORMAL_SPREAD, and the decal
standoff of measure.decal_standoff. The WP6 v1 instrument tests that shared this file were archived on 2026-10-10
(tag wp6-v1-final; plan/WP6_FACETS.md §7)."""
from __future__ import annotations

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d.temple import measure as MS


# --------------------------------------------------------------------------------------------- warnings
def test_flat_crease_warning_names_the_crease():
    tb = build_temple(FX.pyramid(r_c=0.3, r_h=0.05))
    flat = [w for w in tb.warnings if w["code"] == "W_FLAT_CREASE"]
    assert flat and all(w["deg"] < 2.0 and len(w["regions"]) == 2 for w in flat)
    assert not any(w["code"] == "W_FLAT_CREASE" for w in build_temple(FX.pyramid()).warnings)


def test_arm_normal_spread_warning():
    s = FX.base_spec()
    s["thickness"] = [[0.0, 5.0], [65.0, 5.0], [80.0, 5.0], [82.0, 1.6], [136.5, 1.6]]   # a 3.4 mm drop over 2 mm
    w = [w for w in build_temple(s).warnings if w["code"] == "W_ARM_NORMAL_SPREAD"]
    assert w and w[0]["deg"] > 30.0
    assert not any(w["code"] == "W_ARM_NORMAL_SPREAD" for w in build_temple(FX.base_spec()).warnings)


# --------------------------------------------------------------------------------------------- decals
def test_decal_standoff_keeps_relief_in_proportion():
    lift = 0.05
    flat = MS.decal_standoff(np.array([0.050, 0.0505, 0.051]), lift)
    assert flat.max() <= lift + 1e-15 and flat.min() > 0.045
    ribbon = MS.decal_standoff(np.array([0.020, 0.045, 0.072]), lift)       # the Prada ribbon and shield
    assert ribbon.max() == pytest.approx(lift)
    assert ribbon[0] == pytest.approx(0.020 * lift / 0.072)                  # the relief kept in proportion
    assert np.all(np.diff(ribbon) > 0)                                       # no side wall collapses
    low = MS.decal_standoff(np.array([-0.1, 0.0, 0.01]), lift)
    assert low.min() == pytest.approx(MS.DECAL_MIN_LIFT_FRACTION * lift)
