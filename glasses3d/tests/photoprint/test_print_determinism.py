"""A7 for steps 2-7 (WP5_DESIGN §8): two bakes, one serial and one in a process pool, give identical PNG sha256
(print, classes, source, tier) and identical registration.json and report_stable.json (the report without timings).
Every PatchMatch call is seeded and pool results are collected in job order."""

from glasses3d.tests.fixtures.legacy_ft1119 import PRINT_PARTS as FT1119_PARTS
import hashlib
import shutil
import tempfile
from pathlib import Path

import pytest

from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import project as PR

pytestmark = pytest.mark.slow


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture
def workdir():
    d = Path(tempfile.mkdtemp(prefix="wp5_a7_print_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_two_bakes_are_byte_identical(ft1119_job, workdir):
    kw = dict(print_reg_mm=0.8, edge_mean_mm=1.0, edge_p90_mm=2.5, tx_per_mm=5.0, log=None, seed=3)
    PR.project_photo_texture(ft1119_job, FT1119_PARTS, out_dir=workdir / "serial", workers=1, **kw)
    PR.project_photo_texture(ft1119_job, FT1119_PARTS, out_dir=workdir / "pool", workers=3, **kw)
    for name in ("print.png", "classes.png", "source.png", "tier.png", "registration.json", "report_stable.json",
                 "layout.json"):
        assert _sha(workdir / "serial" / name) == _sha(workdir / "pool" / name), name
    assert "timings" not in (workdir / "serial" / "report_stable.json").read_text(encoding="utf-8")
