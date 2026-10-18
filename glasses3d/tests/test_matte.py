"""The ported quadratic-background matte (WP3b; make_uv_test.py ``matte()``)."""
from __future__ import annotations

from glasses3d.tests.fixtures import camera_products
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from glasses3d.g3d_host import fit_views
from glasses3d.g3d_host import matte as M

RERUN = Path(__file__).resolve().parents[1] / "data" / "reference" / "projection" / "rerun"


def gradient_photo(H=240, W=360, seed=3):
    """A grey studio gradient (strong enough that one global threshold fails) with a dark ring-shaped frame, a filled
    temple bar, sensor noise and a few dust specks."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:H, 0:W]
    x, y = xx / W, yy / H
    bg = 70.0 + 150.0 * x - 40.0 * y + 60.0 * (y - 0.5) ** 2 + 25.0 * x * y
    img = np.repeat(bg[..., None], 3, axis=2) + rng.normal(0, 2.0, (H, W, 3))
    obj = np.zeros((H, W), bool)
    r = np.hypot((xx - 120) / 60.0, (yy - 120) / 45.0)
    obj |= (r <= 1.0) & (r >= 0.8)                                  # rim (hollow)
    obj[110:126, 180:330] = True                                    # temple
    img[obj] = (45.0, 35.0, 30.0)
    for (cy, cx) in ((30, 40), (200, 300), (60, 330)):
        img[cy:cy + 3, cx:cx + 3] = 20.0                            # 9 px specks
    want = obj | (r <= 1.0)                                         # the matte fills holes inside closed outlines
    return np.clip(img, 0, 255).astype(np.uint8), want, bg


def test_background_model_recovers_a_quadratic_gradient():
    img, _, bg = gradient_photo()
    clean = np.repeat(bg[..., None], 3, axis=2).round().astype(np.uint8)
    est = M.background_model(clean)
    assert np.abs(est[..., 0] - bg).max() < 1.0


def test_matte_on_a_grey_gradient_background():
    img, want, bg = gradient_photo()
    fg = M.matte(img)
    inter, union = (fg & want).sum(), (fg | want).sum()
    assert inter / union > 0.99
    assert not fg[:20, :].any() and not fg[:, -20:].any()          # gradient corners stay background
    assert not fg[30:33, 40:43].any()                               # specks dropped
    assert fg[120, 120]                                             # hole inside the closed rim filled
    naive = np.abs(img.astype(float) - np.median(img, axis=(0, 1))).max(2) > M.THRESHOLD
    assert naive[:20, -20:].all()                                   # one global background colour would fail here


def test_matte_parameters_and_grayscale_input():
    img, want, _ = gradient_photo()
    strict = M.matte(img, threshold=250)
    assert not strict.any()
    g = M.matte(img[..., 0])
    assert (g & want).sum() / (g | want).sum() > 0.99


@pytest.mark.skipif(not camera_products.manifest_path("ft1119").exists() or not (RERUN / "matte_reference-02.png").exists(),
                    reason="FT1119 reference data never imported, or the prototype's rerun mattes missing")
@pytest.mark.parametrize("name", ["reference-02.png", "reference-03.png", "reference-04.png"])
def test_matte_equals_the_prototype_matte_on_ft1119(name):
    files = camera_products.reference_files("ft1119")       # a missing/changed photo FAILS here (manifest exists)
    fg = M.matte(M.load_photo(files[name]))
    proto = np.asarray(Image.open(RERUN / f"matte_{name}")) > 127
    assert np.array_equal(fg, proto)
