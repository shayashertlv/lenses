"""The Python coating search equals the Edit page's JavaScript byte for byte (audit B3)."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from glasses3d.agent import coating_match as M

ROOT = Path(__file__).parents[2]
CASES = [
    {"values": {"ior": 6, "tint": [1, 1, 1], "metallic": 0, "iridescence": 1, "iridescence_ior": 1.8,
                "iridescence_thickness_max": 435},
     "cosines": [1, .9, .7, .4, .1, 0], "targets": {"straight_on": [90, 190, 140], "sides": [170, 110, 200]}},
    {"values": {"ior": 1.6, "tint": [.2, .6, .4], "metallic": .3, "iridescence": .7, "iridescence_ior": 1.3,
                "iridescence_thickness_max": 0},
     "cosines": [1, .55, .4], "targets": {"straight_on": [200, 60, 60], "sides": [60, 80, 220]}},
    {"values": {"ior": 3, "tint": [.9, .8, .1], "metallic": .9, "iridescence": .5, "iridescence_ior": 2.4,
                "iridescence_thickness_max": 1200},
     "cosines": [1, .4], "targets": {"straight_on": [128, 128, 128], "sides": [20, 200, 30]}},
    {"values": {"ior": 3, "tint": [1, 1, 1], "metallic": 0, "iridescence": 1, "iridescence_ior": 1.5,
                "iridescence_thickness_max": 250},
     "cosines": [.95, .6, .3], "targets": {"straight_on": [230, 150, 40], "sides": [230, 150, 40]}},
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_python_port_equals_the_edit_page_javascript():
    output = subprocess.run(["node", str(Path(__file__).with_name("coating_parity.mjs")), str(ROOT), json.dumps(CASES)],
                            capture_output=True, text=True, check=True, timeout=120).stdout
    for case, reference in zip(CASES, json.loads(output), strict=True):
        assert [M.swatch_srgb(case["values"], c)[0].tolist() for c in case["cosines"]] == reference["swatches"]
        found = M.find_coating(case["targets"]["straight_on"], case["targets"]["sides"], case["values"])
        assert found["reason"] == reference["solve"]["reason"]
        assert [(c["thickness_nm"], c["film_ior"], c["quality"]) for c in found["choices"]] == [
            (c["thickness"], c["film_ior"], c["quality"].lower()) for c in reference["solve"]["choices"]]
        for mine, theirs in zip(found["choices"], reference["solve"]["choices"]):
            assert mine["error_straight_on"] == pytest.approx(theirs["straight"], abs=.006)
            assert mine["error_sides"] == pytest.approx(theirs["sides"], abs=.006)


def test_oakley_targets_rediscover_the_accepted_run_4_coating_quickly():
    import time
    start = time.perf_counter()
    found = M.find_coating([90, 190, 140], [170, 110, 200], {"ior": 6, "tint": [1, 1, 1], "metallic": 0,
                                                            "iridescence": 1, "iridescence_ior": 1.3,
                                                            "iridescence_thickness_max": 400})
    assert time.perf_counter() - start < 2
    best = found["choices"][0]
    assert (best["thickness_nm"], best["film_ior"]) == (444, 1.8)  # accepted run 4: 435 nm / film 1.8 / lens 6
    assert best["straight_on_srgb"] == [94, 185, 125] and best["sides_srgb"] == [171, 110, 197]
