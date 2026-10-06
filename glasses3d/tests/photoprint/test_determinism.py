"""A7 (WP5_DESIGN §8): two step-0 runs give byte-identical registration.json, check_print.json and sheets; one run
serial, the other in a process pool (results are collected in job order, never completion order)."""
import hashlib
import shutil
import tempfile
from pathlib import Path

import pytest

from glasses3d.g3d_host.photoprint import step0
from glasses3d.tests.photoprint import synth

pytestmark = pytest.mark.slow


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def workdir():
    """A private temp dir: pytest rotates its own basetemp dirs, and other sessions on this machine can remove ours."""
    d = Path(tempfile.mkdtemp(prefix="wp5_a7_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_two_runs_are_byte_identical(synthetic, workdir):
    tmp_path = workdir
    job, truth = synthetic
    job = job.with_cameras(truth["cameras"])          # start at the truth so brows register and check_print runs
    # min_decoys=0: this test is about determinism, not the decoy rule; the design's rule (each decoy's own
    # displacement keeps 60 % overlap) leaves the thin brow fronts seen from 01 with 0-1 decoys even on these perfect
    # photos (A0: overlaps 0.69 / 0.50 / 0.41 / 0.32 / 0.28 / 0.20), nothing would register and check_print (which the
    # test must cover) would not run
    # one perturbed start per fold covers the stability fits and their gates; two Powell runs per band cover restarts
    kw = dict(n_samples=1500, maxfev=30, seed=0, b0=True, sheets=True, log=None, min_decoys=0, stability_starts=1,
              max_runs=2)
    r1 = step0.run_step0(job, synth.PRINT_PARTS, tmp_path / "serial", workers=1, **kw)
    r2 = step0.run_step0(job, synth.PRINT_PARTS, tmp_path / "pool", workers=3, **kw)
    for name in ("registration.json", "check_print.json"):
        a, b = tmp_path / "serial" / name, tmp_path / "pool" / name
        assert a.read_bytes() == b.read_bytes(), name
    assert r1["check"]["rows"], "check_print produced no rows: the determinism test would not cover it"
    assert r1["registration"]["stability"]["fits"] and r1["registration"]["stability"]["gates"]
    sheets1 = sorted((tmp_path / "serial" / "sheets").glob("*.png"))
    sheets2 = sorted((tmp_path / "pool" / "sheets").glob("*.png"))
    assert [p.name for p in sheets1] == [p.name for p in sheets2] and sheets1
    assert [_sha(p) for p in sheets1] == [_sha(p) for p in sheets2]
    assert r1["report"]["timings"] and "seconds" not in (tmp_path / "serial" / "registration.json").read_text()
