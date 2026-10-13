"""Exact-reproduction proof of the WP3b port (PLAN.md §4 WP3b): re-fit FT1119 references 02 (rear), 03 (side) and
04 (top) on quality-001 through the PRODUCTION path, ``cameras.fit_best`` over the first-class priors, with
make_uv_test.py's two starts per view (``n_refine=2``), and compare with the prototype's pickled cameras. back, right
and left use ``prior_seeds(view)`` as is; top and topb use its first 18 seeds, which are exactly make_uv_test.py's
explicit grid (asserted): the production top prior appends a +45..62 band after it (cameras.TOP_LOW_PITCHES, measured
in PORT_REVIEW §9), which ends in the same basin on reference-04 (0.06-0.21 deg) but is not bit-identical. The photos come
from the sha256-checked ``data/reference/ft1119/`` (``fit_views.reference_files``); the frame from fits.json.

Library dependence (measured 2026-10-05, see PORT_REVIEW §9): the prototype cameras were fitted under scipy 1.16.0.
scipy 1.18.1's ``least_squares`` returns a different step (about 1e-8 deg) on the very first contour-ICP call with
bit-identical inputs, and the fit's discrete accept/reject decisions amplify that to 0.19 deg on 03/04. Results that
sensitive also depend on the BLAS kernels the CPU selects, so the EXACT test runs only on a recorded fingerprint
(scipy version, numpy/scipy BLAS builds, CPU model, core count) and skips elsewhere with the reason:

- ``test_cameras_equal_the_prototype_pickles_exactly``: the prototype's own fingerprints (host Python 3.12 with
  scipy 1.16.0; and the venv's numpy with scipy 1.16.0 overlaid) -> cameras to 1e-6, metrics and losses to 1e-9.

A second exact test (port == the original automation/bsa code, golden recorded under scipy 1.18.1) could never run
under the pinned scipy 1.16.0 (requirements.txt); it was deleted 2026-10-13 (git tag review-20261013-pre-cleanup).

The portable gates run everywhere: the chosen views are the prototype's (back, right, top) with every loser at least
0.1 worse on the common level, and every camera lies within a cross-version tolerance of the prototype's.
"""
from __future__ import annotations

import json
import os
import platform
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pytest
import scipy

from glasses3d.g3d_host import camera as C
from glasses3d.g3d_host import cameras, fit_views, fitmesh
from glasses3d.g3d_host import matte as M

G3D = Path(__file__).resolve().parents[1]
REF = G3D / "data" / "reference" / "projection"       # the prototype's q001.glb, fits.json and pickles

# make_uv_test.py:93-94's explicit grid; the first 18 of prior_seeds('top'/'topb') must equal it (asserted below)
TOP_SEEDS = [(y, p, 0.0) for y in (-6.0, 0.0, 6.0) for p in (70.0, 80.0, 88.0, -70.0, -80.0, -88.0)]
CASES = {"reference-02.png": ("back",),
         "reference-03.png": ("right", "left"),
         "reference-04.png": ("top", "topb")}
WINNER = {"reference-02.png": "back", "reference-03.png": "right", "reference-04.png": "top"}

# make_uv_test.py's own numbers (data/reference/projection/rerun/report.json; losses from the same run's log)
PROTOTYPE = {
    "reference-02.png": {"iou": 0.8970163200556316, "contour": 5.458052158355713, "loss": {"back": 0.1197906149970419}},
    "reference-03.png": {"iou": 0.7382309427372608, "contour": 11.473937034606934,
                         "loss": {"right": 0.29674252724266315, "left": 0.7165155809685947}},
    "reference-04.png": {"iou": 0.8302047451329488, "contour": 5.243625640869141,
                         "loss": {"top": 0.18902028013146596, "topb": 0.3402526127406986}},
}
# cross-version tolerance vs the prototype (largest seen under scipy 1.18.1: 0.19 deg, persp 0.004, scale 0.22 %,
# centre 1.07 px, IoU 0.0007, contour 0.02 px)
TOL = {"angle_deg": 0.5, "perspective": 0.02, "scale_rel": 0.005, "center_px": 2.0, "iou": 0.003, "contour_px": 0.1}

# ----------------------------------------------------------------------------- recorded fingerprints
_CPU = {"cpu": "Intel(R) Core(TM) Ultra 9 285H", "cpu_count": 16}
_VENV_NUMPY = {"numpy": "2.5.3", "numpy_blas": "scipy-openblas 0.3.34.106.0"}
_SCIPY_1_16 = {"scipy": "1.16.0", "scipy_blas": "scipy-openblas 0.3.28", "scipy_lapack": "scipy-openblas 0.3.28"}
PROTOTYPE_FINGERPRINTS = (
    {**_CPU, "numpy": "2.3.1", "numpy_blas": "scipy-openblas 0.3.29", **_SCIPY_1_16},   # host Python (the prototype's)
    {**_CPU, **_VENV_NUMPY, **_SCIPY_1_16},                    # venv + scipy 1.16.0 overlaid (PORT_REVIEW §9 table)
)


def _dep(cfg: dict, key: str) -> str:
    d = cfg.get("Build Dependencies", {}).get(key, {})
    return f"{d.get('name')} {d.get('version')}"


def _cpu_model() -> str:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except Exception:          # not Windows: the platform string (never equal to the recorded Windows name)
        return platform.processor() or platform.machine()


def fingerprint() -> dict:
    ncfg, scfg = np.show_config(mode="dicts"), scipy.show_config(mode="dicts")
    return {"cpu": _cpu_model(), "cpu_count": os.cpu_count(), "numpy": np.__version__,
            "numpy_blas": _dep(ncfg, "blas"), "scipy": scipy.__version__, "scipy_blas": _dep(scfg, "blas"),
            "scipy_lapack": _dep(scfg, "lapack")}


def _skip_unless(fps: tuple[dict, ...], what: str) -> None:
    fp = fingerprint()
    if fp not in fps:
        diff = {k: (fp.get(k), fps[0].get(k)) for k in fps[0] if fp.get(k) != fps[0].get(k)}
        pytest.skip(f"{what} is bit-exact only on its recorded fingerprint; here (got, recorded) differs in {diff}. "
                    "The portable gates (chosen views, cross-version tolerance) still ran.")


needs_data = pytest.mark.skipif(not fit_views.manifest_path("ft1119").exists() or not (REF / "q001.glb").exists(),
                                reason="reference data never imported (data/reference/ft1119/manifest.json, "
                                       "data/reference/projection/q001.glb)")


def _setup():
    frame = C.NormFrame.from_dict(json.loads((REF / "fits.json").read_text())["frame"])
    mesh = fitmesh.load_fit_mesh(REF / "q001.glb", skip=fitmesh.PROTOTYPE_SKIP, frame=frame)
    return mesh, mesh.scene(), mesh.front_pivot([name + '.001' for name in fitmesh.PROTOTYPE_FRONT_PARTS])


def _seeds(view):
    """None (the production prior) except for top/topb: the production prior's prototype-grid prefix."""
    return cameras.prior_seeds(view)[:len(TOP_SEEDS)] if view in cameras.TOP_VIEWS else None


BAND = "reference-04.png with the full production top prior"


def _refit(job):
    """One reference through cameras.fit_best (runs in a worker process; returns plain data only). ``job`` is a
    CASES name (prototype seeds) or ``BAND`` (reference-04, 'top' only, the full production prior incl. the band)."""
    files = fit_views.reference_files("ft1119")      # raises (fails the test) if the manifest's files are broken
    mesh, scene, pivot = _setup()
    name = "reference-04.png" if job == BAND else job
    cands = [("top", None)] if job == BAND else [(v, _seeds(v)) for v in CASES[name]]
    fg = M.matte(M.load_photo(files[name]))
    t = time.perf_counter()
    best, results = cameras.fit_best(fg, scene, pivot, cands, n_refine=2, sign_margin=False)
    per = {r["view"]: {"view": r["view"], "camera": tuple(r["camera"].to_dict()[k] for k in C.CAMERA_FIELDS),
                       "loss": r["loss"], "common_loss": r["common_loss"], "iou": r["metrics"]["iou"],
                       "contour": r["metrics"]["contour_mean_px"]} for r in results}
    return {"name": job, "best": per[best["view"]], "all": per, "seconds": time.perf_counter() - t}


@pytest.fixture(scope="module")
def all_refits():
    fit_views.reference_files("ft1119")
    t = time.perf_counter()
    jobs = list(CASES) + [BAND]
    workers = int(os.environ.get("G3D_REPRO_WORKERS", len(jobs)))
    if workers <= 1:
        out = [_refit(j) for j in jobs]
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            out = list(ex.map(_refit, jobs))
    res = {o["name"]: o for o in out}
    print(f"\nWP3b reproduction: {len(jobs)} fit_best calls in {time.perf_counter() - t:.0f} s "
          f"(scipy {scipy.__version__})")
    for name, r in res.items():
        b = r["best"]
        print(f"  {name} {b['view']}: IoU {b['iou']:.4f} contour {b['contour']:.3f} px loss {b['loss']:.5f} "
              f"camera {np.round(b['camera'], 6).tolist()}")
    return res


@pytest.fixture(scope="module")
def refits(all_refits):
    return {k: v for k, v in all_refits.items() if k in CASES}


def test_production_top_prior_starts_with_the_prototype_grid():
    assert [s[:3] for s in _seeds("top")] == TOP_SEEDS
    assert [s[:3] for s in _seeds("topb")] == [(180.0 + y, p, r) for y, p, r in TOP_SEEDS]
    assert len(cameras.prior_seeds("top")) == len(TOP_SEEDS) + 3 * len(cameras.TOP_LOW_PITCHES)


@needs_data
@pytest.mark.slow
def test_chosen_views_and_loser_losses_are_the_prototypes(refits):
    for name, r in refits.items():
        assert r["best"]["view"] == WINNER[name], name
        for view, o in r["all"].items():
            if view != WINNER[name]:
                assert o["loss"] > r["best"]["loss"] + 0.1, (name, view)                  # own levels
                assert o["common_loss"] > r["best"]["common_loss"] + 0.1, (name, view)    # one common level


@needs_data
@pytest.mark.slow
def test_cameras_equal_the_prototype_pickles_exactly(refits):
    _skip_unless(PROTOTYPE_FINGERPRINTS, "exact prototype reproduction")
    for name, r in refits.items():
        want = C.load_camera_pickle(REF / f"cam_{name}.pkl")
        for k, g in zip(C.CAMERA_FIELDS, r["best"]["camera"]):
            assert abs(g - getattr(want, k)) <= 1e-6, (name, k, g, getattr(want, k))
        assert abs(r["best"]["iou"] - PROTOTYPE[name]["iou"]) < 1e-9, name
        assert abs(r["best"]["contour"] - PROTOTYPE[name]["contour"]) < 1e-6, name
        for view, loss in PROTOTYPE[name]["loss"].items():
            assert abs(r["all"][view]["loss"] - loss) < 1e-9, (name, view)


@needs_data
@pytest.mark.slow
def test_cameras_within_cross_version_tolerance_of_the_prototype(refits):
    for name, r in refits.items():
        want = C.load_camera_pickle(REF / f"cam_{name}.pkl")
        got = dict(zip(C.CAMERA_FIELDS, r["best"]["camera"]))
        for k in ("yaw", "pitch", "roll"):
            assert abs(got[k] - getattr(want, k)) <= TOL["angle_deg"], (name, k)
        assert abs(got["perspective"] - want.perspective) <= TOL["perspective"], name
        assert abs(got["scale"] / want.scale - 1.0) <= TOL["scale_rel"], name
        assert np.hypot(got["center_x"] - want.center_x, got["center_y"] - want.center_y) <= TOL["center_px"], name
        assert abs(r["best"]["iou"] - PROTOTYPE[name]["iou"]) <= TOL["iou"], name
        assert abs(r["best"]["contour"] - PROTOTYPE[name]["contour"]) <= TOL["contour_px"], name


@needs_data
@pytest.mark.slow
def test_production_top_prior_with_the_low_band_lands_in_the_prototype_basin(all_refits):
    """The band seeds win the coarse stage on reference-04 (true pitch +60), so the camera is not bit-identical, but
    it must be the same basin: within the cross-version tolerance, and no worse in loss than 0.001."""
    r = all_refits[BAND]["best"]
    want = C.load_camera_pickle(REF / "cam_reference-04.png.pkl")
    got = dict(zip(C.CAMERA_FIELDS, r["camera"]))
    for k in ("yaw", "pitch", "roll"):
        assert abs(got[k] - getattr(want, k)) <= TOL["angle_deg"], k
    assert abs(got["scale"] / want.scale - 1.0) <= TOL["scale_rel"]
    assert np.hypot(got["center_x"] - want.center_x, got["center_y"] - want.center_y) <= TOL["center_px"]
    assert abs(r["iou"] - PROTOTYPE["reference-04.png"]["iou"]) <= TOL["iou"]
    assert r["loss"] <= PROTOTYPE["reference-04.png"]["loss"]["top"] + 1e-3


def test_fingerprint_has_every_recorded_key():
    fp = fingerprint()
    for rec in PROTOTYPE_FINGERPRINTS:
        assert set(rec) == set(fp)
