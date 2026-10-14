"""Geometry-bound donor colour policy: non-warm pigment survives every filter; warm behaviour is preserved."""
from itertools import permutations
import json
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import ndimage

from glasses3d.g3d_host.camera import Camera
from glasses3d.g3d_host.photoprint import highlights as HL
from glasses3d.g3d_host.photoprint import metal as MT
from glasses3d.g3d_host.photoprint import signals as S
from glasses3d.g3d_host.photoprint import texels as TX
from glasses3d.tests.photoprint.test_step4_fixes import _arm_photo


def policy(mode="general"):
    return {"schema": S.COLOUR_POLICY_SCHEMA, "mode": mode}


def holes(image, *, fid=None, ppm=4.0, arm=None):
    shape = image.shape[:2]
    if fid is None:
        fid = np.zeros(shape, np.int64)
    return MT.donor_holes(image, np.ones(shape, bool), fid, np.array([True, False, False]),
                          np.array([0, 2, 0], np.uint8), ppm, arm=arm)


def test_policy_uses_only_geometry_support_and_records_its_identity():
    img = np.full((180, 240, 3), (80, 120, 170), np.uint8)
    support = np.zeros(img.shape[:2], bool)
    support[60:120, 80:160] = True
    first = S.fit_colour_policy(img, support)
    img[~support] = (200, 150, 70)
    second = S.fit_colour_policy(img, support)
    assert first["mode"] == second["mode"] == "general"
    assert first["warm_share"] == second["warm_share"] == 0
    assert first["support_sha256"] == second["support_sha256"]
    assert first["image_pixels_sha256"] != second["image_pixels_sha256"]
    assert first["samples"] == int(support.sum())
    assert json.loads(json.dumps(second, allow_nan=False)) == second
    assert S.fit_colour_policy(img, support) == second


def test_policy_training_excludes_known_hardware_and_other_parts():
    shape = (180, 300)
    pr = np.ones(shape, bool)
    other = np.zeros(shape, bool)
    other[:, :20] = True
    hw = np.zeros(shape, bool)
    hw[70:90, 100:120] = True
    support = MT.colour_support(pr, other, pr, 4, hw)
    assert not support[ndimage.distance_transform_edt(~hw) <= MT.SAMPLE_AWAY_HARDWARE_MM * 4].any()
    assert not support[ndimage.distance_transform_edt(~other) <= MT.SAMPLE_AWAY_MM * 4].any()
    assert support[100:140, 180:250].all()


@pytest.mark.parametrize("rgb,mode", [((160, 100, 60), "warm"), ((60, 100, 160), "general"),
                                      ((60, 160, 100), "general"), ((125, 125, 125), "general"),
                                      ((195, 195, 195), "general")])
def test_evidence_selects_colour_family_without_product_identity(rgb, mode):
    img = np.full((80, 80, 3), rgb, np.uint8)
    fit = S.fit_colour_policy(img, np.ones(img.shape[:2], bool))
    assert fit["mode"] == mode
    assert S.acetate_mask(img, fit).all()


def test_warm_dominant_neutral_mix_reports_unresolved_ambiguity():
    img = np.full((100, 160, 3), (160, 100, 60), np.uint8)
    img[:, 80:] = 150
    fit = S.fit_colour_policy(img, np.ones(img.shape[:2], bool))
    assert fit["mode"] == "warm"
    assert fit["neutral_share"] == .5
    assert fit["limitations"] and "mixed neutral pigment" in fit["limitations"][0]
    assert np.array_equal(S.acetate_mask(img, fit), S.acetate_mask(img))


def test_achromatic_glare_does_not_change_a_supported_warm_colour_family():
    img = _arm_photo()
    img[:, :100] = 210
    pr = np.ones(img.shape[:2], bool)
    out = holes(img)
    fit = out["arm"]["colour_policy"]
    assert fit["mode"] == "warm" and fit["neutral_share"] > .5
    old = holes(img, arm=MT.pigment_arm(img, pr, np.zeros_like(pr), pr, 4))
    assert np.array_equal(out["mask"], old["mask"])
    assert out["mask"][10:-10, 10:90].all()


def test_insufficient_support_does_not_invent_a_pigment_arm():
    img = np.full((40, 40, 3), (100, 100, 180), np.uint8)
    support = np.zeros(img.shape[:2], bool)
    fit = S.fit_colour_policy(img, support)
    assert fit["mode"] == "general" and fit["samples"] == 0
    arm = MT.pigment_arm(img, support, support, support, 4, colour_policy=fit)
    assert arm["fitted"] == 0
    assert not MT.pale_mask(img, arm).any()


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_channel_permuted_patterns_remain_donors(order):
    img = _arm_photo()[..., order]
    out = holes(img)
    selected = out["arm"]["colour_policy"]
    core = np.s_[10:-10, 10:-10]
    valid = S.valid_mask(img, np.ones(img.shape[:2], bool), selected)
    assert (valid & ~out["mask"])[core].mean() > .95
    assert selected["mode"] == ("warm" if order == (0, 1, 2) else "general")


def test_warm_auto_policy_keeps_all_legacy_donor_masks_exact():
    img = _arm_photo()
    pr = np.ones(img.shape[:2], bool)
    old_arm = MT.pigment_arm(img, pr, np.zeros_like(pr), pr, 4)
    old = holes(img, arm=old_arm)
    new = holes(img)
    assert new["arm"]["colour_policy"]["mode"] == "warm"
    assert {k: v for k, v in new["arm"].items() if k != "colour_policy"} == old_arm
    for key in old:
        if isinstance(old[key], np.ndarray):
            assert np.array_equal(old[key], new[key]), key
        elif key != "arm":
            assert old[key] == new[key], key


@pytest.mark.parametrize("photo", [f"reference-{i:02d}.png" for i in range(1, 7)])
def test_accepted_ft_photo_keeps_exact_legacy_donor_masks(ft1119_job, photo):
    from glasses3d.g3d_host.photoprint import inputs as IN
    job = ft1119_job
    view = job.views[photo]
    occ = TX.OcclusionScene(job, job.part_ids(IN.FT1119_PARTS), TX.decal_geometry(job.sources["glb"], job.mesh.skipped))
    fid = occ.scene(job.mesh.V).render(view.camera, view.shape, 1)["face_id"]
    pr = (fid >= 0) & occ.is_print[fid]
    other = (fid >= 0) & ~occ.is_print[fid]
    ppm = job.ppm(photo)
    old_arm = MT.pigment_arm(view.image, pr, other, view.matte, ppm)
    old = MT.donor_holes(view.image, view.matte, fid, occ.is_print, occ.is_hardware, ppm, arm=old_arm)
    new = MT.donor_holes(view.image, view.matte, fid, occ.is_print, occ.is_hardware, ppm)
    assert new["arm"]["colour_policy"]["mode"] == "warm", new["arm"]["colour_policy"]
    assert {k: v for k, v in new["arm"].items() if k != "colour_policy"} == old_arm
    for key in old:
        if isinstance(old[key], np.ndarray):
            assert np.array_equal(old[key], new[key]), (photo, key)
        elif key != "arm":
            assert old[key] == new[key], (photo, key)
    assert np.array_equal(S.valid_mask(view.image, view.matte, new["arm"]["colour_policy"]), view.valid)


def test_general_pigment_threshold_has_no_absolute_neutral_rejection():
    yy, xx = np.indices((120, 180))
    lum = 100 + 55 * xx / xx.max()
    img = np.repeat(lum[..., None], 3, axis=-1).astype(np.uint8)
    out = holes(img)
    assert out["arm"]["colour_policy"]["mode"] == "general"
    assert min(out["arm"]["threshold"]) > 1
    assert not out["pale"].any()
    assert not out["mask"][8:-8, 8:-8].any()


@pytest.mark.parametrize("rgb", [(60, 100, 160), (60, 160, 100), (140, 140, 140)])
def test_glints_and_hardware_still_make_holes_on_nonwarm_print(rgb):
    img = np.full((180, 300, 3), rgb, np.uint8)
    img[70:73, 180:240] += 70  # additive white, same physical scale on each pigment
    fid = np.zeros(img.shape[:2], np.int64)
    fid[80:90, 50:60] = 1
    img[80:90, 50:60] = (187, 149, 116)
    out = holes(img, fid=fid)
    assert out["bright_lines"][70:73, 185:235].all()
    assert out["mask"][70:73, 185:235].all()
    assert out["hardware"][80:90, 50:60].all()
    assert out["mask"][out["hardware"]].all()
    assert not out["mask"][120:160, 160:260].any()


@pytest.mark.parametrize("channel", [0, 1, 2])
def test_coloured_thin_pigment_is_not_additive_white(channel):
    img = np.full((80, 180, 3), 50., float)
    img[30:33, 30:130, channel] += 100
    inside = np.ones(img.shape[:2], bool)
    out = MT.thin_lines(img, inside, np.zeros_like(inside), 4, colour_policy=policy())
    assert not out["bright"].any()
    img[30:33, 30:130] = 150
    out = MT.thin_lines(img, inside, np.zeros_like(inside), 4, colour_policy=policy())
    assert out["bright"][30:33, 35:125].all()


def test_dark_blue_blob_is_not_misclassified_as_neutral_rivet():
    img = np.full((120, 220, 3), (100, 100, 140), float)
    yy, xx = np.indices(img.shape[:2])
    coloured = (yy - 50)**2 + (xx - 50)**2 <= 3**2
    rivet = (yy - 50)**2 + (xx - 150)**2 <= 3**2
    img[coloured] = (5, 8, 35)
    img[rivet] = (8, 8, 8)
    inside = np.ones(img.shape[:2], bool)
    out = MT.dark_blobs(img, inside, inside, 4, colour_policy=policy())
    assert not out["mask"][coloured].any()
    assert out["mask"][rivet].all()
    assert len(out["blobs"]) == 1


def test_neutral_pigment_does_not_become_a_foreign_blob_or_footprint_hole():
    img = np.full((100, 180, 3), 120, np.uint8)
    img[30:70, 50:130] = 200
    matte = np.ones(img.shape[:2], bool)
    assert not TX.foreign_blobs(img, matte, matte, 4, colour_policy=policy())["mask"].any()
    out = TX.footprint_holes(img, matte, 4, colour_policy=policy())
    assert not out[10:-10, 10:-10].any()
    img[45:55, 85:95] = 255
    assert TX.footprint_holes(img, matte, 4, colour_policy=policy())[45:55, 85:95].all()


class Atlas:
    def __init__(self, shape):
        self.shape = shape
        self.ii, self.jj = np.indices(shape).reshape(2, -1)

    def image(self, values, fill=0, dtype=None):
        values = np.asarray(values)
        out = np.full(self.shape + values.shape[1:], fill, dtype=dtype or values.dtype)
        out[self.ii, self.jj] = values
        return out


@pytest.mark.parametrize("channel", [0, 1, 2])
def test_painted_highlight_stage_uses_the_same_policy(channel):
    atlas = Atlas((60, 120))
    img = np.full(atlas.shape + (3,), 50., float)
    img[20:22, 20:100, channel] = 150
    cols = img.reshape(-1, 3)
    painted = np.ones(len(cols), bool)
    labels = np.zeros(len(cols), int)
    out = HL.flag(atlas, painted, cols, labels, {}, tx=8, colour_policies={0: policy()})
    assert not out["mask"].any()
    img[20:22, 20:100] = 150
    out = HL.flag(atlas, painted, cols, labels, {}, tx=8, colour_policies={0: policy()})
    assert atlas.image(out["mask"])[20:22, 25:95].all()


def test_broad_bright_grey_phase_survives_painted_highlight_stage():
    atlas = Atlas((60, 120))
    cols = np.full((60 * 120, 3), 200.)
    painted = np.ones(len(cols), bool)
    labels = np.zeros(len(cols), int)
    out = HL.flag(atlas, painted, cols, labels, {}, tx=8, colour_policies={0: policy()})
    assert not out["mask"].any()
    warm = HL.flag(atlas, painted, cols, labels, {}, tx=8, colour_policies={0: policy("warm")})
    assert warm["mask"].all()  # historical warm semantics remain literal


@pytest.mark.parametrize("kind", ["blue", "green", "grey", "bright_grey"])
def test_view_samples_uses_geometry_bound_masks_end_to_end(kind, monkeypatch):
    shape = (100, 160)
    yy, xx = np.indices(shape)
    wave = .5 + .25 * np.sin(xx / 20) + .25 * np.sin(yy / 20)
    if kind in ("grey", "bright_grey"):
        img = np.repeat(((100 if kind == "grey" else 180) + 30 * wave)[..., None], 3, -1)
    else:
        img = np.stack([60 + 20 * wave, 90 + 40 * wave, 140 + 40 * wave], -1)
        if kind == "green":
            img = img[..., [0, 2, 1]]
    img = img.astype(np.uint8)
    # Only visibility/projection is stubbed; every production colour/metal/footprint mask and the bicubic
    # sampling run normally. The view deliberately has no legacy cached `valid` property.
    uv = np.stack([xx[8:-8, 8:-8].ravel(), yy[8:-8, 8:-8].ravel()], -1)
    n = len(uv)
    mesh = SimpleNamespace(V=np.zeros((3, 3)), F=np.array([[0, 1, 2]]), part=np.array([0]), names=["Printable"])
    view = SimpleNamespace(image=img, matte=np.ones(shape, bool), shape=shape,
                           camera=Camera(0, 0, 0, 0, 1, 0, 0))
    job = SimpleNamespace(mesh=mesh, frame=None, views={"photo": view}, ppm=lambda *a: 4.)
    rendered = SimpleNamespace(render=lambda *a: {"face_id": np.zeros(shape, int)})
    occ = SimpleNamespace(scene=lambda *a: rendered, is_print=np.array([True, False]),
                          is_hardware=np.array([0, 0], np.uint8))
    tex = SimpleNamespace(face=np.zeros(n, int), bary=np.tile([1., 0., 0.], (n, 1)))
    class Rows:
        face, bary = tex.face, tex.bary
        def __len__(self):
            return n
    monkeypatch.setattr(TX, "points", lambda *a: (np.zeros((n, 3)), np.tile([0., 0., 1.], (n, 1))))
    monkeypatch.setattr(TX.T, "visible_points", lambda *a, **kw: (np.ones(n, bool), uv))
    monkeypatch.setattr(TX.raster, "camera_basis", lambda *a: np.eye(3))
    monkeypatch.setattr(TX, "ppm_local", lambda *a: np.full(n, 4.))
    result = TX.view_samples(job, Rows(), "photo", occ)
    assert result.arm["colour_policy"]["mode"] == "general"
    assert result.ok.all(), result.reasons
    assert np.allclose(result.col, img[uv[:, 1], uv[:, 0]], atol=1e-8)
    assert result.reasons["not_valid_pixel"] == result.reasons["foreign_blob"] == result.reasons["footprint"] == 0


@pytest.mark.parametrize("bad", [{}, {"schema": S.COLOUR_POLICY_SCHEMA, "mode": "off"}, False])
def test_invalid_policy_never_silently_disables_filters(bad):
    with pytest.raises(ValueError, match="colour policy"):
        S.acetate_mask(np.full((2, 2, 3), 100), bad)
