"""WP5 step 6 (2026-10-07, after the step-5 AR tool test): the brow honey patches, the -x temple's spots, the level's
chroma. Synthetic inputs only (the FT1119 numbers are in PORT_REVIEW section 10):
- ``labels.place_labels(..., seen_share=...)``: in crease mode a face its own anchor sees keeps the anchor; only its
  holes may go to an adjacent anchor that sees them;
- ``fill.light_acetate``: see-through text counts only where the painting photo's acetate is light;
- ``shading.level_map`` with ``sat``: Y kept, chroma scaled, sat 1 byte-identical to the correction round's map;
- the instrument's chroma / contrast (``print_metrics.level_compare`` 'appearance', ``band_energy``);
- fix round 1 of step 6: ``level_map``'s ``chroma_power`` (low chroma lifted, the saturated tail compressed: the
  factor is below 1 above the pivot; power 1 = the step-6 map); the appearance verdict's C* p90 / mean bounds (a median
  match with a 1.4x tail fails); the chroma stage (``ar_calibrate.chroma_stage`` / ``choose_chroma``) on a synthetic
  power-law renderer; the candidates' assembly (``ar_calibrate.finish``: the delivered candidate is the chroma stage's
  capture, the near-18 deg one the trade-off best at that chroma);
- fix round 2 of step 6: the chroma family's ``chroma_hold`` (never greys a colour) and ``chroma_hue`` (off-hue colours
  left alone) and the bake's A3 re-application (``printproof.reapply_level`` against the tool's
  ``shading.level_map``); the instrument's hue terms; ``ar_levels.by_group`` (brows and temples apart: a pooled PASS
  that hides a miss of both groups) and ``pass_brow``; the all-pairs chroma stage and its owner candidate;
- fix round 3 of step 6: a group the photo barely shows is reported, never graded (``ar_levels.compare``,
  ``MIN_PHOTO_GROUP_SHARE``, pinned on FT1119's own photo shares), and ``pass_brow`` / ``group_residuals`` skip it;
  the chroma probes captured at the same time; the cropped ``band_energy``; per-part px/mm (``capture_ppm`` with
  depth, ``_pooled_energy``); the cached photo masks; the re-grade summary.
The tests of the deleted proof scripts (``correction_proof.unlevel`` and its convergence report, the owner sheet's
rows, ``step6_proof``'s transfer, ``texture_chroma``, ``temple_region``, ``registration``, raster cache, sides and coat
share) were deleted on 2026-10-13 with them; see git tag review-20261013-pre-cleanup.
"""
import json
import math

import numpy as np
import pytest

from glasses3d.g3d_host import texture_utils as T
from glasses3d.g3d_host.photoprint import fill as FL
from glasses3d.g3d_host.photoprint import labels as LB
from glasses3d.g3d_host.photoprint import shading as SH
from glasses3d.tests.instruments import print_metrics as PM


def _strip_mesh(n=12):
    V = np.array([[float(i), y, 0.0] for i in range(n + 1) for y in (0.0, 1.0)])
    F = []
    for i in range(n):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        F += [[a, c, b], [b, c, d]]
    return V, np.asarray(F)


def _cols(n_faces, n_views):
    c = np.zeros((n_faces, n_views, 3))
    c[:] = [120.0, 80.0, 50.0]
    c[:, 1] = [60.0, 40.0, 30.0]                     # the two photos disagree everywhere ...
    return c


def _crease_case(seen_share):
    V, F = _strip_mesh()
    faces = np.arange(len(F))
    region = np.where(np.arange(len(F)) < 2 * 5, 0, 1)
    nf = np.full(len(F), 8.0)
    wf = np.ones((len(F), 2))
    cols = _cols(len(F), 2)
    cols[2 * 2: 2 * 4, 1] = cols[2 * 2: 2 * 4, 0]    # ... except on x in [2, 4], inside region 0
    return LB.place_labels(V, F, faces, region, {0: 0, 1: 1}, wf, nf, ["a", "b"], 4.0, band_mm=5.0, mode="crease",
                           face_colours=cols, seen_share=seen_share)


def test_crease_mode_photometric_seam_takes_seen_faces_without_seen_share():
    """The fix-round-2 behaviour (the step-5 bake): the seam goes where the photos agree, x in [2, 4], so region 0's
    faces x 4-5 (which its own anchor sees) are handed to view 1."""
    out = _crease_case(None)
    x = np.arange(len(out["labels"])) // 2
    assert np.any(out["labels"][(x >= 3) & (x < 5)] == 1), out["labels"]
    assert out["keep_seen"] is None


def test_crease_mode_anchor_keeps_what_it_sees():
    """Step 6: every face of both regions is fully seen by its own anchor -> no face changes label, whatever the
    photometric seam prefers; the decision is reported."""
    n = 24
    out = _crease_case(np.ones((n, 2)))
    assert out["moved_faces"] == 0 and np.array_equal(out["labels"], out["start"])
    assert out["keep_seen"]["share"] == LB.KEEP_SEEN_SHARE and out["keep_seen"]["band_faces_kept"] > 0


def test_crease_mode_holes_of_the_anchor_may_go_to_an_adjacent_anchor():
    """Step 6: faces the start anchor does not see (share < KEEP_SEEN_SHARE) may still go to the neighbour that sees
    them (photo instead of fill); faces it sees stay."""
    n = 24
    ss = np.ones((n, 2))
    x = np.arange(n) // 2
    blind = (x >= 3) & (x < 5)                       # region 0's anchor cannot use x in [3, 5) ...
    ss[blind, 0] = 0.2
    out = _crease_case(ss)
    lab = out["labels"]
    assert not np.any(lab[(x < 3)] == 1), lab        # ... faces it sees are never handed over
    assert np.all(lab[x >= 5] == 1)
    assert np.any(lab[blind] == 1), lab              # its holes may be painted by the adjacent anchor


class _Tex:
    """A 1 x n atlas strip with regions."""

    def __init__(self, region):
        n = len(region)
        self.shape = (1, n)
        self.ii = np.zeros(n, np.int64)
        self.jj = np.arange(n)
        self.region = np.asarray(region)

    def image(self, v, fill=0, dtype=None):
        v = np.asarray(v)
        out = np.full((1, len(self.jj)) + v.shape[1:], fill, dtype=dtype or v.dtype)
        out[0] = v
        return out


def test_light_acetate_marks_only_the_honey_phase():
    """Dark acetate (L* ~ 16) with one honey patch (L* ~ 45): only the patch (smoothed) is >= 6 L* over the region's
    median; a texel not painted by the photo is never marked."""
    n = 200
    col = np.tile([[40.0, 32.0, 28.0]], (n, 1))      # dark phase
    col[120:150] = [170.0, 120.0, 70.0]              # a honey patch
    painted = np.ones(n, bool)
    painted[10] = False
    tex = _Tex(np.zeros(n, np.int64))
    lit = FL.light_acetate(tex, painted, col, tx=4.0)
    assert lit[125:145].all() and not lit[:100].any() and not lit[10]
    assert not FL.light_acetate(tex, np.zeros(n, bool), col, tx=4.0).any()


def test_level_map_sat_keeps_luminance_and_scales_chroma():
    rgb = np.array([[60.0, 40.0, 30.0], [120.0, 85.0, 60.0], [200.0, 150.0, 90.0], [90.0, 90.0, 90.0]])
    base = {"gain": 0.9, "gamma": 1.1}
    assert np.array_equal(SH.level_map(rgb, base), SH.level_map(rgb, base | {"sat": 1.0}))
    out = SH.level_map(rgb, base | {"sat": 1.4})
    ref = SH.level_map(rgb, base)
    assert np.allclose(PM.lstar(out), PM.lstar(ref), atol=0.05)          # luminance kept
    c_out = np.hypot(*T.lab_of(out)[:, 1:].T)
    c_ref = np.hypot(*T.lab_of(ref)[:, 1:].T)
    assert (c_out[:3] > c_ref[:3] * 1.25).all() and abs(c_out[3] - c_ref[3]) < 0.05   # grey stays grey
    img = np.full((3, 3, 3), 100, np.uint8)
    img[1, 1] = [150, 90, 40]
    m = np.zeros((3, 3), bool)
    m[1, 1] = True
    assert np.array_equal(SH.apply_level_u8(img, m, base | {"sat": 1.0}), SH.apply_level_u8(img, m, base))


def test_instrument_appearance_chroma_and_contrast():
    rng = np.random.default_rng(3)
    L = rng.uniform(15, 45, 3000)
    base = np.stack([L * 3.0, L * 2.2, L * 1.6], 1)
    photo = np.clip(base, 0, 255)
    grey = np.clip(np.repeat(L[:, None] * 2.3, 3, 1), 0, 255)
    r = PM.level_compare(grey, photo)
    ap = r["appearance"]
    assert ap["chroma_ar_minus_photo"]["p50"] < -2 and not ap["pass"]
    same = PM.level_compare(photo, photo)["appearance"]
    assert same["chroma_ar_minus_photo"]["p50"] == 0 and same["lstar_std_ratio"] == 1.0 and same["pass"]
    flat = np.clip(np.stack([np.full(3000, 90.0), np.full(3000, 66.0), np.full(3000, 48.0)], 1), 0, 255)
    assert PM.level_compare(flat, photo)["appearance"]["lstar_std_ratio"] < 0.1


def test_instrument_band_energy_follows_patch_contrast():
    yy, xx = np.mgrid[0:120, 0:200]
    patt = np.sin(xx / 6.0) * np.cos(yy / 7.0)
    m = np.ones((120, 200), bool)
    lo = np.clip(np.stack([60 + 10 * patt] * 3, -1), 0, 255)
    hi = np.clip(np.stack([60 + 30 * patt] * 3, -1), 0, 255)
    e_lo, e_hi = PM.band_energy(lo, m, 4.8), PM.band_energy(hi, m, 4.8)
    assert e_hi > 2.5 * e_lo > 0
    assert PM.band_energy(np.full((120, 200, 3), 80.0), m, 4.8) == pytest.approx(0.0, abs=1e-9)
    assert PM.band_energy(lo, np.zeros_like(m), 4.8) is None


def test_appearance_residuals_put_contrast_on_the_l_star_scale():
    """Step 6: a std ratio at the edge of the instrument's contrast window counts like a 2 L* residual; equal spread
    counts 0; the targets name the appearance role for the delivered level."""
    from glasses3d.tests.photoprint import ar_calibrate as AC
    r = AC.appearance_residuals([1.0, -0.5, 0.2, 1.5], [1.18, 1.0])
    assert r[:4] == [1.0, -0.5, 0.2, 1.5] and r[4] == pytest.approx(2.0, abs=1e-3) and r[5] == 0.0
    assert AC.appearance_residuals([0.0], [None])[1] == 99.0
    assert AC.TARGETS["photo_angles"] == "appearance" and AC.ROLE_PAIRS["appearance"] == "calibration"


class _Tex2D:
    """A full H x W atlas of texels (row-major), regions all 0."""

    def __init__(self, H, W):
        self.shape = (H, W)
        self.ii, self.jj = [a.ravel() for a in np.mgrid[0:H, 0:W]]
        self.region = np.zeros(H * W, np.int64)

    def image(self, v, fill=0, dtype=None):
        v = np.asarray(v)
        out = np.full(self.shape + v.shape[1:], fill, dtype=dtype or v.dtype)
        out[self.ii, self.jj] = v
        return out


def test_thin_light_lines_flag_glint_remnants_not_honey_or_amber():
    """Step 6: on dark acetate, a 2-texel light grey line is flagged; a wide honey patch and a thin AMBER fleck (red
    rises, blue does not) are not; another view's texels are judged against their own view only."""
    from glasses3d.g3d_host.photoprint import highlights as HL
    H, W = 60, 120
    tex = _Tex2D(H, W)
    img = np.zeros((H, W, 3))
    img[:] = [45.0, 34.0, 28.0]                       # dark phase
    img[20:22, 10:60] = [80.0, 72.0, 66.0]            # thin light achromatic line (a glint remnant)
    img[30:55, 70:110] = [170.0, 120.0, 70.0]         # a wide honey patch
    img[10:12, 70:110] = [95.0, 45.0, 28.0]           # a thin amber fleck
    col = img.reshape(-1, 3)
    painted = np.ones(H * W, bool)
    label = np.zeros(H * W, np.int64)
    m = HL.thin_light_lines(tex, painted, col, label, tx=16.0).reshape(H, W)
    assert m[20:22, 15:55].mean() > 0.9
    assert not m[32:53, 72:108].any() and not m[10:12, 72:108].any()
    label2 = label.copy().reshape(H, W)
    label2[:, :60] = 1                                # the line's half painted by another view: still its own line
    m2 = HL.thin_light_lines(tex, painted, col, label2.ravel(), tx=16.0).reshape(H, W)
    assert m2[20:22, 15:55].mean() > 0.9
    r = HL.flag(tex, painted, col, label, {}, tx=16.0)
    assert r["thin_light_line"] > 0 and r["mask"].reshape(H, W)[20:22, 15:55].all()
    assert HL.flag(tex, painted, col, label, {}, tx=16.0, thin=False)["thin_light_line"] == 0


# ----------------------------------------------------------------------------- fix round 1 of step 6
def _chroma(rgb):
    lab = T.lab_of(np.asarray(rgb, float))
    return np.hypot(lab[..., 1], lab[..., 2])


def test_level_map_chroma_power_lifts_low_chroma_and_compresses_the_tail():
    """A power below 1 lifts the dark, low-chroma havana phase and COMPRESSES the saturated honey tail (the factor is
    below 1 above the pivot: fix round 2's review), Y kept; power 1 is the step-6 map byte for byte; the factor follows
    the documented formula."""
    rgb = np.array([[60.0, 48.0, 40.0], [70.0, 52.0, 40.0], [200.0, 120.0, 50.0], [190.0, 100.0, 30.0],
                    [90.0, 90.0, 90.0]])
    base = {"gain": 0.9, "gamma": 1.1}
    assert np.array_equal(SH.level_map(rgb, base | {"sat": 1.3}),
                          SH.level_map(rgb, base | {"sat": 1.3, "chroma_power": 1.0}))
    ref = SH.level_map(rgb, base)
    lv = base | {"sat": 1.1, "chroma_power": 0.75}
    out = SH.level_map(rgb, lv)
    assert np.allclose(PM.lstar(out), PM.lstar(ref), atol=0.05)
    gain = _chroma(out[:4]) / _chroma(ref[:4])
    order = np.argsort(_chroma(rgb[:4]))
    assert (np.diff(gain[order]) < 0).all(), gain                              # the higher the chroma, the less lift
    assert gain[0] > 1.15 and (gain[2:] < 0.85).all(), gain
    assert abs(_chroma(out[4]) - _chroma(ref[4])) < 0.05                      # grey stays grey
    C = _chroma(rgb)
    assert np.allclose(SH.chroma_factor(rgb, lv), np.minimum(1.1 * (np.maximum(C, 1.0) / 10.0) ** -0.25, 4.0))
    assert SH.chroma_factor(rgb, {"sat": 1.3}) == 1.3


def test_appearance_verdict_bounds_the_saturated_tail():
    """Step 6's verdict passed on the C* median alone; a print whose honey tail is 1.4x the photo's with the same
    median now fails on p90 (and the units say which term)."""
    rng = np.random.default_rng(5)
    n = 4000
    L = rng.uniform(15, 40, n)
    photo = np.clip(np.stack([L * 2.9, L * 2.3, L * 1.9], 1), 0, 255)
    honey = rng.random(n) < 0.2
    photo[honey] = np.clip(np.stack([L[honey] * 4.2, L[honey] * 2.6, L[honey] * 1.2], 1), 0, 255)
    lin = T.srgb_to_linear(photo)
    Y = lin @ np.array([0.2126, 0.7152, 0.0722])
    ar_lin = lin.copy()
    ar_lin[honey] = np.maximum(Y[honey, None] + 1.4 * (lin[honey] - Y[honey, None]), 0.0)
    ar = T.linear_to_srgb(ar_lin)
    same = PM.level_compare(photo, photo)["appearance"]
    assert same["pass"] and same["chroma_units"] == [0.0, 0.0, 0.0]
    ap = PM.level_compare(ar, photo)["appearance"]
    assert abs(ap["chroma_ar_minus_photo"]["p50"]) < 0.3 and ap["criteria"]["chroma_p50"]
    assert ap["chroma_ar_over_photo"]["p90"] > 1.25 and not ap["criteria"]["chroma_p90"] and not ap["pass"]
    assert ap["chroma_units"][1] > 1.0
    edge = math.log(PM.CHROMA_SPREAD_RATIO[1])
    assert ap["chroma_units"][1] == pytest.approx(math.log(ap["chroma_ar_over_photo"]["p90"]) / edge, abs=2e-3)


PHOTO_C = [[5.83, 18.54, 8.49], [4.44, 15.60, 7.00]]     # photo 05 / 01 at their own angles (step 6's numbers)
BASE_C = [[4.35, 19.64, 7.57], [3.39, 16.50, 6.21]]      # the AR at sat 1, power 1 (photo_angles_s5)
PHOTO_OFF = {"trade-off": [[5.74, 18.29, 8.36], [4.41, 15.50, 6.96]], "held-out": [[6.04, 19.27, 9.03]]}
BASE_OFF = {"trade-off": [[3.56, 14.51, 5.96], [3.19, 14.30, 5.40]], "held-out": [[3.39, 14.50, 5.92]]}  # same capture


def _synthetic_capture(tag, level, residuals):
    """A capture whose AR chroma follows a power-law renderer: each statistic q -> q x sat x (q / 10)^(power - 1),
    with a mild curvature in ln sat (the base numbers are step 6's at the delivered (gain, gamma))."""
    from glasses3d.tests.photoprint import ar_calibrate as AC
    s, p = AC.chroma_level(level)
    rnd = lambda base: [[q * s * (q / 10.0) ** (p - 1.0) * math.exp(0.05 * math.log(s) ** 2) for q in b] for b in base]
    ar = {"calibration": rnd(BASE_C)} | {r: rnd(b) for r, b in BASE_OFF.items()}
    ph = {"calibration": PHOTO_C} | PHOTO_OFF
    units = {r: AC.units_of(np.array(ar[r]), np.array(ph[r])).ravel() for r in ar}
    return {"tag": tag, "level": level, "capture": f"cap_{tag}", "glb": f"{tag}.glb",
            "pairs": {"calibration": [["pitch_p20", "05"], ["yaw_m30_pitch_p20", "01"]],
                      "trade-off": [["front", "05"], ["yaw_m30", "01"]], "held-out": [["yaw_p45", "03"]]},
            "residuals": residuals, "residuals_by_form": {}, "view_dependence": {}, "pass_by_role": {},
            "pass": True, "contrast": {"calibration": [1.14, 0.98]},
            "chroma": {r: [round(a[0] - b[0], 2) for a, b in zip(ar[r], ph[r])] for r in ar},
            "chroma_units": {r: [round(float(u), 3) for u in units[r]] for r in ar},
            "chroma_stats": {r: [[a, b] for a, b in zip(ar[r], ph[r])] for r in ar}}


def test_chroma_stage_matches_median_tail_and_mean_with_a_captured_point():
    """On the power-law renderer the median needs sat ~1.34 and the tail ~0.94: one factor sits at the windows' edges
    at best; the stage reaches every chroma unit within CHROMA_STOP with a power below 1, and the choice is one of its
    captures."""
    from glasses3d.tests.photoprint import ar_calibrate as AC
    res = {"calibration": [-0.97, -1.22, 1.62, -1.62], "trade-off": [1.5, 4.9], "held-out": [5.0],
           "appearance": [-0.97, -1.22, 1.62, -1.62, 1.6, -0.27]}
    caps = []

    def g(sv, pv, tag):
        e = _synthetic_capture(tag, AC.with_chroma(0.93, 1.08, sv, pv), res)
        caps.append(e)
        return e

    base = _synthetic_capture("base", AC.with_chroma(0.93, 1.08), res)
    assert AC.chroma_units_worst(base) > 1.0                    # the median is low at (1, 1)
    sat_only = min(max(abs(u) for u in _synthetic_capture("x", AC.with_chroma(0.93, 1.08, s), res)
                       ["chroma_units"]["calibration"]) for s in np.linspace(0.8, 2.2, 281))
    assert sat_only > 0.9          # a single factor sits at the windows' edges (p50 -1.0, p90 x 1.18 at sat ~1.11)
    st = AC.chroma_stage(base, g, log=lambda m: None)
    chosen = st["chosen_e"]
    assert chosen in caps and st["chosen"] == chosen["tag"]
    assert AC.chroma_units_worst(chosen) <= AC.CHROMA_STOP, st
    s, p = AC.chroma_level(chosen["level"])
    assert p < 0.95 and chosen["level"]["chroma_power"] == p and "sat" in chosen["level"]
    assert st["probes"] == ["chroma1", "chroma2", "chroma3"] and len(caps) <= len(AC.CHROMA_PROBES) + AC.CHROMA_MAX_ITER


def test_finish_delivers_the_chroma_capture_and_recaptures_the_near_candidate():
    """The candidates come from the captured iterations by NAME: 'photo_angles' is the chroma stage's capture at the
    best appearance (gain, gamma), delivered; 'near_18deg' the trade-off best re-captured at the chosen chroma, not
    delivered (step 6's first run filed the near capture as delivered)."""
    from glasses3d.tests.photoprint import ar_calibrate as AC
    A = {"calibration": [-0.9, -1.2, 1.6, -1.6], "trade-off": [1.6, 4.9], "held-out": [5.2],
         "appearance": [-0.9, -1.2, 1.6, -1.6, 1.6, -0.3]}
    B = {"calibration": [-2.5, -3.0, -0.4, -4.0], "trade-off": [0.9, 1.6], "held-out": [4.6],
         "appearance": [-2.5, -3.0, -0.4, -4.0, 1.1, -0.6]}
    by_level = {(0.93, 1.08): A, (0.83, 1.17): B}
    calls = []

    def f(gain, gamma, tag, sat=1.0, power=1.0):
        calls.append((tag, gain, gamma, sat, power))
        return _synthetic_capture(tag, AC.with_chroma(gain, gamma, sat, power), by_level[(gain, gamma)])

    base_its = [f(0.93, 1.08, "photo_angles_s5"), f(0.83, 1.17, "near_18deg_s5")]
    roles = base_its[0]["pairs"]
    fin = AC.finish(base_its, f, roles, log=lambda m: None)
    c = fin["candidates"]
    assert set(c) == set(AC.TARGETS) | {"all_pairs_chroma"}
    assert c["photo_angles"]["tag"] == fin["chroma_stage"]["chosen"] == fin["chosen"]["tag"] != "near_chroma"
    assert c["photo_angles"]["delivered"] and not c["near_18deg"]["delivered"]
    assert c["photo_angles"]["level"]["gain"] == 0.93 and "chroma_power" in c["photo_angles"]["level"]
    assert c["near_18deg"]["tag"] == "near_chroma" and calls[-1][0] == "near_chroma"
    assert c["near_18deg"]["level"] == AC.with_chroma(0.83, 1.17, *AC.chroma_level(c["photo_angles"]["level"]))
    assert c["photo_angles"]["role"] == "appearance" and c["photo_angles"]["over"] == roles["calibration"]
    assert c["near_18deg"]["over"] == roles["trade-off"]
    assert "chroma power" in c["photo_angles"]["label"]
    # the assembly alone, on swapped inputs: delivered follows the NAME, never the capture's tag or role
    sw = AC.assemble_candidates(fin["near"], fin["chosen"], roles)
    assert sw["photo_angles"]["tag"] == "near_chroma" and sw["photo_angles"]["delivered"]


def test_tooltest_label_names_the_attached_print(tmp_path):
    """Fix round 1 of step 6: the label is built from the bake (print sha256, bake directory, level), never a typed
    step number."""
    from glasses3d.tests.photoprint import print_tooltest as TT
    bake = tmp_path / "ft1119_bake"
    bake.mkdir()
    (bake / "report.json").write_text(json.dumps({"level": {"applied": True, "gain": 0.93, "gamma": 1.08, "sat": 1.12,
                                                            "chroma_power": 0.78}}), encoding="utf-8")
    lab = TT.make_label({"print_sha256": "8535019e" + "0" * 56}, bake)
    assert lab.startswith("TOOL TEST") and "8535019e00000000" in lab and "ft1119_bake" in lab
    assert "chroma_power 0.780" in lab and "step-4" not in lab and "Not a delivered model" in lab
    (bake / "report.json").write_text(json.dumps({"level": {"applied": False}}), encoding="utf-8")
    assert "not calibrated" in TT.make_label({"print_sha256": "ab" * 32}, bake)


def test_coat_off_probe_strips_only_the_print_parts_clearcoat(tmp_path):
    """The material note's attribution probe: the black probe without KHR_materials_clearcoat on the print parts'
    materials; every other material, the meshes and the binary chunk's other bytes unchanged."""
    from pathlib import Path
    from glasses3d.tests.photoprint import ar_calibrate as AC
    src = Path(AC.G3D) / "data/wp5/pre_correction_tooltest/q001_painted_TOOLTEST.glb"
    if not src.is_file():
        pytest.skip("the tool-test GLB is local-only")
    coated, bare = tmp_path / "probe_TOOLTEST.glb", tmp_path / "probe_nocoat_TOOLTEST.glb"
    AC.black_probe(src, coated)
    info = AC.black_probe(src, bare, clearcoat=False)
    d0, b0 = AC._chunks(coated.read_bytes())
    d1, b1 = AC._chunks(bare.read_bytes())
    assert sorted(info["clearcoat_removed_from"]) == sorted({m["name"] for m in d0["materials"]
                                                             if "KHR_materials_clearcoat" in (m.get("extensions") or {})})
    assert not any("KHR_materials_clearcoat" in (m.get("extensions") or {}) for m in d1["materials"])
    assert b0 == b1 and d0["meshes"] == d1["meshes"]
    for m0, m1 in zip(d0["materials"], d1["materials"]):
        e0 = dict(m0.get("extensions") or {})
        e0.pop("KHR_materials_clearcoat", None)
        assert e0 == (m1.get("extensions") or {}) and m0.get("pbrMetallicRoughness") == m1.get("pbrMetallicRoughness")


# ----------------------------------------------------------------------------- fix round 2 of step 6
HOLD_HUE = {"chroma_hold": True, "chroma_hue": [20.0, 100.0]}


def _lab_colour(L, C, h_deg):
    """An sRGB colour of the given CIELAB L*, C*ab and hue (degrees)."""
    from skimage.color import lab2rgb
    a, b = C * math.cos(math.radians(h_deg)), C * math.sin(math.radians(h_deg))
    return np.clip(lab2rgb(np.array([[[L, a, b]]]))[0, 0] * 255.0, 0, 255)


def test_chroma_hold_never_greys_and_the_hue_band_leaves_off_hue_colours_alone():
    """Fix round 2: ``chroma_hold`` holds the factor at >= 1 (fix round 1's power greyed the tail: 0.88 at C* 25, 0.78
    at 40); ``chroma_hue`` lifts only inside 20-100 deg, half-way at 7.5 deg outside, none beyond 15 (a magenta
    colour keeps its chroma); without them the map is fix round 1's byte for byte."""
    lv = {"gain": 0.93, "gamma": 1.08, "sat": 1.107, "chroma_power": 0.745}
    havana = np.array([_lab_colour(25, c, 50) for c in (2.0, 6.0, 25.0, 40.0)])
    f1 = SH.chroma_factor(havana, lv)
    assert f1[2] == pytest.approx(0.876, abs=0.01) and f1[3] == pytest.approx(0.777, abs=0.01)   # the review's values
    fh = SH.chroma_factor(havana, lv | {"chroma_hold": True})
    assert (fh >= 1.0).all() and np.allclose(fh[:2], f1[:2]) and np.allclose(fh[2:], 1.0)
    for hue, want in ((330.0, 0.0), (12.5, 0.5), (60.0, 1.0), (107.5, 0.5), (200.0, 0.0)):
        c = _lab_colour(25, 3.0, hue)[None]
        g = SH.chroma_factor(c, lv | {"chroma_hold": True})
        f = SH.chroma_factor(c, lv | HOLD_HUE)
        lab = T.lab_of(c)
        assert SH.hue_weight(lab)[0] == pytest.approx(want, abs=0.03), hue
        assert f[0] == pytest.approx(1.0 + want * (g[0] - 1.0), abs=0.03 * (g[0] - 1.0) + 1e-9), hue
    rgb = np.array([[60.0, 48.0, 40.0], [190.0, 100.0, 30.0], [90.0, 90.0, 90.0]])
    assert np.array_equal(SH.level_map(rgb, lv), SH.level_map(rgb, lv | {"chroma_hold": False}))
    out, ref = SH.level_map(havana, lv | HOLD_HUE), SH.level_map(havana, {"gain": 0.93, "gamma": 1.08})
    assert np.allclose(PM.lstar(out), PM.lstar(ref), atol=0.05)                   # Y kept
    assert (_chroma(out) >= _chroma(ref) - 0.05).all()                           # never greyer than the L* map alone
    mag = _lab_colour(20, 3.0, 330.0)[None]
    assert abs(_chroma(SH.level_map(mag, lv | HOLD_HUE)) - _chroma(SH.level_map(mag, {"gain": 0.93, "gamma": 1.08}))) < 0.05


def test_a3_reapply_the_hold_and_hue_band():
    """The proofs' chroma factor (``printproof._chroma_f``, moved unchanged from the deleted correction_proof.py)
    equals the tool's, and ``printproof.reapply_level`` (A3's re-application, on the ROUNDED colour, fix round 3: no
    longer an inline copy) reproduces the tool's ``level_map`` for every level form A3 meets."""
    from glasses3d.tests.photoprint import printproof as PP
    rng = np.random.default_rng(11)
    rgb = rng.uniform(15, 230, (3000, 3))
    rgb[:1000, 2] = np.minimum(rgb[:1000, 2], rgb[:1000, 0])
    lv = {"applied": True, "gain": 0.93, "gamma": 1.08, "sat": 1.05, "chroma_power": 0.6} | HOLD_HUE
    f = PP._chroma_f(rgb, 1.05, 0.6, True, [20.0, 100.0])
    assert np.allclose(f, SH.chroma_factor(rgb, lv), atol=1e-9)
    rgb8 = np.rint(rgb)
    for level in ({"gain": 0.78, "gamma": 1.16},                                       # the correction round
                  {"gain": 0.93, "gamma": 1.08, "sat": 1.322},                         # step 6
                  {"gain": 0.93, "gamma": 1.08, "sat": 1.107, "chroma_power": 0.745},  # fix round 1
                  lv,                                                                  # fix round 2 (delivered form)
                  {"gain": 0.931, "gamma": 1.080, "sat": 1.0, "chroma_hold": True, "chroma_hue": [20.0, 100.0]}):
        got = PP.reapply_level(rgb8 + rng.uniform(-0.4, 0.4, rgb.shape), level)     # rounds first, as A3 does
        assert np.allclose(got, SH.level_map(rgb8, level), atol=1e-9, rtol=0), level
    # the branch that matters: a hold / band level differs from the plain power, and A3 follows the tool there
    plain = {k: v for k, v in lv.items() if k not in HOLD_HUE}
    assert np.abs(PP.reapply_level(rgb8, lv) - PP.reapply_level(rgb8, plain)).max() > 1.0


def test_instrument_hue_terms_catch_magenta_noise_and_a_hue_shift():
    """Fix round 2: the appearance block grades the hue median (within 5 deg) and the magenta share (at most 2 points
    over the photo's); a lift that adds magenta-hued dark pixels fails, so does a 9-deg hue rotation."""
    rng = np.random.default_rng(4)
    n = 4000
    photo = np.array([_lab_colour(L, C, h) for L, C, h in zip(rng.uniform(15, 40, n), rng.uniform(3, 14, n),
                                                              rng.normal(45, 10, n))])
    same = PM.level_compare(photo, photo)["appearance"]
    assert same["pass"] and same["hue"]["p50_ar_minus_photo"] == 0.0
    noisy = photo.copy()
    k = rng.random(n) < 0.06
    noisy[k] = [_lab_colour(18, 5.0, 330.0)] * int(k.sum())
    ap = PM.level_compare(noisy, photo)["appearance"]
    assert ap["hue"]["magenta_share_ar_minus_photo"] > PM.MAGENTA_EXCESS and not ap["criteria"]["hue_magenta"]
    turned = np.array([_lab_colour(L, C, h - 9.0) for L, C, h in zip(rng.uniform(15, 40, n), rng.uniform(3, 14, n),
                                                                     rng.normal(45, 10, n))])
    at = PM.level_compare(turned, photo)["appearance"]
    assert at["hue"]["p50_ar_minus_photo"] < -PM.HUE_TOL_DEG and not at["criteria"]["hue_p50"] and not at["pass"]


def _px(L, C=6.0, h=45.0):
    return np.array([_lab_colour(l, C, h) for l in np.atleast_1d(L)])


def test_by_group_grades_brows_and_temples_apart():
    """Fix round 2 (made true in fix round 3: the old data's pooled verdict FAILED, p10 -2.39): the brows 2.8 L* dark
    and the temples 2.8 L* light, over the same photo distribution, pool into a PASS (|p10| ~0.8, |p50| ~0), while each
    group alone fails; ``by_group`` shows both misses and ``pass_brow`` fails the role the pooled form passes. A role
    with no row is None; a row whose brows pass makes it pass."""
    from glasses3d.tests.photoprint import ar_levels as AL
    rng = np.random.default_rng(9)
    n = 3000
    Lb, Lt = rng.uniform(15, 35, n), rng.uniform(15, 35, n)
    pp = np.concatenate([_px(Lb), _px(Lt)])
    gp = np.r_[np.zeros(n, int), np.ones(n, int)]
    pa = np.concatenate([_px(Lb - 2.8), _px(Lt + 2.8)])
    no_hl = np.zeros(2 * n, bool)
    r = AL.compare((pa, gp, no_hl), (pp, gp))
    pooled = r["print"]
    assert pooled["pass"], pooled["ar_minus_photo"]                                   # the pooled form passes ...
    assert abs(pooled["ar_minus_photo"]["p10"]) < 1.5 and abs(pooled["ar_minus_photo"]["p50"]) < 0.5
    g = r["by_group"]
    assert g["brow"]["graded"] and g["temple"]["graded"] and not r["groups_excluded"]
    assert g["brow"]["ar_minus_photo"]["p50"] == pytest.approx(-2.8, abs=0.4) and not g["brow"]["pass"]   # ... and
    assert g["temple"]["ar_minus_photo"]["p50"] == pytest.approx(2.8, abs=0.4) and not g["temple"]["pass"]  # hides two
    rows = {"pitch_p20_vs_05": {**r, "role": "calibration"}}
    rows["pass"] = {"calibration": {"print": pooled["pass"]}}
    pb = AL.pass_brow(rows)
    assert pb["calibration"] is False and pb["trade-off"] is None and pb["held-out"] is None
    good = AL.compare((pp, gp, no_hl), (pp, gp))
    assert AL.pass_brow({"front_vs_05": {**good, "role": "trade-off"}})["trade-off"] is True
    assert AL.pass_brow({"front_vs_05": {"role": "trade-off", "missing": "no front render"}})["trade-off"] is False
    few = AL.by_group(pa[:n + 10], gp[:n + 10], None, pp, gp)
    assert few["temple"]["pass"] is None and few["temple"]["graded"] is False and "AR" in few["temple"]["reason"]


def test_a_group_the_photo_barely_shows_is_reported_never_graded():
    """Fix round 3: the photo shows the brows at 5 % of its print-form pixels (a side view), the AR pose at 50 %; the
    brows' chroma misses badly (AR C* 6 against the photo's 14). Fix round 2's instrument weighted those few photo
    pixels x10 and failed the pooled row on them; now the brows are left out of both weighted forms (the pooled row
    is the temples alone, which match), still reported in ``by_group`` with ``graded`` False, and skipped by
    ``pass_brow`` and ``group_residuals`` / ``group_chroma_units``."""
    from glasses3d.tests.photoprint import ar_levels as AL
    rng = np.random.default_rng(12)
    nb_p, nt_p, nb_a, nt_a = 150, 2850, 1500, 1500
    Lt_p, Lt_a = rng.uniform(15, 35, nt_p), rng.uniform(15, 35, nt_a)
    pp = np.concatenate([_px(rng.uniform(15, 35, nb_p), C=14.0), _px(Lt_p)])
    gp = np.r_[np.zeros(nb_p, int), np.ones(nt_p, int)]
    pa = np.concatenate([_px(rng.uniform(15, 35, nb_a), C=6.0), _px(Lt_a)])
    ga = np.r_[np.zeros(nb_a, int), np.ones(nt_a, int)]
    no_hl = np.zeros(len(pa), bool)
    r = AL.compare((pa, ga, no_hl), (pp, gp))
    assert set(r["groups_excluded"]) == {"brow"} and r["photo_group_share"]["brow"] == pytest.approx(0.05, abs=1e-3)
    assert "MIN_PHOTO_GROUP_SHARE" in r["groups_excluded"]["brow"]
    assert r["photo_weight"]["brow"] == pytest.approx(10.0, rel=0.02)
    assert r["print"]["ar_group_share"] == {"1": 1.0} and r["matched"]["ar_group_share"] == {"1": 1.0}
    assert r["print"]["appearance"]["pass"] and r["print"]["pass"]                  # the temples alone match
    old = AL.compare((pa, ga, no_hl), (pp, gp), min_photo_share=0.0)                  # fix round 2's instrument
    assert not old["groups_excluded"] and not old["print"]["appearance"]["pass"]
    assert old["print"]["appearance"]["chroma_units"][1] < -1.0                         # failed on the brows' x10
    b = r["by_group"]["brow"]
    assert b["graded"] is False and "excluded" in b and not b["appearance"]["pass"]     # reported, not graded
    assert r["by_group"]["temple"]["graded"] is True
    two = {"yaw_p45_vs_03": {**r, "role": "held-out"},
           "yaw_m45_vs_01": {**AL.compare((pa, ga, no_hl), (pa, ga)), "role": "held-out"}}
    assert AL.pass_brow(two)["held-out"] is True                     # decided by the row that grades the brows
    assert AL.pass_brow({"yaw_p45_vs_03": {**r, "role": "held-out"}})["held-out"] is None
    assert AL.group_residuals(two, "brow", [("yaw_p45", "03"), ("yaw_m45", "01")])[:2] == [None, None]
    assert AL.group_residuals(two, "brow", [("yaw_m45", "01")]) == [0.0, 0.0]
    assert AL.group_chroma_units(two, "brow", [("yaw_p45", "03")]) == [None] * 3
    assert AL.group_residuals(two, "temple", [("yaw_p45", "03")])[0] is not None


def test_min_photo_group_share_separates_the_side_view_brows(ft1119_job):
    """Fix round 3: the minimum is set from FT1119's own photo-side shares (constants of the job): it leaves out exactly
    the two side views' brows (03: 0.090, 06: 0.117) and keeps photo 05's temple tops (0.174) and every other group
    (>= 0.48)."""
    from glasses3d.tests.photoprint import ar_levels as AL
    t = AL.group_share_table(ft1119_job)
    excl = {(p, g) for p, rows in t["photos"].items() for g, r in rows.items() if r["excluded"]}
    assert excl == {("03", "brow"), ("06", "brow")}
    sh = {(p, g): r["share"] for p, rows in t["photos"].items() for g, r in rows.items()}
    assert sh[("03", "brow")] == pytest.approx(0.090, abs=0.002)
    assert sh[("06", "brow")] == pytest.approx(0.117, abs=0.002)
    assert sh[("05", "temple")] == pytest.approx(0.174, abs=0.002)
    assert min(v for k, v in sh.items() if k not in excl | {("05", "temple")}) > 0.45
    assert max(sh[k] for k in excl) < AL.MIN_PHOTO_GROUP_SHARE < sh[("05", "temple")]


def test_chroma_stage_reports_every_graded_pair_and_finds_an_all_pairs_level():
    """Fix round 2: on the synthetic renderer (the off-angle tails short as in fix round 1's captures) the
    photo-angle stage meets CHROMA_STOP on its pairs while its worst over every graded pair is larger; the all-pairs
    stage, from the same captures, lowers that worst; both worsts are reported for every capture."""
    from glasses3d.tests.photoprint import ar_calibrate as AC
    res = {"calibration": [-0.97, -1.22, 1.62, -1.62], "trade-off": [1.5, 4.9], "held-out": [5.0],
           "appearance": [-0.97, -1.22, 1.62, -1.62, 1.6, -0.27]}
    caps = []

    def g(sv, pv, tag):
        e = _synthetic_capture(tag, AC.with_chroma(0.93, 1.08, sv, pv), res)
        caps.append(e)
        return e

    base = _synthetic_capture("base", AC.with_chroma(0.93, 1.08), res)
    st = AC.chroma_stage(base, g, log=lambda m: None)
    ch = st["chosen_e"]
    assert set(st["worst_units_calibration"]) == set(st["worst_units_all_graded"]) == set(st["captures"])
    assert AC.chroma_units_worst(ch) <= AC.CHROMA_STOP < AC.chroma_units_worst(ch, AC.ALL_GRADED)
    al = AC.chroma_stage(base, g, log=lambda m: None, roles=AC.ALL_GRADED, its=st["its"], tag="chroma_all_s")
    ae = al["chosen_e"]
    assert al["probes"] is None and ae in st["its"] + caps
    assert AC.chroma_units_worst(ae, AC.ALL_GRADED) < AC.chroma_units_worst(ch, AC.ALL_GRADED)
    assert ch["level"]["chroma_hold"] is True and ch["level"]["chroma_hue"] == list(SH.HAVANA_HUE_DEG)


def test_tooltest_label_names_the_hold_and_hue_band(tmp_path):
    from glasses3d.tests.photoprint import print_tooltest as TT
    bake = tmp_path / "ft1119_bake"
    bake.mkdir()
    (bake / "report.json").write_text(json.dumps({"level": {"applied": True, "gain": 0.93, "gamma": 1.08, "sat": 1.05,
                                                            "chroma_power": 0.6, "chroma_hold": True,
                                                            "chroma_hue": [20.0, 100.0]}}), encoding="utf-8")
    lab = TT.make_label({"print_sha256": "ab" * 32}, bake)
    assert "chroma_power 0.600" in lab and "chroma held >= 1" in lab and "chroma hue 20-100 deg" in lab


# ----------------------------------------------------------------------------- fix round 3 of step 6
class _FakeMesh:
    def __init__(self, V, F, part, names):
        self.V, self.F, self.part, self.names = np.asarray(V, float), np.asarray(F), np.asarray(part), list(names)

    def part_ids(self, names):
        return [self.names.index(n) for n in names]


class _FakeJob:
    """The parts of a frame (model mm): two front-facing brow quads at z = 0 and two temple quads (outer faces
    turned away from x = 0) over z -100..-10, named by ``names`` per position ('bl' / 'br': the brows at -x / +x,
    'tl' / 'tr': the temples)."""

    def __init__(self, names=None, brows_x=(-1, 1)):
        names = names or {"bl": "Brow_Left", "br": "Brow_Right", "tl": "Temple_Left", "tr": "Temple_Right"}
        brow = np.array([[10, 0, 0], [60, 0, 0], [60, 20, 0], [10, 20, 0]], float)     # at +x; mirrored by brows_x
        quads = {"bl": (brow * [brows_x[0], 1, 1], (0, 0, 1)), "br": (brow * [brows_x[1], 1, 1], (0, 0, 1)),
                 "tl": (np.array([[-65, 0, -10], [-65, 0, -100], [-65, 20, -100], [-65, 20, -10]], float), (-1, 0, 0)),
                 "tr": (np.array([[65, 0, -10], [65, 0, -100], [65, 20, -100], [65, 20, -10]], float), (1, 0, 0))}
        V, F, part, order = [], [], [], ["bl", "br", "tl", "tr"]
        for k, pos in enumerate(order):
            P, nrm = quads[pos]
            f = [[0, 1, 2], [0, 2, 3]]
            if np.cross(P[1] - P[0], P[2] - P[0]) @ np.asarray(nrm, float) < 0:
                f = [[0, 2, 1], [0, 3, 2]]
            F += [[len(V) + a for a in t] for t in f]
            V += list(P)
            part += [k, k]
        self.mesh = _FakeMesh(V, F, part, [names[p] for p in order])
        n = np.cross(self.mesh.V[self.mesh.F[:, 1]] - self.mesh.V[self.mesh.F[:, 0]],
                     self.mesh.V[self.mesh.F[:, 2]] - self.mesh.V[self.mesh.F[:, 0]])
        self.face_normals = n / np.linalg.norm(n, axis=1, keepdims=True)

    def part_ids(self, names):
        return self.mesh.part_ids(names)


def test_chroma_probes_are_captured_at_the_same_time():
    """Fix round 3: the three independent probe captures overlap; the results keep the probes' order."""
    import threading
    import time
    from glasses3d.tests.photoprint import ar_calibrate as AC
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def g(sv, pv, tag):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.4 if tag == "chroma1" else 0.2)                 # the first finishes last
        with lock:
            state["active"] -= 1
        return {"tag": tag, "level": AC.with_chroma(0.93, 1.08, sv, pv)}

    out = AC.capture_probes(g)
    assert state["peak"] == len(AC.CHROMA_PROBES) == 3
    assert [e["tag"] for e in out] == ["chroma1", "chroma2", "chroma3"]
    assert [AC.chroma_level(e["level"]) for e in out] == [tuple(map(float, p)) for p in AC.CHROMA_PROBES]


def test_band_energy_on_the_cropped_mask_equals_the_whole_image():
    """Fix round 3 (speed only): the DoG on the mask's padded bounding box gives the whole image's value, also for a
    mask at the image border; a precomputed L* gives the same."""
    from scipy import ndimage
    rng = np.random.default_rng(14)
    img = np.clip(ndimage.gaussian_filter(rng.uniform(20, 200, (240, 320, 3)), (2.0, 2.0, 0)), 0, 255)
    m1 = np.zeros((240, 320), bool)
    m1[100:150, 120:220] = True
    m2 = np.zeros((240, 320), bool)
    m2[0:40, 280:320] = True
    for m, ppm in ((m1, 4.7), (m2, 3.6)):
        L = PM.lstar(img)
        full = float(PM.bandpass(L, m, 0.5 * ppm, 3.0 * ppm)[m].std())
        assert PM.band_energy(img, m, ppm) == pytest.approx(full, rel=1e-12, abs=1e-12)
        assert PM.band_energy(img, m, ppm, L=L) == PM.band_energy(img, m, ppm)


def test_capture_ppm_follows_depth_and_groups_pool_their_parts(tmp_path):
    """Fix round 3: px/mm through a perspective projection falls with depth (a temple 50 mm behind the front at 300 mm
    is 300 / 350 of the front's), and a group's band energy pools its parts' (pixel-weighted RMS)."""
    from glasses3d.tests.photoprint import ar_levels as AL
    M = np.eye(4)
    M[2, 3] = -0.3                                                    # the model 0.3 m in front of the camera
    f = 2.0
    P = np.array([[f, 0, 0, 0], [0, f, 0, 0], [0, 0, -1.0, -0.02], [0, 0, -1.0, 0]])
    cap = tmp_path / "cap"
    cap.mkdir()
    pose = {"id": "front", "asset_to_world": M.T.ravel().tolist(),
            "camera": {"projection": P.T.ravel().tolist(), "width": 400, "height": 400}}
    (cap / "report.json").write_text(json.dumps({"poses": [pose]}), encoding="utf-8")
    front, back = AL.capture_ppm(cap, "front", (0.0, 0.0, 0.0)), AL.capture_ppm(cap, "front", (0.0, 0.0, -50.0))
    assert back / front == pytest.approx(300.0 / 350.0, rel=1e-3)
    assert AL.capture_ppm(cap, "front") == pytest.approx(AL.capture_ppm(cap, "front", AL.BRIDGE_MM))
    assert AL._pooled_energy([(2.0, 100), (4.0, 300)]) == pytest.approx(math.sqrt(13.0))
    assert AL._pooled_energy([(None, 10), (3.0, 0)]) is None
    pts = AL.part_points(_FakeJob())
    assert pts["Temple_Left"][0] == pytest.approx(-65.0)
    assert AL.AR_TEMPLE_Z[0] <= pts["Temple_Left"][2] <= AL.AR_TEMPLE_Z[1]
    assert pts["Brow_Left"][0] == pytest.approx(-35.0) and pts["Brow_Right"][0] == pytest.approx(35.0)


def test_photo_masks_and_the_occlusion_scene_are_built_once(monkeypatch):
    """Fix round 3: ``measure`` / ``band_energies`` rebuilt the occlusion scene and the photo masks for every row;
    ``photo_mask`` builds each once per cache."""
    from glasses3d.tests.photoprint import ar_levels as AL
    calls = {"occ": 0, "mask": 0}

    def occ(job):
        calls["occ"] += 1
        return "occ"

    def mask(job, view, occ_, parts=AL.PRINT_PARTS):
        calls["mask"] += 1
        assert occ_ == "occ"
        return np.ones((2, 2), bool)

    monkeypatch.setattr(AL, "_occ", occ)
    monkeypatch.setattr(AL, "photo_frame_mask", mask)
    cache = {}
    for _ in range(3):
        AL.photo_mask(None, "reference-03.png", ("Brow_Left",), cache)
        AL.photo_mask(None, "reference-03.png", ("Temple_Left",), cache)
    assert calls == {"occ": 1, "mask": 2}


def test_the_regrade_summary():
    """The re-grade summary names the pair and term of the worst chroma unit, per role and over every graded pair
    (the coat-share half of this test went with the deleted step6_proof.py on 2026-10-13)."""
    from glasses3d.tests.photoprint import ar_calibrate as AC
    row = lambda units, excl: {"role": None, "print": {"ar_minus_photo": {"p10": 0.1, "p50": -0.2},
                                                       "appearance": {"chroma_units": units, "pass": False}},
                               "groups_excluded": excl}
    rows = {"pitch_p20_vs_05": row([0.1, 0.5, -0.2], {}) | {"role": "calibration"},
            "yaw_p45_vs_03": row([-1.6, -0.5, -1.4], {"brow": "below the minimum"}) | {"role": "held-out"},
            "pass": {}, "pass_brow": {}}
    m = {"pairs": [["pitch_p20", "05"]], "tradeoff": [], "heldout": [["yaw_p45", "03"]], "captures": {"cap": rows}}
    s = AC.regrade_summary(m, "cap")
    assert s["worst_unit_calibration"] == 0.5 and s["worst_unit_all_graded"] == 1.6
    assert s["worst_term_all_graded"] == {"role": "held-out", "pair": "yaw_p45_vs_03", "term": "p50"}
    assert s["groups_excluded"] == {"pitch_p20_vs_05": [], "yaw_p45_vs_03": ["brow"]}
