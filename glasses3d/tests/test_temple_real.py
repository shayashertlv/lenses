"""WP6 on the real Prada data (skipped without the reference data or the glossy WP3b cameras): the job finds the two
temples by geometry, the re-recorded glossy WP3b table matches the WP6 measurement, the delivered temples read as a
slab to the crease instrument on the side photos, and the guides file the $0 proof uses is well formed."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX

G3D = Path(__file__).resolve().parents[1]
REF = G3D / "data" / "reference" / "prada" / "manifest.json"
GLOSSY = G3D / "data" / "wp6" / "wp3b_glossy" / "cameras.json"
TABLE = G3D / "data" / "wp6" / "wp3b_glossy" / "table.json"
GUIDES = G3D / "tests" / "proofs" / "wp6_prada_guides.json"

pytestmark = pytest.mark.skipif(not REF.exists(), reason="Prada reference data not imported")


@pytest.fixture(scope="module")
def job():
    from glasses3d.g3d_host import relief_views as RV
    return RV.Job("prada")


def test_job_finds_the_temples_by_geometry(job):
    names = {s: job.mesh.node_names[job.temple_part[s]] for s in (1, -1)}
    # +X is Astra's "right" temple (the wearer's left); the job never looked at the names
    assert "right" in names[1] and "left" in names[-1]
    for s in (1, -1):
        V, F = job.delivered_temples[s]
        assert np.sign(V[:, 0].mean()) == s and V[:, 2].max() - V[:, 2].min() > 120


@pytest.mark.skipif(not TABLE.exists(), reason="glossy WP3b cameras not recorded (tests/proofs/wp6_wp3b_glossy.py)")
def test_glossy_wp3b_table_matches_the_measurement():
    t = json.loads(TABLE.read_text(encoding="utf-8"))["table"]
    want = {"reference-01.webp": 1.43, "reference-02.webp": 1.13, "reference-03.png": 0.97, "reference-04.png": 0.31,
            "reference-05.png": 1.25, "reference-06.png": 2.93}
    for name, mm in want.items():
        assert t[name]["glossy_floor"]["contour_mean_mm"] == pytest.approx(mm, abs=0.01)
    assert t["reference-01.webp"]["old"]["contour_mean_mm"] == pytest.approx(5.58, abs=0.01)


@pytest.mark.skipif(not GLOSSY.exists(), reason="glossy WP3b cameras not recorded")
def test_delivered_side_creases_are_shallow_or_flag(job):
    from glasses3d.g3d_host import relief_check as RC
    from glasses3d.g3d_host import relief_views as RV
    assert job.cameras_path == GLOSSY
    model = RV.Model.delivered(job)
    segs = []
    for key, near in (("05", 1), ("01", -1)):
        cam = job.base[key]
        r = RV.render_model(job, model, key, cam, {1: 0.0, -1: 0.0}, points=True, normals=True)
        seg = RC.photo_crease_segments(job, key, r, cam, {1: 0.0, -1: 0.0}, near,
                                       decal_mask=RC.decal_mask(job, key, cam))
        assert seg["usable"], seg
        segs += seg["segments"]
    assert segs and all(s["verdict"] in ("SHALLOW", "FLAG") for s in segs)
    assert max(s["dn_deg"] for s in segs) < 6.0                # WP6 design: delivered <= 4.6 deg on the side segments


def test_guides_file_builds_a_valid_start_spec():
    gp = json.loads(GUIDES.read_text(encoding="utf-8"))
    assert set(gp["guides"]) == {"05", "01", "02"}
    for key, lines in gp["guides"].items():
        assert set(lines) == {"AB", "AC", "BC"}
    from glasses3d.tests.proofs.wp6_prada import LABEL, start_params
    assert "TOOL TEST" in LABEL and "TOOL TEST" in gp["label"]
    tb = build_temple(FX.prada_spec(**start_params(None, gp)))
    assert tb.checks["closed"] and set(tb.spec.facets) == {"F1", "F2", "F3", "F4"}
