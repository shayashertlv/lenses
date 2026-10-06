"""The stripes protocol is held out, on the real FT1119 job.

Fix round 1 (2026-10-05): shuffling the fold-B pixels of every photo moved the fold-A objective at the WP3b start
(0.604 -> 0.554), because the band images were band-passed over the whole ``valid`` mask (sigma up to 3.2 mm against
4 mm stripes), and the gate's held-out evaluation band-passed the render over both folds.

Fix round 2 (2026-10-05): fold supports are relabelled at each band start, so the pixels the FINAL pose labels with
the held-out fold were partly read by earlier bands (17 / 23 / 11 % of view 01's band supports), and shuffling only
those pixels changed the fold-A fit (Brow_Right max |d| 7.10 -> 2.80 mm). The fit now records every pixel it read
(``RegResult.reads``) and the gate / check_print never grade or render from them. The end-to-end tests below re-run
the WHOLE fold fit (every band, every Powell restart, guards on) on photos whose never-read pixels are permuted and
require a bit-identical pose; the teeth test shows that the first leak-free build's held-out set (every final-pose
fold-B pixel) still moves the fit.

Permutations only move VALID pixels among themselves, so ``valid`` and every visibility test stay identical and only
photo values can change."""
import numpy as np
import pytest

from glasses3d.g3d_host.photoprint import check_print as CP
from glasses3d.g3d_host.photoprint import edges as ED
from glasses3d.g3d_host.photoprint import gate as GT
from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import regions as RG
from glasses3d.g3d_host.photoprint import register as REG
from glasses3d.g3d_host.photoprint import signals as S
from glasses3d.g3d_host.photoprint.pose import Deformer, pack

pytestmark = pytest.mark.slow

V01, V05 = "reference-01.png", "reference-05.png"
TARGET = "Brow_Right[+x]/front"
# every band and a restart, from a perturbed start (so the pose really moves between the band starts and early bands
# read pixels that the final pose labels fold B); small so the test stays short
E2E = dict(fold="A", maxfev=60, max_runs=2, start_seed=1, log=None)


@pytest.fixture(scope="module")
def setup(ft1119_job):
    job = ft1119_job
    groups = RG.rigid_groups(job)
    regions = RG.build_regions(job, IN.FT1119_PARTS, groups, n_samples=4000)
    adm = ED.admitted(ED.edge_table(job, job.part_ids(IN.FT1119_PARTS)))
    plan = REG.plan_registration(job, regions, groups, adm)
    return job, groups, regions, plan, REG.start_pose(job)


def _permute(job, labels: dict, code: int, seed: int = 0):
    """A copy of the job whose photos have their VALID pixels labelled ``code`` permuted among themselves."""
    return _permute_masks(job, {n: lab == code for n, lab in labels.items()}, seed)


def _permute_masks(job, masks: dict, seed: int = 0):
    """A copy of the job whose photos have their VALID pixels inside ``masks[view]`` permuted among themselves
    (mattes kept), so ``valid`` is unchanged everywhere."""
    rng = np.random.default_rng(seed)
    imgs, mattes = {}, {}
    for n, mk in masks.items():
        v = job.views[n]
        m = np.asarray(mk, bool) & v.valid
        img = v.image.copy()
        px = img[m]
        img[m] = px[rng.permutation(len(px))]
        imgs[n], mattes[n] = img, v.matte
    out = job.with_images(imgs, mattes)
    for n in masks:
        assert np.array_equal(out.views[n].valid, job.views[n].valid)
    return out


def _same_pose(job, p, q) -> bool:
    return (all(np.array_equal(pack(job, p.cameras[n]), pack(job, q.cameras[n])) for n in job.views)
            and p.hinges == q.hinges and p.parallax == q.parallax)


def _objective(job, regions, groups, plan, pose0, fold, leaky=False):
    d = Deformer(job, regions, groups, plan.axes)
    obj = REG.Objective(job, regions, plan, d, pose0, fold)
    if leaky:                                   # the first build: band images over the whole valid mask
        obj.fold_supports = lambda pose: {n: job.views[n].valid for n in obj.pair_views}
    obj.start_band(S.REG_BANDS_MM[0], obj.z0)
    return obj.photometric(obj.pose(obj.z0))


@pytest.fixture(scope="module")
def e2e(setup):
    """One fold-A fit from WP3b; the final pose's fold-B (held-out) valid pixels of every view, and those of them the
    fit never read."""
    job, groups, regions, plan, _ = setup
    r1 = REG.register(job, regions, groups, plan, **E2E)
    d = Deformer(job, regions, groups, plan.axes)
    held, clean = {}, {}
    for n in job.views:
        lab = GT.fold_label_image(job, d, r1.pose, regions, n)
        held[n] = job.views[n].valid & (lab == RG.FOLD_B)
        clean[n] = held[n] & ~r1.reads.get(n, np.zeros(job.views[n].shape, bool))
    return r1, held, clean


def test_fold_a_objective_never_reads_fold_b_pixels(setup):
    job, groups, regions, plan, pose0 = setup
    d = Deformer(job, regions, groups, plan.axes)
    obj = REG.Objective(job, regions, plan, d, pose0, "A")
    labels = {n: GT.fold_label_image(job, d, pose0, regions, n) for n in obj.pair_views}
    shuffled = _permute(job, labels, RG.FOLD_B)
    assert _objective(job, regions, groups, plan, pose0, "A") == _objective(shuffled, regions, groups, plan, pose0, "A")
    # teeth: the leaky support and the all-stripes fit both see the permutation
    assert (_objective(job, regions, groups, plan, pose0, "A", leaky=True)
            != _objective(shuffled, regions, groups, plan, pose0, "A", leaky=True))
    assert (_objective(job, regions, groups, plan, pose0, "all")
            != _objective(shuffled, regions, groups, plan, pose0, "all"))


def test_fold_supports_hold_only_fitted_fold_region_pixels(setup):
    job, groups, regions, plan, pose0 = setup
    d = Deformer(job, regions, groups, plan.axes)
    obj = REG.Objective(job, regions, plan, d, pose0, "B")
    sup = obj.fold_supports(pose0)
    assert set(sup) == set(obj.pair_views) and V05 in sup and V01 in sup
    for n, m in sup.items():
        lab = GT.fold_label_image(job, d, pose0, regions, n)
        assert m.any() and not (m & (lab != RG.FOLD_B)).any() and not (m & ~job.views[n].valid).any()
        assert (lab == RG.FOLD_A).any()


def test_reads_are_the_union_of_every_band_support(setup, e2e):
    job, groups, regions, plan, _ = setup
    r1, _, _ = e2e
    assert set(r1.reads) == {n for _rid, a, p in plan.pairs() for n in (a, p)}
    bands = r1.info["reads"]["band_support_px"]
    assert len(bands) == len(S.REG_BANDS_MM)
    for n, m in r1.reads.items():
        short = job.views[n].short
        assert int(m.sum()) >= max(b[short] for b in bands)
        assert r1.info["reads"]["views"][short]["read_px"] == int(m.sum())
    assert REG.register(job, regions, groups, plan, fold="all", maxfev=5, max_runs=1, log=None).reads is None


def test_fold_fit_is_blind_to_every_pixel_it_did_not_read(setup, e2e):
    """End to end: permute every valid pixel the fold-A fit never read (in particular every pixel the gate and
    check_print grade for fold B), re-run the whole fit: the pose and the read set are bit-identical."""
    job, groups, regions, plan, _ = setup
    r1, held, clean = e2e
    assert sum(int(m.sum()) for m in clean.values()) > 10000
    never = {n: job.views[n].valid & ~r1.reads.get(n, np.zeros(job.views[n].shape, bool)) for n in job.views}
    shuffled = _permute_masks(job, never, seed=11)
    assert any(not np.array_equal(shuffled.views[n].image, job.views[n].image) for n in never)
    r2 = REG.register(shuffled, regions, groups, plan, **E2E)
    assert _same_pose(job, r1.pose, r2.pose)
    assert set(r2.reads) == set(r1.reads) and all(np.array_equal(r1.reads[n], r2.reads[n]) for n in r1.reads)


def test_teeth_final_pose_held_out_set_includes_read_pixels(setup, e2e):
    """The first leak-free build graded every final-pose fold-B pixel; some of them were read by early bands, and
    permuting that set moves the fit."""
    job, groups, regions, plan, _ = setup
    r1, held, _ = e2e
    read_held = sum(int((held[n] & r1.reads[n]).sum()) for n in r1.reads)
    assert read_held > 0
    shuffled = _permute_masks(job, held, seed=11)
    r3 = REG.register(shuffled, regions, groups, plan, **E2E)
    assert not _same_pose(job, r1.pose, r3.pose)


def test_gate_and_check_print_never_use_pixels_the_fit_read(setup, e2e):
    """The temple faces (anchor 06, free partner 01), where this test's fold-A fit read view-01 pixels that the final
    pose labels fold B. +x temple outer, gate 01 -> 06 and check_print held-out 06 from 01 (fold B): permuting 01's read
    pixels leaves both bit-identical when ``reads`` is applied, and moves both without it. -x temple inner, gate
    06 -> 01 and check_print held-out 01: the graded set drops 01's read pixels."""
    job, groups, regions, plan, _ = setup
    r1, held, _ = e2e
    by = {x.name: x for x in regions}
    r, ri = by["Temple_Right[+x]/outer"], by["Temple_Left[-x]/inner"]
    V06 = "reference-06.png"
    shuffled = _permute_masks(job, {V01: r1.reads[V01]}, seed=5)
    d1 = Deformer(job, regions, groups, plan.axes)
    d2 = Deformer(shuffled, regions, groups, plan.axes)
    g1 = GT.gate(job, d1, r1.pose, r, V01, V06, "B", reg_mm=0.8, reads=r1.reads)
    g2 = GT.gate(shuffled, d2, r1.pose, r, V01, V06, "B", reg_mm=0.8, reads=r1.reads)
    assert g1["source_px_read_by_fit"] > 0 and g1["E_px"] >= GT.MIN_GATE_PX and g1 == g2
    n1 = GT.gate(job, d1, r1.pose, r, V01, V06, "B", reg_mm=0.8)
    n2 = GT.gate(shuffled, d2, r1.pose, r, V01, V06, "B", reg_mm=0.8)
    assert n1["E_px"] >= GT.MIN_GATE_PX and n1["signals"] != n2["signals"]
    back = GT.gate(job, d1, r1.pose, ri, V06, V01, "B", reg_mm=0.8, reads=r1.reads)
    assert back["E_px_read_by_fit"] > 0 and back["E_px"] == back["E_px_before_reads"] - back["E_px_read_by_fit"]
    c1 = CP.case_images(job, d1, r1.pose, r, V06, [V01], "B", ref=V01, reads=r1.reads)
    c2 = CP.case_images(shuffled, d2, r1.pose, r, V06, [V01], "B", ref=V01, reads=r1.reads)
    assert np.array_equal(c1["E"], c2["E"]) and np.array_equal(c1["p_img"], c2["p_img"])
    assert c1["P"]["source_samples_read_by_fit"][V01] > 0 and not (c1["E"] & r1.reads[V06]).any()
    u1 = CP.case_images(job, d1, r1.pose, r, V06, [V01], "B", ref=V01)
    u2 = CP.case_images(shuffled, d2, r1.pose, r, V06, [V01], "B", ref=V01)
    assert not np.array_equal(u1["p_img"][u1["ren_support"]], u2["p_img"][u2["ren_support"]])
    h1 = CP.case_images(job, d1, r1.pose, ri, V01, [V06], "B", ref=V06, reads=r1.reads)
    assert h1["E_px_read_by_fit"] > 0 and not (h1["E"] & r1.reads[V01]).any()


def test_held_out_gate_never_reads_fitted_fold_render(setup):
    """Gate 01 -> 05 on fold B: permuting the SOURCE photo's fold-A pixels of the region leaves the verdict
    bit-identical; the leaky form (render band-passed on both folds) moves."""
    job, groups, regions, plan, pose0 = setup
    by = {r.name: r for r in regions}
    r = by[TARGET]
    d = Deformer(job, regions, groups, plan.axes)
    px = GT.region_pixels(job, d, pose0, r, V01)
    lab = np.full(job.views[V01].shape, GT.NO_REGION, np.int8)
    lab[px["ii"], px["jj"]] = px["fold"]
    shuffled = _permute(job, {V01: lab}, RG.FOLD_A, seed=3)
    d2 = Deformer(shuffled, regions, groups, plan.axes)
    g1 = GT.gate(job, d, pose0, r, V01, V05, "B", reg_mm=0.8)
    g2 = GT.gate(shuffled, d2, pose0, r, V01, V05, "B", reg_mm=0.8)
    assert g1["E_px"] >= GT.MIN_GATE_PX and g1["signals"] == g2["signals"]
    rr1 = GT.single_source_render(job, d, pose0, r, V05, V01)
    rr2 = GT.single_source_render(shuffled, d2, pose0, r, V05, V01)
    from scipy import ndimage
    E = (ndimage.binary_erosion(rr1["ok"], iterations=2) & ndimage.binary_erosion(job.views[V05].acetate, iterations=1)
         & (rr1["fold"] == RG.FOLD_B))
    v5 = job.views[V05]
    ppm = job.ppm(V05)
    leaky1 = GT.gate_images(rr1["ren"], rr1["ok"], v5.image, v5.matte, E, ppm, reg_mm=0.8)
    leaky2 = GT.gate_images(rr2["ren"], rr2["ok"], v5.image, v5.matte, E, ppm, reg_mm=0.8)
    assert leaky1["signals"]["warmth"]["peak"] != leaky2["signals"]["warmth"]["peak"]


def test_parallax_limit_is_a_wall(setup):
    job, groups, regions, plan, pose0 = setup
    d = Deformer(job, regions, groups, plan.axes)
    box = REG.Objective(job, regions, plan, d, pose0, "A")
    lim = REG.Objective(job, regions, plan, d, pose0, "A", parallax_limit=4.0)
    z = box.z0.copy()
    i = box.par_index[0]
    z[i], z[i + 1] = 3.5, -3.0                      # inside the §5.2 box, |d| reaches 6.5 mm at an end
    assert box.outside(z) == 0.0 and lim.outside(z) == pytest.approx(2.5)
    z[i], z[i + 1] = 2.0, -1.5
    assert lim.outside(z) == 0.0


def test_perturbed_start_is_seeded_inside_the_box_and_refers_guards_to_wp3b(setup):
    job, groups, regions, plan, pose0 = setup
    d = Deformer(job, regions, groups, plan.axes)
    obj = REG.Objective(job, regions, plan, d, pose0, "A")
    o1, o2 = obj.start_offset(1), obj.start_offset(1)
    assert np.array_equal(o1, o2) and not np.array_equal(o1, obj.start_offset(2)) and np.abs(o1).max() > 0
    z = obj.clip_into_box(obj.z0 + 10 * o1)
    assert obj.outside(z) == 0.0
    r = REG.register(job, regions, groups, plan, fold="A", maxfev=5, max_runs=1, start_seed=1, log=None)
    assert r.info["start_seed"] == 1 and r.info["start_offset"] is not None
    assert all(abs(g["start"] - g0["start"]) == 0 for g, g0 in zip(
        r.info["guards"], REG.register(job, regions, groups, plan, fold="A", maxfev=5, max_runs=1, log=None)
        .info["guards"]))
