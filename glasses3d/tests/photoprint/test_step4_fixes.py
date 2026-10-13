"""WP5 build step 4 (host) fixes, unit level: the metal / highlight colour model and hardware margins (fix 1), the
block-quilting filler (fix 2) and the gains (fix 3: same-part edges, mirror equations, the levelling field); the
chart-seam reflection test (fix 4) was deleted on 2026-10-13 with ``seams.reflect_seams``. The bake-level numbers are
in test_print_bake.py and the proof run (bake_ft1119). Sides are the x sign."""
import numpy as np
import pytest
from scipy import ndimage

from glasses3d.g3d_host.photoprint import fill as FL
from glasses3d.g3d_host.photoprint import gains as GN
from glasses3d.g3d_host.photoprint import metal as MT


# ----------------------------------------------------------------------------- fix 1: metal / highlight colours
def _arm_photo(shape=(160, 240), seed=0, honey_br=0.40):
    """A synthetic havana photo: dark brown (lum 30-60) and amber (lum 100-160, B/R ~ honey_br) blotches."""
    rng = np.random.default_rng(seed)
    t = ndimage.gaussian_filter(rng.standard_normal(shape), 3.0)
    t = (t - t.min()) / (t.max() - t.min())
    R = 45 + 170 * t
    G = R * (0.62 + 0.05 * rng.standard_normal(shape) * 0.1)
    B = R * np.where(t > 0.4, honey_br, 0.62) + rng.normal(0, 1.0, shape)
    return np.clip(np.stack([R, G, B], -1), 0, 255).astype(np.uint8)


def test_pigment_arm_flags_gold_and_white_but_not_amber():
    img = _arm_photo()
    gold = (np.array([[139, 105, 80], [170, 134, 99], [187, 149, 116], [128, 104, 78]], float))
    white = np.array([[150, 125, 118], [122, 100, 94]], float)   # a highlight over amber that PASSES the acetate test
    pr = np.ones(img.shape[:2], bool)
    arm = MT.pigment_arm(img, pr, np.zeros_like(pr), pr, 8.0)
    assert arm["fitted"] >= 3
    assert not MT.pale_mask(img, arm)[pr].mean() > 0.02             # the photo's own amber phase stays print
    assert MT.pale_mask(gold[None].astype(np.uint8), arm).all()
    assert MT.pale_mask(white[None].astype(np.uint8), arm).all()
    amber = np.array([[146, 101, 57], [136, 88, 51]], np.uint8)
    assert not MT.pale_mask(amber[None], arm).any()


def test_pigment_arm_adapts_to_a_desaturated_photo():
    """06's honey phase is desaturated (B/R 0.55-0.62): fitted on that photo, it stays print; its glints still go."""
    img = _arm_photo(honey_br=0.58)
    pr = np.ones(img.shape[:2], bool)
    arm = MT.pigment_arm(img, pr, np.zeros_like(pr), pr, 8.0)
    honey = np.array([[168, 128, 101], [162, 123, 93]], np.uint8)
    assert not MT.pale_mask(honey[None], arm).any()
    assert MT.pale_mask(np.array([[[150, 125, 122]]], np.uint8), arm).all()     # B/R 0.81, passes the acetate test


def test_pigment_arm_continuity_prior_ignores_a_highlight_dominated_bin():
    """Photo 02: at luminance >= 110 the temple inner faces' sheen (B/R ~0.67) outnumbers the amber 16:1, so those
    bins' modes are the sheen; the continuity prior carries the last arm bin's centre instead."""
    img = _arm_photo()
    f = img.astype(float)
    lum = f.mean(-1)
    sheen = lum >= 110
    f[sheen] = np.stack([f[sheen].mean(-1) * 1.25, f[sheen].mean(-1) * 0.95, f[sheen].mean(-1) * 0.80], -1)
    img2 = np.clip(f, 0, 255).astype(np.uint8)
    pr = np.ones(img.shape[:2], bool)
    arm = MT.pigment_arm(img2, pr, np.zeros_like(pr), pr, 8.0)
    assert arm["jumped_bins"], arm
    thr = [t for t in arm["threshold"] if t is not None]
    assert max(thr) < 0.6, arm                                      # the sheen (B/R ~0.64) is above every threshold


def test_hardware_faces_on_q001(ft1119_job):
    job = ft1119_job
    pids = job.part_ids(["Brow_Left", "Brow_Right", "Temple_Left", "Temple_Right"])
    hw = MT.hardware_faces(job, pids)[:len(job.mesh.F)]
    kind = {n: int(hw[job.mesh.part == i].max()) for i, n in enumerate(job.mesh.names)}
    for n in ("Temple_hinge_Left", "Front_hinge_Right", "Pad_stalk_Left", "Pad_stalk_Right"):
        assert kind[n] == 2, (n, kind[n])                           # small: the 2.5 mm margin
    for n in ("Saddle_bridge", "T_vertical_Left", "T_arm_Right", "T_return_Left", "Hinge_screw_Left"):
        assert kind[n] == 1, (n, kind[n])                           # large or thin: 1.0 mm
    for n in ("Eyewire_Left", "Lens_Right", "Brow_Left", "Temple_Right", "Nose_pad_Left"):
        assert kind[n] == 0, (n, kind[n])


def test_donor_holes_hardware_margin_and_enclosed():
    H, W = 120, 160
    img = np.full((H, W, 3), (60, 40, 30), np.uint8)
    fid = np.zeros((H, W), np.int64)                                # face 0 = print everywhere
    fid[50:60, 70:80] = 1                                           # face 1 = small hardware
    is_print = np.array([True, False, False])
    is_hw = np.array([0, 2, 0], np.uint8)
    # a bright metal ring (non-acetate) with an acetate-coloured inside, away from the hardware
    img[20:36, 20:36] = (230, 220, 200)
    img[24:32, 24:32] = (90, 60, 40)
    dh = MT.donor_holes(img, np.ones((H, W), bool), fid, is_print, is_hw, 8.0)
    d = ndimage.distance_transform_edt(~(fid == 1))
    assert dh["hardware"][d <= MT.HARDWARE_MARGIN_MM * 8.0].all() and not dh["hardware"][d > MT.HARDWARE_MARGIN_MM * 8.0 + 1].any()
    assert dh["mask"][24:32, 24:32].all()                           # enclosed island goes with the ring
    assert not dh["mask"][80:, 100:].any()                          # plain havana stays


# ----------------------------------------------------------------------------- fix 2: quilting
def _havana_img(shape=(96, 160), seed=2):
    t = ndimage.gaussian_filter(np.random.default_rng(seed).standard_normal(shape), 2.0)
    t = (t - t.min()) / (t.max() - t.min())
    return np.stack([58 + 147 * t, 34 + 94 * t, 22 + 30 * t], -1).astype(np.uint8)


def test_quilt_contract_and_determinism():
    img = _havana_img()
    hole = np.zeros(img.shape[:2], bool)
    hole[30:60, 60:110] = True
    donors = ~hole
    donors[:, :15] = False
    r = FL.quilt_call(img, hole, donors, 5, 7)
    assert r["outside_changed"] == 0 and r["hole_changed"] > 0.95 * hole.sum() and r["nearest_px"] == 0
    assert np.array_equal(r["out"][~hole], np.where(hole[..., None], 0, img)[~hole])
    r2 = FL.quilt_call(img, hole, donors, 5, 7)
    assert np.array_equal(r["out"], r2["out"])
    u = np.zeros_like(img)
    u[:] = (200, 80, 40)
    r3 = FL.quilt_call(u, hole, ~hole, 5, 1)
    assert (r3["out"][hole] == (200, 80, 40)).all()


def test_quilt_keeps_fine_energy_where_patchmatch_smooths():
    """The mechanism behind the design's risk 9 (PatchMatch's voting averages patches), on a fine SYNTHETIC havana with
    both fillers at the SAME radius: the quilted hole keeps its fine-band energy within 30 %, PatchMatch's falls
    further below it. Not a claim about A4 on FT1119: there, each filler at its own knockout radius, PatchMatch is
    closer to A4 on the temples (tests/photoprint/test_print_bake.py, test_a4_each_filler_at_its_own_radius)."""
    img = _havana_img(shape=(128, 192), seed=5)
    hole = np.zeros(img.shape[:2], bool)
    hole[40:88, 60:130] = True

    def energy(x, m):
        L = x.astype(float).mean(-1)
        bp = ndimage.gaussian_filter(L, 1.0) - ndimage.gaussian_filter(L, 4.0)
        return float(bp[ndimage.binary_erosion(m, iterations=3)].std())
    e_true = energy(img, hole)
    eq = energy(FL.quilt_call(img, hole, ~hole, 6, 3)["out"], hole)
    ep = energy(FL.patchmatch_call(img, hole, ~hole, 6, 3)["out"], hole)
    assert abs(np.log(eq / e_true)) < np.log(1.3), (eq, e_true)
    assert eq > ep, (eq, ep)


def test_fill_call_dispatch():
    img = _havana_img()
    hole = np.zeros(img.shape[:2], bool)
    hole[30:40, 60:70] = True
    assert FL.fill_call("quilt", img, hole, ~hole, 3, 0)["method"] == "quilt"
    assert FL.fill_call("patchmatch", img, hole, ~hole, 3, 0)["method"] == "patchmatch"
    with pytest.raises(ValueError):
        FL.fill_call("smear", img, hole, ~hole, 3, 0)


# ----------------------------------------------------------------------------- fix 3: gains
class _Tex:
    """A flat two-region strip of texels on one part (x = 0..40 mm), 8 texels/mm, one chart."""

    def __init__(self, H=40, W=320, split=160, tx=8.0):
        ii, jj = np.mgrid[0:H, 0:W]
        self.ii, self.jj = ii.ravel(), jj.ravel()
        self.shape = (H, W)
        self.index = np.arange(H * W).reshape(H, W)
        self.P = np.stack([(self.jj + 0.5) / tx, (self.ii + 0.5) / tx, np.zeros(H * W)], 1)
        self.part = np.zeros(H * W, np.int64)
        self.chart = np.zeros(H * W, np.int64)
        self.region = np.where(self.jj < split, 0, 1)

    def __len__(self):
        return len(self.ii)

    def image(self, values, fill=0, dtype=None):
        values = np.asarray(values)
        out = np.full(self.shape + values.shape[1:], fill, dtype=dtype or values.dtype)
        out[self.ii, self.jj] = values
        return out


def test_level_field_removes_a_boundary_step_and_keeps_the_texture():
    tex = _Tex()
    rng = np.random.default_rng(0)
    tex_noise = ndimage.gaussian_filter(rng.standard_normal(tex.shape), 2.0).ravel() * 25
    base = np.stack([90 + tex_noise, 60 + tex_noise * 0.7, 40 + tex_noise * 0.4], 1)
    label = np.where(tex.region == 0, 0, 1)                         # two photos; view 0 is the reference
    col = base.copy()
    col[label == 1] = GN.apply_per_texel(col[label == 1], np.full(((label == 1).sum(), 3), 1.4))   # a 1.4x step
    painted = np.ones(len(tex), bool)
    lv = GN.level_field(tex, painted, label, col, 0, None, tx=8.0)
    out = GN.apply_per_texel(col, lv["gain"])
    near = np.abs(tex.P[:, 0] - 20.0) < 1.0
    a, b = near & (label == 0), near & (label == 1)
    step_before = abs(np.median(col[b].mean(1)) - np.median(col[a].mean(1)))
    step_after = abs(np.median(out[b].mean(1)) - np.median(out[a].mean(1)))
    assert step_after < 0.8 * step_before, (step_before, step_after)   # FT1119: weighted steps 4.52 -> 3.07 dE00
    assert np.allclose(lv["gain"][label == 0], 1.0, atol=0.02)      # the reference view's group stays put
    far = (label == 1) & (tex.P[:, 0] > 30)
    r = np.corrcoef(out[far].mean(1), col[far].mean(1))[0, 1]       # a smooth field: the texture is kept
    assert r > 0.99


def test_group_gains_mirror_same_view_ties_the_two_sides():
    """Two mirrored regions painted by the SAME view get equal stage-2 gains, whatever their neighbours pull."""
    tex = _Tex(W=320, split=160)
    tex.part = np.where(tex.region == 0, 0, 1)                      # two parts (no boundary edge between them)
    col = np.full((len(tex), 3), 80.0)
    label = np.zeros(len(tex), np.int64)
    painted = np.ones(len(tex), bool)
    g = GN.group_gains(tex, painted, label, col, None, mirror_region={0: 1, 1: 0})
    gains = {x["region"]: x["gain"] for x in g["groups"]}
    assert np.allclose(gains[0], gains[1])
    assert g["mirrors"] and g["mirrors"][0]["kind"] == "mirror_same_view"


def test_group_gains_never_match_across_parts():
    tex = _Tex(W=320, split=160)
    tex.part = np.where(tex.region == 0, 0, 1)
    col = np.where((tex.region == 0)[:, None], 60.0, 140.0) * np.ones((1, 3))
    label = np.where(tex.region == 0, 0, 1)
    g = GN.group_gains(tex, np.ones(len(tex), bool), label, col, 0)
    assert g["edges"] == [] and all(np.allclose(x["gain"], 1.0) for x in g["groups"])


# ----------------------------------------------------------------------------- fix 4: thin lines, dark blobs
def test_thin_lines_glints_and_outlines_but_not_print():
    """Fix 4 (glints on curved edges): a thin achromatic bright line is a glint, a thin amber streak and a round honey
    dot are print; a thin dark line near the silhouette is an outline, the same line in the middle of the print is
    print."""
    H, W = 120, 200
    img = np.zeros((H, W, 3))
    img[:] = (50, 33, 25)
    img[30:32, 20:120] += 45                                        # white glint: every channel +45
    img[50:52, 20:120, 0] += 70                                     # amber streak: red up, blue flat
    img[50:52, 20:120, 1] += 35
    yy, xx = np.mgrid[0:H, 0:W]
    img[(yy - 80) ** 2 + (xx - 60) ** 2 <= 9] += 45                 # a small round light dot
    img[100:102, 30:150] -= 40                                      # a dark line in the middle of the print
    img[60:62, 150:198] = (2, 2, 2)                                 # a dark line reaching the right edge
    img = np.clip(img, 0, 255)
    inside = np.ones((H, W), bool)
    edge = np.zeros((H, W), bool)
    edge[:, 190:] = True
    r = MT.thin_lines(img, inside, edge, 8.0)
    assert r["bright"][30:32, 30:110].mean() > 0.8
    assert not r["bright"][50:52, 30:110].any()
    assert not r["bright"][75:86, 55:66].any()
    assert not r["dark"][100:102, 30:150].any()
    assert r["dark"][60:62, 190:198].any()


def test_dark_blobs_find_a_rivet_not_havana():
    """Fix 1 (02's pad-arm rivets, about 5 mm from q001's pad stalks): a round near-black neutral disc on print is a
    hole; a warm dark havana blotch of the same size, a dark streak and a disc off the print are not."""
    H, W = 160, 220
    img = np.zeros((H, W, 3))
    img[:] = (70, 45, 33)
    yy, xx = np.mgrid[0:H, 0:W]
    img[(yy - 40) ** 2 + (xx - 40) ** 2 <= 25] = (10, 8, 7)          # rivet: neutral near-black, ~1.2 mm2 at 8 px/mm
    img[(yy - 40) ** 2 + (xx - 120) ** 2 <= 25] = (40, 18, 10)       # warm dark havana fleck (R - B 30)
    img[100:104, 20:120] = (10, 8, 7)                                # a dark streak (not round)
    img[(yy - 130) ** 2 + (xx - 190) ** 2 <= 25] = (10, 8, 7)        # a rivet-like disc off the print
    pr = np.ones((H, W), bool)
    pr[110:, 160:] = False
    r = MT.dark_blobs(img, np.ones((H, W), bool), pr, 8.0)
    assert r["mask"][40, 40] and len(r["blobs"]) == 1, r["blobs"]
    assert not r["mask"][40, 120] and not r["mask"][102, 70] and not r["mask"][130, 190]


# ----------------------------------------------------------------------------- step 4 fix round 1
def test_small_enclosed_selects_small_holes_inside_photo_only():
    """Small (<= 2 mm2) hole components whose ring is mostly photo texels are selected; a large hole and a small hole
    on the edge of an unpainted area are not."""
    tx = 8.0
    H, W = 120, 200
    hole = np.zeros((H, W), bool)
    hole[20:26, 20:30] = True                        # 60 texels = 0.94 mm2, inside the photo
    hole[50:90, 40:100] = True                       # 37.5 mm2
    hole[20:26, 147:157] = True                      # small, but 4 of its 10 columns' ring lies in the unpainted area
    painted = np.ones((H, W), bool)
    painted[:, 150:] = False
    painted &= ~hole
    m = FL.small_enclosed(hole, painted, tx)
    assert m[20:26, 20:30].all() and not m[50:90, 40:100].any() and not m[20:26, 147:157].any()
    assert int(m.sum()) == 60
    assert not FL.small_enclosed(np.zeros((H, W), bool), painted, tx).any()
    # the area limit is in mm2: at 2 texels/mm the 60-texel hole is 15 mm2, no longer small
    assert not FL.small_enclosed(hole, painted, 2.0)[20:26, 20:30].any()


def test_level_field_clip_counts_and_warning():
    """The stage-3 field's own clip is reported (texels and nodes at log 2, texels beyond x1.5) and warned like the
    stage-2 saturation; a 3x step (log 1.1, above the clip) leaves texels at the clip."""
    tex = _Tex()
    rng = np.random.default_rng(1)
    noise = ndimage.gaussian_filter(rng.standard_normal(tex.shape), 2.0).ravel() * 10
    col = np.stack([60 + noise, 40 + noise * 0.7, 30 + noise * 0.4], 1)
    label = np.where(tex.region == 0, 0, 1)
    col[label == 1] = GN.apply_per_texel(col[label == 1], np.full(((label == 1).sum(), 3), 3.0))
    lv = GN.level_field(tex, np.ones(len(tex), bool), label, col, None, None, tx=8.0, w_boundary=1.0)
    lg = np.abs(np.log(lv["gain"]))
    assert lv["texels_at_clip"] == int((lg >= GN.LEVEL_CLIP - 1e-6).any(1).sum())
    assert lv["nodes_at_clip"] >= 0 and 0.0 <= lv["texels_at_clip_share"] <= 1.0
    w = GN.support_warnings({}, {"groups": []}, lv)
    assert bool([x for x in w if "stage-3 levelling field sits at its clip" in x]) == (lv["texels_at_clip"] > 0)
    fake = {"texels_at_clip": 12, "texels_at_clip_share": 0.001, "nodes_at_clip": 2, "texels_gain_large_share": 0.01}
    w = GN.support_warnings({}, {"groups": []}, fake)
    assert len(w) == 1 and "on 12 texels" in w[0]
    assert GN.support_warnings({}, {"groups": []}, dict(fake, texels_at_clip=0)) == []


def test_spline_cache_never_serves_another_photo():
    """Step 4 fix round 1: the photo spline cache checks the image's identity. A stale entry under the same id (what a
    pool worker's second held-out bake met: a new photo array at a freed photo's address) is recomputed, not served."""
    from types import SimpleNamespace
    from glasses3d.g3d_host.photoprint import texels as TX
    a = SimpleNamespace(image=np.zeros((8, 8, 3), np.uint8))
    b = SimpleNamespace(image=np.full((8, 8, 3), 200, np.uint8))
    TX._SPLINES.clear()
    sa = TX._spline(a)
    TX._SPLINES[id(b.image)] = (a.image, sa)                 # as if b's array had taken a freed array's id
    assert np.allclose(TX.sample_bicubic(b, np.array([[3.0, 4.0]])), 200.0)
    assert TX._SPLINES[id(b.image)][0] is b.image
    assert TX._spline(a) is sa                                 # a real hit is still served from the cache
    TX._SPLINES.clear()
