"""WP6 matte mode "glossy_floor" (§6): default unchanged, the new mode only removes pixels (all 12 Prada / FT1119
photos), it opens the reflection-closed gap, and it equals the WP6 scratch matte3 it was ported from."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from scipy import ndimage

from glasses3d.g3d_host import matte as M
from glasses3d.tests.test_matte import gradient_photo

G3D = Path(__file__).resolve().parents[1]
REF = G3D / "data" / "reference"
SCRATCH_M3 = Path(r"C:/Users/Shay/AppData/Local/Temp/claude/g3d-wp6/matte")


def _photos():
    out = []
    for prod in ("prada", "ft1119"):
        man = REF / prod / "manifest.json"
        if not man.exists():
            continue
        for name in json.loads(man.read_text(encoding="utf-8"))["files"]:
            if name != "model.glb":
                out.append((prod, name))
    return out


PHOTOS = _photos()


def test_default_mode_is_unchanged():
    img, want, _ = gradient_photo()
    a = M.matte(img)
    b = M.matte(img, mode="default")
    assert np.array_equal(a, b)
    with pytest.raises(ValueError):
        M.matte(img, mode="shiny")


def _glossy_scene(seed=4):
    """A white floor with a dark object (temples), a soft reflection band (20 levels) that closes a gap between them,
    and a thin contact sliver: the default fills the gap; glossy_floor keeps it open and drops the sliver."""
    rng = np.random.default_rng(seed)
    H, W = 240, 400
    img = np.full((H, W, 3), 235.0) + rng.normal(0, 1.5, (H, W, 3))
    obj = np.zeros((H, W), bool)
    obj[60:80, 40:360] = True                     # upper temple
    obj[150:170, 40:360] = True                   # lower temple
    obj[60:170, 40:60] = True                     # joined at the hinge end: the gap is a hole
    obj[60:170, 340:360] = True
    img[obj] = 120.0
    refl = np.zeros((H, W), bool)
    refl[80:150, 60:340] = False
    refl[105:120, 60:340] = True                  # glossy reflection band inside the gap, 20 levels off
    img[refl] -= 20.0
    img[164:170, 360:392] -= 160.0                # 6 px contact sliver running on from the lower temple's end
    return np.clip(img, 0, 255).astype(np.uint8), obj


def test_glossy_floor_opens_the_gap_and_drops_slivers():
    img, obj = _glossy_scene()
    d = M.matte(img)
    g = M.matte(img, mode="glossy_floor")
    assert np.all(g <= d)
    assert d[112, 200] and not g[112, 200]        # the reflection-filled gap is open again
    assert d[167, 380] and not g[167, 380]        # the contact sliver is gone
    inter = (g & obj).sum() / (g | obj).sum()
    assert inter > 0.97


@pytest.mark.skipif(not PHOTOS, reason="reference photos not imported")
@pytest.mark.parametrize("prod,name", PHOTOS)
def test_glossy_is_a_subset_of_default_on_every_reference_photo(prod, name):
    img = M.load_photo(REF / prod / name)
    d = M.matte(img)
    g = M.matte(img, mode="glossy_floor")
    assert not np.any(g & ~d), f"{prod}/{name}: glossy_floor added {int((g & ~d).sum())} px"
    assert g.any()


PRADA_DELTAS_K = {"reference-01.webp": -122, "reference-04.png": -3}      # WP6 measurement: 01 -122k ... 04 -3k


@pytest.mark.skipif(not (REF / "prada" / "manifest.json").exists(), reason="Prada reference data not imported")
@pytest.mark.parametrize("name,k", sorted(PRADA_DELTAS_K.items()))
def test_prada_deltas_match_the_measurement(name, k):
    img = M.load_photo(REF / "prada" / name)
    delta = int(M.matte(img, mode="glossy_floor").sum()) - int(M.matte(img).sum())
    assert delta == pytest.approx(k * 1000, abs=1500)


@pytest.mark.skipif(not SCRATCH_M3.exists(), reason="WP6 scratch mattes not available")
@pytest.mark.parametrize("key", ["01", "02", "03", "04", "05", "06"])
def test_port_equals_the_scratch_matte3(key):
    ref = SCRATCH_M3 / f"m3_reference-{key}.npy"
    if not ref.exists():
        pytest.skip(f"{ref} missing")
    name = next(n for p, n in PHOTOS if p == "prada" and n.startswith(f"reference-{key}"))
    img = M.load_photo(REF / "prada" / name)
    assert np.array_equal(M.matte(img, mode="glossy_floor"), np.load(ref))
