"""WP5 correction round (2026-10-06): unit tests of the four fixes and their instruments, plus one slow bake that
checks the AR level and the flatten end to end (A3 read back with both re-applied independently).

Items: (1) double lighting: ``shading.flatten`` (keep test) and ``shading.level_map``; the instrument's ``lstar`` /
``level_compare`` / ``achromatic_highlight``; (2) brow-front coverage: photometric occluder margin, bicubic footprint,
foreign-blob dilation by kind, no large-hardware margin, shoulder-only glint dilation, the local witness rule; (3)
streaks: strip-space donors in quilting, the closed see-through band, ``print_metrics.directional_energy``; (4) the
endpiece scratches: the bicubic footprint near the photo's silhouette.

Fix round 1 of the correction round: the flatten keep rule's leave-one-pair-out condition, the AR highlight mask that
does not move with the print (``level_compare(ar_highlight=)``, the black probe, the geometric pose-photo pairs),
the footprint against glint lines and bright pixels' light edges, and the tool test's record going only into the bake
it was given.

Fix round 2 of the correction round: the pair roles (only the two poses at a photo's own camera calibrate; the two
18 deg off are trade-off rows) and the calibration search between grid points. The tests of the deleted proof scripts
(the level inverse of ``correction_proof``, the owner sheet's candidate rows) were deleted on 2026-10-13 with them; see
git tag review-20261013-pre-cleanup."""
import json
import math
import struct
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d_host.photoprint import fill as FL
from glasses3d.g3d_host.photoprint import highlights as HL
from glasses3d.g3d_host.photoprint import metal as MT
from glasses3d.g3d_host.photoprint import quilt_t as QT
from glasses3d.g3d_host.photoprint import shading as SH
from glasses3d.g3d_host.photoprint import texels as TX
from glasses3d.tests.instruments import print_metrics as PM

G3D = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------------- item 1: flatten and level
def _sphere_normals(n, rng):
    v = rng.normal(size=(n, 3))
    v[:, 2] = np.abs(v[:, 2]) + 0.6                       # facing the camera (+z toward it)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


class _Cam:
    yaw = pitch = roll = 0.0


def test_fit_view_recovers_a_shading_field_through_print_noise():
    rng = np.random.default_rng(3)
    N = _sphere_normals(20000, rng)
    cam = _Cam()
    n3 = SH.camera_normals(cam, N)
    true = np.exp(0.6 * n3[:, 2] + 0.25 * n3[:, 1] - 0.3 * n3[:, 0] ** 2)       # a smooth studio light
    alb = np.exp(rng.normal(0, 0.6, len(N)))                                    # print: large log-normal spread
    Y = 0.05 * alb * true
    col = np.repeat((255 * np.clip(Y, 0, 1) ** (1 / 2.2))[:, None], 3, 1)
    from glasses3d.g3d_host import texture_utils as T
    col = T.linear_to_srgb(np.repeat(Y[:, None], 3, 1))
    fit = SH.fit_view(cam, N, col, np.ones(len(N), bool))
    f = SH.factor(fit, cam, N)
    assert fit["fitted"] and fit["r2"] < 0.5                                   # print dominates, as on FT1119
    est = np.log(f) - np.median(np.log(f))
    tru = np.log(true) - np.median(np.log(true))
    assert np.corrcoef(est, tru)[0, 1] > 0.97
    flat = SH.deshade(col, f)
    assert np.std(np.log(SH.ylin(flat))) < np.std(np.log(SH.ylin(col)))


def test_flatten_keep_test_decides():
    """Three views of the same surface under different lights: dividing each view's own field out makes every pair
    agree (kept, the leave-one-pair-out condition too); 'off' fits nothing."""
    rng = np.random.default_rng(5)
    from glasses3d.g3d_host import texture_utils as T

    class S_:
        pass

    class V_:
        pass

    n = 30000
    N = _sphere_normals(n, rng)
    alb = np.exp(rng.normal(-3, 0.5, n))
    views = {}
    job = type("J", (), {})()
    job.views = {}
    for name, (a, b) in {"reference-01.png": (0.9, 0.0), "reference-05.png": (-0.2, 0.7),
                         "reference-03.png": (-0.6, -0.4)}.items():
        cam = _Cam()
        n3 = SH.camera_normals(cam, N)
        Y = alb * np.exp(a * n3[:, 0] + b * n3[:, 1])
        s = S_()
        s.col = T.linear_to_srgb(np.repeat(np.clip(Y, 0, 1)[:, None], 3, 1))
        s.ok = np.ones(n, bool)
        views[name] = s
        v = V_()
        v.camera = cam
        job.views[name] = v
    tex = type("T", (), {})()
    tex.N = N
    r = SH.flatten(job, tex, views, mode="test")
    assert r["keep"]["pass"] and r["applied"] and r["after"]["pooled_de00"] < r["before"]["pooled_de00"]
    assert r["keep"]["loo_pass"] and r["keep"]["pairs_improved"] == "3 of 3" and len(r["keep"]["per_pair"]) == 3
    assert SH.flatten(job, tex, views, mode="off") == {"applied": False, "mode": "off", "cols": None}
    with pytest.raises(ValueError):
        SH.flatten(job, tex, views, mode="maybe")


def _agreement(rows):
    n = sum(r["texels"] for r in rows.values())
    return {"pairs": rows, "pooled_de00": sum(r["texels"] * r["de00_gained_mean"] for r in rows.values()) / n,
            "pooled_mad_log_y": sum(r["texels"] * r["mad_log_y"] for r in rows.values()) / n, "texels": n}


def test_keep_test_refuses_a_drop_that_rests_on_one_pair():
    """The correction bake's shape: one large pair carries the pooled drop, two others get worse: the pooled rule
    passes, the leave-one-pair-out rule does not; one pair alone can never validate."""
    pair = lambda n, de, mad: {"texels": n, "de00_gained_mean": de, "mad_log_y": mad}
    before = _agreement({"01-04": pair(66769, 9.39, 0.575), "01-05": pair(92616, 8.19, 0.373),
                         "01-06": pair(80131, 8.48, 0.437), "04-05": pair(66363, 11.79, 0.586)})
    after = _agreement({"01-04": pair(66769, 9.62, 0.558), "01-05": pair(92616, 7.93, 0.330),
                        "01-06": pair(80131, 8.74, 0.484), "04-05": pair(66363, 9.75, 0.464)})
    k = SH.keep_test(before, after)
    assert k["pooled_pass"] and not k["loo_pass"] and not k["pass"] and k["loo_worst_pair"] == "04-05"
    assert k["per_pair"]["01-06"]["de00_drop"] < 0 and k["pairs_improved"] == "2 of 4"
    one_b = _agreement({"04-05": pair(66363, 11.79, 0.586)})
    one_a = _agreement({"04-05": pair(66363, 9.75, 0.464)})
    assert not SH.keep_test(one_b, one_a)["pass"]


def test_level_map_identity_monotone_and_keeps_chromaticity():
    rgb = np.array([[30.0, 20.0, 15.0], [120.0, 80.0, 40.0], [200.0, 150.0, 90.0]])
    assert np.array_equal(SH.level_map(rgb, None), rgb)
    out = SH.level_map(rgb, {"gain": 0.6, "gamma": 1.2})
    assert (np.diff(PM.lstar(out)) > 0).all() and (PM.lstar(out) < PM.lstar(rgb)).all()
    from glasses3d.g3d_host import texture_utils as T
    li, lo = T.srgb_to_linear(rgb), T.srgb_to_linear(out)
    assert np.allclose(li / li.sum(1, keepdims=True), lo / lo.sum(1, keepdims=True), atol=1e-6)
    img = np.full((4, 4, 3), 100, np.uint8)
    m = np.zeros((4, 4), bool)
    m[0, 0] = True
    o = SH.apply_level_u8(img, m, {"gain": 0.5, "gamma": 1.0})
    assert (o[0, 0] < 100).all() and (o[~m] == 100).all()


def test_instrument_lstar_and_level_compare():
    assert np.allclose(PM.lstar(np.array([[255, 255, 255], [0, 0, 0], [119, 119, 119]])), [100, 0, 50.03], atol=0.05)
    rng = np.random.default_rng(0)
    dark = rng.integers(20, 60, (500, 3)).astype(float)
    grey = np.full((300, 3), 180.0)                                   # an achromatic highlight
    ar = np.concatenate([dark, grey])
    photo = dark.copy()
    r = PM.level_compare(ar, photo, exclude_highlights=True)
    assert r["highlight_share"]["ar"] == pytest.approx(300 / 800) and abs(r["ar_minus_photo"]["p50"]) < 1e-6
    assert not PM.level_compare(ar, photo)["pass"]
    # composition: the photo's second group is weighted to the AR's share
    pa = np.concatenate([dark, dark[:10]])
    ga = np.r_[np.zeros(500), np.ones(10)]
    pp = np.concatenate([dark, np.full((500, 3), 200.0)])
    gp = np.r_[np.zeros(500), np.ones(500)]
    m = PM.level_compare(pa, pp, ar_group=ga, photo_group=gp)
    u = PM.level_compare(pa, pp)
    assert abs(m["ar_minus_photo"]["p50"]) < 1.0 < abs(u["ar_minus_photo"]["p50"])


def test_level_compare_takes_an_ar_highlight_mask_that_does_not_move_with_the_print():
    """Fix round 1: light grey PRINT pixels pass the pixel rule and would leave the target; a probe mask (the
    renderer's reflections alone) keeps them in, and the same mask applies to every candidate."""
    rng = np.random.default_rng(2)
    dark = rng.integers(20, 60, (500, 3)).astype(float)
    refl = np.full((100, 3), 200.0)                                   # the renderer's reflection
    washed = np.full((200, 3), 120.0)                                 # light, grey print (L* ~ 50, C* 0)
    ar = np.concatenate([dark, refl, washed])
    mask = np.r_[np.zeros(500, bool), np.ones(100, bool), np.zeros(200, bool)]
    photo = dark.copy()
    own = PM.level_compare(ar, photo, exclude_highlights=True)
    fixed = PM.level_compare(ar, photo, exclude_highlights=True, ar_highlight=mask)
    assert own["highlight_share"]["ar"] == pytest.approx(300 / 800) and own["pass"]          # the washed print hid
    assert fixed["highlight_share"]["ar"] == pytest.approx(100 / 800) and not fixed["pass"]  # ... and is seen now
    assert fixed["ar_minus_photo"]["p50"] > 2 and "independent" in fixed["ar_highlight_source"]
    with pytest.raises(ValueError):
        PM.level_compare(ar, photo, exclude_highlights=True, ar_highlight=mask[:-1])


def test_pose_photo_pairs_follow_the_cameras(ft1119_job):
    """Fix round 2: only the poses at a photo's own camera (within 5 deg) calibrate; the zero-elevation poses 18 deg
    below photos 05 / 01 are trade-off rows, the 20-30 deg ones held-out."""
    from glasses3d.tests.photoprint import ar_levels as AL
    t = {r["pose"]: r for r in AL.pair_table(ft1119_job)}
    assert set(AL.auto_pairs(ft1119_job, "photo_angle")) == {("pitch_p20", "05"), ("yaw_m30_pitch_p20", "01")}
    assert set(AL.auto_pairs(ft1119_job, "near")) == {("front", "05"), ("yaw_m30", "01")}
    assert set(AL.auto_pairs(ft1119_job, "loose")) == {("yaw_m45", "01"), ("yaw_p45", "03")}
    assert t["pitch_p20"]["angle_deg"] < 3 and t["yaw_m30_pitch_p20"]["angle_deg"] < 3
    assert 15 < t["front"]["angle_deg"] <= AL.MATCH_DEG and 15 < t["yaw_m30"]["angle_deg"] <= AL.MATCH_DEG
    with pytest.raises(ValueError):
        AL.auto_pairs(ft1119_job, "matched")                             # fix round 1's role name is gone
    assert t["yaw_p30"]["photo"] != "01"                                # the mirror pose never matches photo 01
    assert np.allclose(AL.pose_direction(-30, 0), [0.5, 0, np.sqrt(3) / 2])      # yaw < 0: the +x side near


# ----------------------------------------------------------------------------- item 2 / 4: donor rules
def test_foreign_blobs_dilate_only_gold():
    img = np.full((80, 120, 3), (60, 40, 30), np.uint8)
    img[20:40, 20:40] = (235, 215, 160)                 # gold (R - B 75)
    img[20:40, 70:90] = (205, 201, 192)                 # silver / glare
    matte = np.ones((80, 120), bool)
    r = TX.foreign_blobs(img, matte, matte, 8.0)
    kinds = sorted(b["kind"].split(" ")[0] for b in r["blobs"])
    assert kinds == ["bright", "gold"]
    assert r["mask"][18, 30] and not r["mask"][18, 80]  # the gold blob's ring is a hole, the silver one's is not
    assert r["mask"][30, 80]
    r4 = TX.foreign_blobs(img, matte, matte, 8.0, dilate_all=True)
    assert r4["mask"][18, 80]                           # the step-4 rule dilated every blob


def _scene_ids(H, W):
    fid = np.zeros((H, W), np.int64)                    # face 0: print everywhere
    is_print = np.array([True, False, False])
    return fid, is_print


def test_large_hardware_margin_off_small_kept():
    H, W = 80, 120
    img = np.full((H, W, 3), (50, 35, 28), np.uint8)
    fid, _ = _scene_ids(H, W)
    fid[40:42, 10:110] = 1                              # face 1: large hardware (a T-trim bar)
    fid[10:16, 10:16] = 2                               # face 2: small hardware (a hinge)
    is_print = np.array([True, False, False, False])
    is_hw = np.array([0, 1, 2, 0], np.uint8)
    ok = MT.donor_holes(img, np.ones((H, W), bool), fid, is_print, is_hw, 8.0)
    old = MT.donor_holes(img, np.ones((H, W), bool), fid, is_print, is_hw, 8.0, rules="step4")
    assert not ok["hardware"][44, 60] and old["hardware"][44, 60]       # 2 px from the bar: only step 4 cut it
    assert ok["hardware"][20, 13] and old["hardware"][20, 13]          # the hinge keeps its 2.5 mm


def test_footprint_keeps_clear_of_glints_and_bright_edges():
    """Fix round 1: a sample 3-4 px from a silver bar whose anti-aliased edge (light, but passing the acetate test)
    lies within its bicubic support is refused by the 'edges' rule; a sample beside a glint line only with the glint
    footprint switched on (off by default); the correction round's 'plain' footprint admitted both."""
    H, W = 80, 120
    img = np.full((H, W, 3), (55, 38, 28), np.float64)
    img[20:60, 40:44] = (205, 203, 198)                 # a silver bar (bright, non-acetate)
    img[20:60, 44:46] = (175, 140, 105)                 # its anti-aliased edge: light, passes the acetate test
    matte = np.ones((H, W), bool)
    glints = np.zeros((H, W), bool)
    glints[10, 60:110] = True
    from glasses3d.g3d_host.photoprint import signals as S_
    assert S_.acetate_mask(img)[30, 45]
    plain = TX.footprint_holes(img, matte, 6.5, glints=glints, rule="plain", glint_px=1.5)
    edges = TX.footprint_holes(img, matte, 6.5, glints=glints, rule="edges", glint_px=1.5)
    default = TX.footprint_holes(img, matte, 6.5, glints=glints)
    assert not plain[30, 47] and edges[30, 47] and default[30, 47]     # 3.0 px from the bar's light edge ...
    assert not plain[11, 80] and edges[11, 80] and not default[11, 80]  # 1 px beside the glint: only when switched on
    assert not edges[30, 70] and not edges[14, 80]      # print far from both stays a donor
    with pytest.raises(ValueError):
        TX.footprint_holes(img, matte, 6.5, rule="maybe")


def test_glint_grows_only_into_its_shoulder():
    H, W = 60, 120
    img = np.full((H, W, 3), (45, 32, 26), np.float64)
    img[30, 10:110] += 60                               # a 1-px achromatic glint ...
    img[31, 10:110] += 8                                # ... with a faint shoulder below (< half its contrast)
    img[29, 10:110] += 20                               # ... and a stronger one above (>= half)
    img = np.clip(img, 0, 255).astype(np.uint8)
    inside = np.ones((H, W), bool)
    edge = np.zeros((H, W), bool)
    tl = MT.thin_lines(img, inside, edge, 8.0)
    assert tl["bright"][30, 60] and tl["shoulder"][29, 60] and not tl["shoulder"][31, 60]
    fid, is_print = _scene_ids(H, W)
    a = MT.donor_holes(img, np.ones((H, W), bool), fid, is_print, np.zeros(3, np.uint8), 8.0)
    b = MT.donor_holes(img, np.ones((H, W), bool), fid, is_print, np.zeros(3, np.uint8), 8.0, rules="step4")
    assert a["lines"][29, 60] and a["lines"][30, 60] and not a["lines"][31, 60]
    assert b["lines"][31, 60]                            # step 4's blanket pixel took the dark print below


def test_witness_flags_need_a_local_highlight():
    """A texel 30 levels brighter than an unregistered witness but no brighter than its own surroundings (honey
    print the witness saw 2 mm away) is not a highlight; a local bright spot still is."""
    H, W = 40, 40

    class Tx:
        shape = (H, W)
        ii, jj = np.nonzero(np.ones((H, W), bool))

        def image(self, v, fill=0, dtype=None):
            v = np.asarray(v)
            out = np.full((H, W) + v.shape[1:], fill, dtype=dtype or v.dtype)
            out[self.ii, self.jj] = v
            return out
    tex = Tx()
    n = H * W
    col = np.tile([[150.0, 120.0, 100.0]], (n, 1))     # muted honey everywhere in the painting view
    spot = (tex.ii == 20) & (tex.jj == 20)
    col[spot] = [200.0, 190.0, 180.0]                   # one local highlight
    wit = np.tile([[110.0, 70.0, 40.0]], (n, 1))        # the witness sees darker print here (misregistered)
    label = np.zeros(n, np.int64)
    painted = np.ones(n, bool)
    w = {0: [(1, np.ones(n, bool), wit)]}
    old = HL.flag(tex, painted, col, label, w)
    new = HL.flag(tex, painted, col, label, w, tx=8.0)
    assert old["witness"] > 1000
    assert new["mask"][spot].all() and new["mask"].sum() < 20 and new["witness_not_local"] > 1000


# ----------------------------------------------------------------------------- item 3: strip-space donors
def _striped(H=90, W=400):
    img = np.zeros((H, W, 3), np.uint8)
    for t in range(H):                                  # bands across the strip: an edge glint row band and print
        img[t] = (220, 220, 220) if t < 12 else ((150, 100, 60) if (t // 24) % 2 else (50, 35, 28))
    return img


def test_quilt_without_tband_is_the_step4_filler():
    img = _striped()
    hole = np.zeros(img.shape[:2], bool)
    hole[40:52, 150:250] = True
    donors = ~hole
    a = FL.quilt_call(img, hole, donors, 6, 11)
    b = FL.quilt_call(img, hole, donors, 6, 11, tband=None)
    assert np.array_equal(a["out"], b["out"]) and not a["strip_t"]


def test_strip_space_donors_come_from_the_same_t():
    img = _striped()
    H, W = img.shape[:2]
    hole = np.zeros((H, W), bool)
    hole[54:62, 150:250] = True                         # inside the dark band t 48-71
    donors = ~hole
    tband = np.tile(np.arange(H, dtype=float)[:, None], (1, W))
    r = FL.quilt_call(img, hole, donors, 4, 2, tband=tband)
    got = r["out"][hole].astype(float)
    assert r["strip_t"] and (np.abs(got - np.array([50, 35, 28])).max(1) <= 2).mean() > 0.95
    assert not (got > 200).any()                         # never the edge glint band


def test_directional_energy_sees_lines_along_the_strip():
    rng = np.random.default_rng(1)
    iso = np.repeat(rng.normal(80, 20, (120, 400))[..., None], 3, -1)
    from scipy import ndimage
    iso = np.repeat(ndimage.gaussian_filter(iso[..., 0], 4)[..., None], 3, -1)
    lines = iso.copy()
    for y in range(10, 120, 40):
        lines[y:y + 3] -= 40
    sup = np.ones(iso.shape[:2], bool)
    a = PM.directional_energy(iso, sup, sup, 16.0)
    b = PM.directional_energy(lines, sup, sup, 16.0)
    assert 0.5 < a["across_over_along"] < 2.0 < b["across_over_along"]


# ----------------------------------------------------------------------------- calibration plumbing
def test_ar_calibrate_swaps_only_the_print_image(tmp_path):
    from glasses3d.tests.photoprint import ar_calibrate as AC
    src = G3D / "data/wp5/pre_correction_tooltest/q001_painted_TOOLTEST.glb"
    if not src.is_file():
        pytest.skip("the tool-test GLB is local-only")
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.full((8, 8, 3), 77, np.uint8)).save(buf, format="PNG")
    png = buf.getvalue()
    dst = tmp_path / "x_TOOLTEST.glb"
    info = AC.swap_image(src, dst, png)
    d0, b0 = AC._chunks(src.read_bytes())
    d1, b1 = AC._chunks(dst.read_bytes())
    v = d1["bufferViews"][info["bufferView"]]
    assert b1[v["byteOffset"]:v["byteOffset"] + v["byteLength"]] == png
    for i, (a, b) in enumerate(zip(d0["bufferViews"], d1["bufferViews"])):
        if i == info["bufferView"]:
            continue
        assert b0[a.get("byteOffset", 0):a.get("byteOffset", 0) + a["byteLength"]] == \
            b1[b.get("byteOffset", 0):b.get("byteOffset", 0) + b["byteLength"]]
    assert {k: v for k, v in d0.items() if k not in ("bufferViews", "buffers")} == \
        {k: v for k, v in d1.items() if k not in ("bufferViews", "buffers")}


def test_black_probe_blackens_only_the_print_parts_images(tmp_path):
    from glasses3d.tests.photoprint import ar_calibrate as AC
    src = G3D / "data/wp5/pre_correction_tooltest/q001_painted_TOOLTEST.glb"
    if not src.is_file():
        pytest.skip("the tool-test GLB is local-only")
    dst = tmp_path / "probe_TOOLTEST.glb"
    info = AC.black_probe(src, dst)
    d0, _ = AC._chunks(src.read_bytes())
    d1, b1 = AC._chunks(dst.read_bytes())
    assert info["images"] == [AC.print_image_index(d0)] == AC.part_image_indices(d0)
    import io
    from PIL import Image
    v = d1["bufferViews"][d1["images"][info["images"][0]]["bufferView"]]
    im = np.asarray(Image.open(io.BytesIO(b1[v["byteOffset"]:v["byteOffset"] + v["byteLength"]])))
    assert im.max() == 0 and d0["materials"] == d1["materials"] and d0["meshes"] == d1["meshes"]


def _synthetic_residuals(gain, gamma):
    """Residuals with the shape of fix round 1's captures (slopes from its grid at gain 0.55-0.75, gamma 1.0-1.15:
    p10 / p50 of the second photo-angle pair 3.5 L* apart at gamma 1 and further apart as gamma rises, so the worst
    cannot fall below ~1.7 at gamma 1), a mild curvature in log gain, and the trade-off rows as a second target."""
    lg, g = math.log(gain / 0.55), gamma - 1.0
    cal = (np.array([-3.16, -3.91, -0.19, -4.18]) + np.array([9.86, 10.48, 8.64, 10.19]) * lg
           - np.array([34.5, 33.5, 29.9, 31.9]) * g + 1.5 * lg ** 2)
    near = (np.array([-0.29, 1.94, 2.98, 0.61]) + np.array([7.9, 12.7, 8.5, 9.0]) * lg
            - np.array([25.9, 39.3, 28.4, 29.9]) * g + 1.0 * lg ** 2)
    # step 6: the 'appearance' role (the delivered target) = the calibration residuals + one contrast term per pair
    # (2 ln(std ratio) / ln 1.18), the ratio rising along the passing ridge as step 6 measured (1.12 -> 1.24)
    contrast = np.array([0.6 + 3.0 * g + 0.8 * lg, -0.3 + 1.0 * g + 0.4 * lg])
    return {"calibration": [float(x) for x in cal], "trade-off": [float(x) for x in near], "held-out": [],
            "appearance": [float(x) for x in np.r_[cal, contrast]]}


def test_calibration_search_refines_between_grid_points():
    """Fix round 2: the trust-region minimax reaches the target's best level between the grid points (within 0.15 L*
    of the brute-force minimax of the synthetic model) and chooses a CAPTURED level; fix round 1 stopped at a coarse
    grid point for every candidate but the pooled one."""
    from glasses3d.tests.photoprint import ar_calibrate as AC
    its = []

    def f(gain, gamma, tag):
        e = {"tag": tag, "level": {"gain": gain, "gamma": gamma}, "residuals": _synthetic_residuals(gain, gamma)}
        its.append(e)
        return e

    for gm in AC.GRID[1]:
        for gn in AC.GRID[0]:
            f(gn, gm, f"g{gn}_{gm}")
    grid_best = {r: AC.worst(AC.best(its, r), r) for r in ("calibration", "trade-off", "appearance")}
    box = AC.search_box()
    G, M = np.meshgrid(np.exp(np.linspace(*np.log(box["gain"]), 300)), np.linspace(*box["gamma"], 241))
    # fix round 1 of step 6: the roles come from AC.TARGETS (the delivered target searches 'appearance'), as calibrate
    # runs them; the plain 'calibration' role is searched too (the search is role-agnostic)
    assert AC.TARGETS == {"photo_angles": "appearance", "near_18deg": "trade-off"}
    for name, role in list(AC.TARGETS.items()) + [("calibration_only", "calibration")]:
        res = AC.search(its, f, role, 8, f"{name}_s", log=lambda m: None)
        truth = min(max(abs(x) for x in _synthetic_residuals(g, m)[role]) for g, m in zip(G.ravel(), M.ravel()))
        got = AC.worst(AC.best(its, role), role)
        assert got <= truth + 0.15, (name, got, truth, res)
        assert got < grid_best[role] - 0.1                                # it refined between the grid points
        assert res["best"] in {e["tag"] for e in its} and res["steps"] <= 8
    assert grid_best["calibration"] > 2.0 >= AC.worst(AC.best(its, "calibration"), "calibration")  # passes only refined
    assert AC.worst(AC.best(its, "appearance"), "appearance") < grid_best["appearance"] - 0.1


def test_tooltest_records_only_into_the_bake_it_was_given(tmp_path):
    """Fix round 1: record_in_bake writes the given bake's bake.json when its print is the attached one, says why
    not otherwise, and touches nothing else."""
    import hashlib
    from glasses3d.tests.photoprint import print_tooltest as TT
    bake = tmp_path / "bake"
    bake.mkdir()
    (bake / "print.png").write_bytes(b"png bytes")
    man = {"print_sha256": hashlib.sha256(b"png bytes").hexdigest()}
    checks = {"A6": True, "B5": False}
    r = TT.record_in_bake(bake, man, checks, tmp_path / "tooltest.json")
    assert not r["recorded"] and "bake.json" in r["reason"]
    import time
    time.sleep(0.05)
    (bake / "bake.json").write_text(json.dumps({"STOP_B": {"reasons": ["A6 (Blender round trip) not run", "B3 fails"],
                                                           "fired": True}}), encoding="utf-8")
    r = TT.record_in_bake(bake, man, checks, tmp_path / "tooltest.json")
    d = json.loads((bake / "bake.json").read_text(encoding="utf-8"))
    assert r["recorded"] and d["tooltest"]["json"] == str(tmp_path / "tooltest.json")
    assert d["STOP_B"]["reasons"] == ["B3 fails", "B5 fails on the tool-test GLB of this print"]
    other = TT.record_in_bake(bake, {"print_sha256": "0" * 64}, checks, tmp_path / "t2.json")
    assert not other["recorded"] and "not the attached print" in other["reason"]


# ----------------------------------------------------------------------------- end to end (slow)
@pytest.mark.slow
def test_bake_with_flatten_and_level_reads_back(ft1119_job, tmp_path):
    """A bake at 8 texels/mm with flatten ON (fix round 1: the keep test may refuse it, so the re-application is
    exercised with 'on') and an AR level: A3 read back with the declared shading and level re-applied by the proof;
    the level is the last step (atlas = level_map(atlas_radiance) on textured texels)."""
    from glasses3d.g3d_host.photoprint import inputs as IN
    from glasses3d.g3d_host.photoprint import project as PR
    from glasses3d.tests.photoprint import printproof as PP
    lv = {"gain": 0.78, "gamma": 1.16}
    res = PR.project_photo_texture(ft1119_job, IN.FT1119_PARTS, out_dir=tmp_path, tx_per_mm=8.0, workers=2,
                                   log=None, level=lv, level_source="test", print_reg_mm=0.8, edge_mean_mm=1.0,
                                   edge_p90_mm=2.5, flatten="on")
    rep = res["report"]
    assert rep["flatten"]["mode"] == "on" and rep["flatten"]["applied"] and "loo" in rep["flatten"]["keep"]
    assert rep["level"]["applied"]
    assert rep["strip_donors"] and rep["level"]["gain"] == 0.78
    used = res["tex"].index >= 0
    exp = SH.apply_level_u8(res["final_radiance"], used & ~res["tex"].image(res["no_texture"], False, bool), lv)
    assert np.array_equal(exp, res["final_unpadded"])
    r = PP.readback_a3(ft1119_job, res, tmp_path)
    assert r["level_reapplied"] and r["flatten_reapplied"] == rep["flatten"]["applied"]
    assert r["pass"], r
    with pytest.raises(PR.RefusedError):
        PR.project_photo_texture(ft1119_job, IN.FT1119_PARTS, out_dir=tmp_path, tx_per_mm=8.0, level={"gain": 0},
                                 print_reg_mm=0.8, edge_mean_mm=1.0, edge_p90_mm=2.5)
