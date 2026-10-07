"""Small pigment-arm outliers must not carve holes into valid amber print.

Controls retain hardware, coherent contamination, neutral highlights and additive-white glints. No photo or
product coordinates enter the production rule.
"""
import numpy as np
from glasses3d.g3d_host.photoprint import metal as MT


def scene():
    image = np.full((100, 200, 3), (50, 35, 25), float)
    inside = np.ones(image.shape[:2], bool)
    return image, inside, np.zeros_like(inside)


def test_small_warm_colour_outliers_survive_but_white_glints_do_not():
    image, inside, hardware = scene()
    candidate = np.zeros_like(inside)
    image[40:43, 40:50] = (180, 145, 100)  # pigment rise: B/R increment 75/130, below achromatic light
    candidate[40:43, 40:50] = True
    image[40:43, 100:110] = (150, 135, 125)  # same spatial footprint, +100 in every channel
    candidate[40:43, 100:110] = True
    mask = MT.supported_pale(image, candidate, inside, hardware, 8.0)
    assert not mask[40:43, 40:50].any()
    assert mask[40:43, 100:110].all()
    assert not mask[~candidate].any()


def test_hardware_and_coherent_pale_areas_stay_excluded():
    image, inside, hardware = scene()
    image[30:50, 90:110] = (180, 145, 100)
    image[40:43, 20:30] = (180, 145, 100)
    candidate = image[:, :, 0] > 100
    hardware[40, 10] = True
    mask = MT.supported_pale(image, candidate, inside, hardware, 8.0)
    assert mask[candidate].all()  # large far patch, small near-hardware patch


def test_broad_neutral_patch_is_not_reintroduced():
    image, inside, hardware = scene()
    image[:] = (110, 108, 106)
    candidate = np.zeros_like(inside)
    candidate[40:43, 40:50] = True  # zero local contrast still exceeds the absolute neutral limit
    inside[41, 44] = False
    mask = MT.supported_pale(image, candidate, inside, hardware, 8.0)
    assert np.array_equal(mask, candidate & inside)


def test_same_physical_small_spot_at_two_resolutions():
    image, inside, hardware = scene()
    image[40:43, 40:50] = (180, 145, 100)
    image[40:43, 100:110] = (150, 135, 125)
    candidate = image[:, :, 0] > 100
    low = MT.supported_pale(image, candidate, inside, hardware, 8.0)
    expand = lambda x: np.repeat(np.repeat(x, 2, axis=0), 2, axis=1)
    high = MT.supported_pale(expand(image), expand(candidate), expand(inside), expand(hardware), 16.0)
    assert np.array_equal(high, expand(low))


def test_top_photo_pigment_seeds_are_reported_separately_from_confirmed_holes(ft1119_job):
    from glasses3d.g3d_host.photoprint import inputs as IN, texels as TX
    job = ft1119_job
    view = job.views['reference-04.png']
    occ = TX.OcclusionScene(job, job.part_ids(IN.FT1119_PARTS), TX.decal_geometry(job.sources['glb'], job.mesh.skipped))
    fid = occ.scene(job.mesh.V).render(view.camera, view.shape, 1)['face_id']
    holes = MT.donor_holes(view.image, view.matte, fid, occ.is_print, occ.is_hardware, job.ppm(view.name))
    # Frozen source-photo crop identified before the repair, containing two smooth amber patches.
    zone = np.s_[802:844, 370:600]
    candidate = MT.pale_mask(view.image, holes['arm'])
    assert candidate[zone].sum() > 100
    assert holes['pale_colour_only'][zone].sum() > holes['pale'][zone].sum()
    assert not (holes['pale'] & holes['pale_colour_only']).any()
    # Rejecting colour-only seeds must never weaken the independent physical hardware margin.
    assert holes['mask'][holes['hardware']].all()
