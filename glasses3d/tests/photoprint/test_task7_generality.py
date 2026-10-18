"""Review-20261018 independent synthetic counterexamples (no product data or pools)."""
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import ndimage

from glasses3d.g3d_host.photoprint import fill as FL, gains as GN, highlights as HL
from glasses3d.g3d_host.photoprint import metal as MT, metrics as PM, signals as S, texels as TX
from glasses3d.tests.instruments import print_metrics as PROOF
from glasses3d.tests.photoprint.test_colour_policy import Atlas


COLOURS = {
    "havana": ((150, 95, 45), (60, 35, 20)),
    "blonde": ((232, 190, 115), (150, 95, 50)),
    "honey": ((240, 205, 140), (190, 140, 80)),
    "blue": ((60, 90, 170), (20, 30, 80)),
    "green": ((60, 170, 90), (20, 80, 30)),
    "grey": ((150, 150, 150), (90, 90, 90)),
    "white": ((235, 235, 235), (205, 205, 205)),
    "crystal": ((230, 226, 220), (190, 186, 180)),
}


def pattern(name):
    rng = np.random.default_rng(3)
    blob = ndimage.gaussian_filter(rng.random((240, 480)), 4)
    spots = blob > np.percentile(blob, 70)
    light, dark = COLOURS[name]
    img = np.where(spots[..., None], dark, light) + rng.normal(0, 2, (240, 480, 3))
    return np.clip(img, 0, 249).astype(np.uint8), spots


def donor_row(name):
    img, spots = pattern(name)
    matte = np.ones(img.shape[:2], bool)
    dh = MT.donor_holes(img, matte, np.zeros(matte.shape, int), np.array([True, False]),
                        np.array([0, 0], np.uint8), 6.)
    policy = dh["arm"]["colour_policy"]
    fp = TX.footprint_holes(img, matte, 6., glints=dh["bright_lines"], colour_policy=policy)
    ok = S.valid_mask(img, matte, policy) & ~dh["mask"] & ~fp
    atlas = Atlas(img.shape[:2])
    hl = HL.flag(atlas, ok.ravel(), img.reshape(-1, 3), np.zeros(ok.size, int), {},
                 tx=6., colour_policies={0: policy})
    final = ok & ~atlas.image(hl["mask"])
    core = np.s_[10:-10, 10:-10]
    return {"mode": policy["mode"], "donor_share": float(ok[core].mean()),
            "light_share": float(ok[core][~spots[core]].mean()),
            "dark_share": float(ok[core][spots[core]].mean()),
            "after_highlights_share": float(final[core].mean())}


@pytest.mark.parametrize("name", COLOURS)
def test_patterned_colourway_keeps_both_phases(name):
    row = donor_row(name)
    assert row["donor_share"] > .9, row
    assert row["light_share"] > .9 and row["dark_share"] > .9, row
    assert row["after_highlights_share"] > .9, row


def test_general_wide_glare_interior_is_removed():
    atlas = Atlas((128, 128))
    img = np.full(atlas.shape + (3,), 40.)
    img[40:88] = 225.
    fit = S.fit_colour_policy(img, np.ones(atlas.shape, bool))
    result = HL.flag(atlas, np.ones(128**2, bool), img.reshape(-1, 3), np.zeros(128**2, int), {},
                     tx=16., colour_policies={0: fit})
    assert atlas.image(result["mask"])[40:88, 8:-8].all()


@pytest.mark.parametrize("name", ["grey", "white", "crystal"])
@pytest.mark.parametrize("module", [PM, PROOF])
def test_neutral_photo_pigment_is_not_calibration_glare(name, module):
    img, _ = pattern(name)
    pixels = img.reshape(-1, 3)
    assert module.achromatic_highlight(pixels).mean() < .01
    comp = module.level_compare(pixels, pixels, exclude_highlights=True,
                                ar_highlight=np.zeros(len(pixels), bool))
    assert comp["pass"], comp
    assert comp["photo"]["n"] == len(pixels)


@pytest.mark.parametrize("name", COLOURS)
def test_independent_contamination_instrument_accepts_reference_palette(name):
    img, _ = pattern(name)
    # Explicit reference support, never the tested render's own colours.
    assert PROOF.acetate_like(img, reference=img.reshape(-1, 3)).mean() > .99
    alien = np.full((8, 8, 3), (245, 30, 240), float)
    assert not PROOF.acetate_like(alien, reference=img.reshape(-1, 3)).any()


def test_part_palette_names_are_exact():
    assert PM.part_name_matches("Temple.001", {"Temple.001"})
    assert not PM.part_name_matches("Temple.001", {"Temple"})
    rgb = np.array([[[255, 0, 0], [0, 255, 0]]])
    palette = [{"name": "Temple", "rgb": [255, 0, 0]}, {"name": "Temple.001", "rgb": [0, 255, 0]}]
    assert PM.ar_frame_mask(rgb, palette, ["Temple"], erode=0).tolist() == [[True, False]]


def mirror_case():
    region = np.repeat([0, 1], 3000)
    cols = np.full((len(region), 3), 225.)
    tex = SimpleNamespace(region=region, part=region.copy(), P=np.c_[region * 100, region * 0, region * 0])
    samples = {"front.jpeg": SimpleNamespace(ok=region == 0, col=cols),
               "IMG_4521.jpeg": SimpleNamespace(ok=region == 1, col=cols)}
    return tex, cols, region, samples


def test_empty_mirror_gain_samples_never_enter_either_solve():
    tex, cols, label, samples = mirror_case()
    edges = GN.pair_gains(tex, samples, list(samples), label, mirror_region={0: 1, 1: 0})
    assert all("gain" not in e or (e["pairs"] >= GN.MIRROR_MIN and np.isfinite(e["gain"]).all()) for e in edges)
    grp = GN.group_gains(tex, np.ones(len(label), bool), label, cols, 0, mirror_region={0: 1, 1: 0})
    assert np.isfinite(grp["gain"]).all()
    assert not grp["mirrors"]


@pytest.mark.parametrize("src", [np.zeros((0, 3)), np.full((3, 3), np.nan)])
def test_invalid_median_gain_is_rejected(src):
    assert GN.median_gain(src, np.full((3, 3), 100.)) is None


def test_gain_support_uses_unambiguous_original_filenames():
    names = ["IMG_4521.jpeg", "IMG_4531.jpeg"]
    edge = {"a": names[0], "b": names[1], "kind": "boundary", "pairs": 1000, "gain": [1.1] * 3}
    chain = GN.solve_gains(names, np.array([0, 1]), [edge], names[0])
    warnings = " ".join(GN.support_warnings(chain, {}))
    assert all(name in warnings for name in names)


def test_quilt_without_candidate_refuses_before_boundary_join():
    image = np.full((80, 180, 3), (150, 90, 40), np.uint8)
    donors = np.zeros(image.shape[:2], bool)
    donors[:, :4] = True
    with pytest.raises(ValueError, match="donor windows"):
        FL.fill_call("quilt", image, ~donors, donors, 14, 123)


def test_quilt_changed_counts_original_hole_content_not_zero():
    image = np.full((80, 100, 3), (150, 90, 40), np.uint8)
    hole = np.zeros(image.shape[:2], bool)
    hole[30:50, 40:60] = True
    result = FL.fill_call("quilt", image, hole, ~hole, 4, 123)
    assert result["hole_changed"] == np.any(result["out"][hole] != image[hole], axis=-1).sum() == 0
    assert result["hole_filled"] == hole.sum()


def test_knockout_wider_than_origin_strip_clips_instead_of_wrapping():
    canvas = np.zeros((300, 600), bool)
    canvas[:64, :192] = True
    holes = dict(FL.knockout_holes(canvas, (0, 66, 0, 194), 16.))
    assert set(holes) == {"band2mm", "band4mm", "band8mm", "band16mm", "block8x5mm"}
    assert holes["band16mm"].sum() == 184 * 56
    assert holes["block8x5mm"].sum() == 129 * 56


def test_group_key_rejects_view_index_overflow():
    tex = SimpleNamespace(region=np.array([0, 1]), part=np.array([0, 1]), P=np.zeros((2, 3)))
    with pytest.raises(ValueError, match="view index"):
        GN.group_gains(tex, np.ones(2, bool), np.array([64, 0]), np.full((2, 3), 100.), 0)


def test_level_key_rejects_chart_index_overflow():
    tex = SimpleNamespace(region=np.array([0, 1]), chart=np.array([1024, 0]), ii=np.array([0, 0]),
                          jj=np.array([0, 1]), shape=(1, 2), index=np.array([[0, 1]]), part=np.array([0, 1]),
                          P=np.array([[0., 0., 0.], [10., 0., 0.]]))
    with pytest.raises(ValueError, match="chart index"):
        GN.level_field(tex, np.ones(2, bool), np.array([0, 0]), np.full((2, 3), 100.), 0, np.empty((0, 2), int), tx=16.)


def test_registered_mixed_label_remains_supported():
    tex = SimpleNamespace(region=np.array([0, 1]), chart=np.array([0, 1]), part=np.array([0, 1]),
                          P=np.zeros((2, 3)))
    painted, labels, colours = np.ones(2, bool), np.full(2, -2), np.full((2, 3), 100.)
    assert np.isfinite(GN.group_gains(tex, painted, labels, colours, 0)["gain"]).all()
    assert GN.level_field(tex, painted, labels, colours, 0)["nodes"] == 0


@pytest.mark.parametrize("module", [PM, PROOF])
def test_blue_hue_difference_is_shortest_circular_angle(module):
    from skimage.color import lab2rgb
    def pixels(h):
        lab = np.array([[50., 15 * np.cos(np.radians(h)), 15 * np.sin(np.radians(h))]])
        return np.repeat(lab2rgb(lab) * 255., 400, axis=0)
    comp = module.level_compare(pixels(222.4), pixels(248.8))
    assert comp["appearance"]["hue"]["p50_ar_minus_photo"] == pytest.approx(-26.4, abs=.05)
    assert not comp["appearance"]["criteria"]["hue_p50"]
    # A naturally purple reference is not foreign hue; adding unrelated hue still fails the same 2% limit.
    photo = pixels(330.)
    assert module.level_compare(photo, photo)["appearance"]["criteria"]["hue_magenta"]
    wrong = photo.copy()
    wrong[:40] = pixels(90.)[:40]
    assert not module.level_compare(wrong, photo)["appearance"]["criteria"]["hue_magenta"]


@pytest.mark.parametrize("module", [PM, PROOF])
def test_black_probe_reflections_still_excluded(module):
    colours = np.array([[120., 120., 120.], [30., 30., 30.]])
    assert module.achromatic_highlight(colours, known_black=True).tolist() == [True, False]


@pytest.mark.parametrize("module", [PM, PROOF])
def test_minority_neutral_phase_is_not_dropped(module):
    photo = np.array([(60., 90., 170.)] * 800 + [(235., 235., 235.)] * 200)
    assert not module.achromatic_highlight(photo).any()


@pytest.mark.parametrize("module", [PM, PROOF])
def test_rare_reference_colour_does_not_license_more_foreign_hue(module):
    from skimage.color import lab2rgb
    lightness = np.tile(np.linspace(20., 70., 40), 25)
    photo_hue, actual_hue = np.full(1000, 60.), np.full(1000, 60.)
    photo_hue[::40], actual_hue[::10] = 330., 330.
    def colour(h):
        return lab2rgb(np.c_[lightness, 30 * np.cos(np.radians(h)), 30 * np.sin(np.radians(h))]) * 255.
    photo, actual = colour(photo_hue), colour(actual_hue)
    result = module.level_compare(actual, photo)["appearance"]
    assert all(value for key, value in result["criteria"].items() if key != "hue_magenta")
    assert not result["criteria"]["hue_magenta"]
    assert result["hue"]["foreign_share_ar_minus_photo"] == pytest.approx(.075)


def test_unrelated_atlas_charts_do_not_create_neutral_glare():
    atlas = Atlas((64, 104))
    image = np.full((64, 104, 3), 40.)
    image[:, :32] = 235.
    painted = np.ones(atlas.shape, bool)
    painted[:, 32:40] = False
    chart = np.ones(atlas.shape, int)
    chart[:, :32] = 0
    chart[:, 32:40] = -1
    atlas.chart = chart.ravel()
    fit = S.fit_colour_policy(image, painted)
    result = HL.flag(atlas, painted.ravel(), image.reshape(-1, 3), np.zeros(64 * 104, int), {},
                     tx=16., thin=False, colour_policies={0: fit})
    assert not result["mask"].any()


def test_unregistered_witness_does_not_erase_neutral_pigment_phase():
    atlas = Atlas((128, 128))
    image = np.full((128, 128, 3), (60., 90., 170.))
    image[56:72] = 195.
    mask = np.ones(128**2, bool)
    policy = S.fit_colour_policy(image, np.ones(atlas.shape, bool))
    witness = [(1, mask, np.tile((60., 90., 170.), (128**2, 1)))]
    result = HL.flag(atlas, mask, image.reshape(-1, 3), np.zeros(128**2, int), {0: witness},
                     tx=16., colour_policies={0: policy})
    assert not atlas.image(result["mask"])[56:72].any()


def test_calibration_support_matches_explicit_posed_scene(monkeypatch):
    from glasses3d.tests.photoprint.test_photo_pose import posed_job
    from glasses3d.g3d_host.photoprint.ar_levels import Measurements, PHOTO_ERODE
    monkeypatch.setattr(MT, "hardware_faces", lambda job, parts, decals=None: np.zeros(len(job.F) + 1, np.uint8))
    job, view, _, moved = posed_job()
    measure = Measurements(job, {"arms": [job.mesh.names[1], job.mesh.names[2]]})
    occ = TX.OcclusionScene(job, [1, 2])
    fid = occ.scene(moved.V, pose=view.pose).render(view.camera, view.shape, 1)["face_id"]
    expected = ndimage.binary_erosion((fid >= 0) & np.isin(job.mesh.part[np.maximum(fid, 0)], [1, 2]),
                                     iterations=PHOTO_ERODE)
    expected &= ndimage.binary_erosion(view.matte, iterations=PHOTO_ERODE)
    actual = measure.photo_frame_mask(job, view.name, occ)
    assert np.array_equal(actual, expected)
    view.pose = None
    authored = measure.photo_frame_mask(job, view.name, occ)
    assert np.count_nonzero(expected != authored) > 200
