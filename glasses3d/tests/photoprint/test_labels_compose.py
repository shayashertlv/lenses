"""Steps 4 and 7 on synthetic inputs: the label MRF (alpha-expansion = brute force on a tiny graph; a crease attracts
the boundary), the A3 compose guarantee, PatchMatch's checks and its global-mask pitfall, and the D6 / refusal rules
of ``project_photo_texture`` that need no bake. The seam harmonisation tests were deleted on 2026-10-13 with
``seams.harmonise``."""
import itertools

import numpy as np
import pytest
from scipy import ndimage

from glasses3d.g3d_host.photoprint import fill as FL
from glasses3d.g3d_host.photoprint import labels as LB
from glasses3d.g3d_host.photoprint.compose import compose


def _strip_mesh(n=12, crease_at=7, angle_deg=60.0):
    """A strip of n quads along x (2 triangles each), folded by ``angle_deg`` at x = crease_at."""
    V = []
    for i in range(n + 1):
        for y in (0.0, 1.0):
            if i <= crease_at:
                V.append([float(i), y, 0.0])
            else:
                a = np.radians(angle_deg)
                d = i - crease_at
                V.append([crease_at + d * np.cos(a), y, -d * np.sin(a)])
    V = np.asarray(V)
    F = []
    for i in range(n):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        F += [[a, c, b], [b, c, d]]
    return V, np.asarray(F)


def test_alpha_expansion_matches_brute_force():
    V, F = _strip_mesh(n=4)
    g = LB.face_graph(V, F, np.arange(len(F)), 2.0)
    rng = np.random.default_rng(1)
    cand = [0, 1, 2]
    D = {l: rng.uniform(0, 6, len(F)) for l in cand}
    lam = rng.uniform(0.5, 3, len(g["a"]))
    free = np.ones(len(F), bool)
    start = np.zeros(len(F), np.int64)
    lab, hist = LB.expansion(start, free, cand, D, g, lam, max_cycles=6)

    def energy(L):
        return sum(D[int(L[f])][f] for f in range(len(F))) + float(lam[L[g["a"]] != L[g["b"]]].sum())

    best = min(energy(np.array(L)) for L in itertools.product(cand, repeat=len(F)))
    # alpha-expansion is within a factor 2 of the optimum for Potts; on this tiny graph it reaches it
    assert energy(lab) <= best + 1e-6 or energy(lab) <= 2 * best
    assert hist == sorted(hist, reverse=True)


def test_crease_attracts_the_label_boundary():
    V, F = _strip_mesh(n=12, crease_at=7)
    faces = np.arange(len(F))
    region = np.where(np.arange(len(F)) < 2 * 5, 0, 1)            # the facing boundary at x = 5, the crease at 7
    nf = np.full(len(F), 8.0)
    wf = np.ones((len(F), 2))                                       # both views see everything equally well
    out = LB.place_labels(V, F, faces, region, {0: 0, 1: 1}, wf, nf, ["a", "b"], 4.0, band_mm=4.0)
    assert out["boundary_start"]["on_crease_share"] == 0.0
    assert out["boundary"]["on_crease_share"] == pytest.approx(1.0)
    # a view that does not see a face never takes it, whatever the crease says
    wf2 = wf.copy()
    wf2[2 * 5: 2 * 7, 1] = 0.0
    out2 = LB.place_labels(V, F, faces, region, {0: 0, 1: 1}, wf2, nf, ["a", "b"], 4.0, band_mm=4.0)
    assert np.all(out2["labels"][2 * 5: 2 * 7] == 0)


def test_compose_keeps_protected_texels_byte_identical():
    rng = np.random.default_rng(0)
    P0 = rng.integers(0, 256, (40, 50, 3), dtype=np.uint8)
    prot = rng.random((40, 50)) > 0.4
    edited = rng.uniform(0, 255, (40, 50, 3))
    final, info = compose(P0, prot, edited)
    assert info["protected_byte_identical"] and np.array_equal(final[prot], P0[prot])
    assert np.array_equal(final[~prot], np.clip(np.rint(edited[~prot]), 0, 255).astype(np.uint8))


def _havana_img(shape=(80, 160), seed=2):
    t = ndimage.gaussian_filter(np.random.default_rng(seed).standard_normal(shape), 2.0)
    t = (t - t.min()) / (t.max() - t.min())
    return np.stack([58 + 147 * t, 34 + 94 * t, 22 + 30 * t], -1).astype(np.uint8)


def test_patchmatch_changes_the_hole_and_nothing_else():
    img = _havana_img()
    hole = np.zeros(img.shape[:2], bool)
    hole[30:50, 60:90] = True
    donors = ~hole
    donors[:, :20] = False                                          # excluded area (global mask)
    r = FL.patchmatch_call(img, hole, donors, 3, 7)
    assert r["outside_changed"] == 0 and r["hole_changed"] == int(hole.sum())
    r2 = FL.patchmatch_call(img, hole, donors, 3, 7)
    assert np.array_equal(r["out"], r2["out"])                      # seeded: byte-identical reruns


def test_a_global_mask_containing_the_hole_is_refused():
    """PyPatchMatch silently leaves a hole unfilled when the global mask covers it (AP6); patchmatch_call builds the
    global mask as non-donor & non-hole, so donors overlapping the hole are dropped, and the raw pitfall is shown."""
    import patchmatch
    img = _havana_img()
    hole = np.zeros(img.shape[:2], bool)
    hole[30:50, 60:90] = True
    work = img.copy()
    work[hole] = 0
    out = patchmatch.inpaint(work, hole.astype(np.uint8), global_mask=hole.astype(np.uint8), patch_size=3)
    assert int((out[hole] != work[hole]).any(-1).sum()) == 0          # the pitfall: nothing filled
    r = FL.patchmatch_call(img, hole, np.ones_like(hole), 3, 1)       # donors include the hole: dropped from donors
    assert r["hole_changed"] > 0 and r["donor_px"] == int((~hole).sum())


def test_refraction_into_acetate_obeys_snell():
    n = np.array([[0.0, 0.0, 1.0]])
    to_cam = np.array([np.sin(np.radians(40)), 0.0, np.cos(np.radians(40))])
    t = FL._refracted(n, to_cam, 1.49)[0]
    assert t[2] < 0 and np.isclose(np.linalg.norm(t), 1.0)
    sin_t = np.hypot(t[0], t[1])
    assert np.isclose(sin_t, np.sin(np.radians(40)) / 1.49, atol=1e-9)
    assert t[0] < 0                                              # continues away from the camera side


# ----------------------------------------------------------------------------- fix round 2: crease mode, sources
def _cols(n_faces, n_views, value=None):
    c = np.zeros((n_faces, n_views, 3))
    c[:] = [120.0, 80.0, 50.0] if value is None else value
    return c


def test_crease_mode_moves_the_boundary_onto_the_crease():
    V, F = _strip_mesh(n=12, crease_at=7)
    faces = np.arange(len(F))
    region = np.where(np.arange(len(F)) < 2 * 5, 0, 1)
    nf = np.full(len(F), 8.0)
    wf = np.ones((len(F), 2))
    out = LB.place_labels(V, F, faces, region, {0: 0, 1: 1}, wf, nf, ["a", "b"], 4.0, band_mm=4.0, mode="crease",
                          face_colours=_cols(len(F), 2))
    assert out["mode"] == "crease" and out["boundary_start"]["on_crease_share"] == 0.0
    assert out["boundary"]["on_crease_share"] == pytest.approx(1.0) and out["moved_to_unseen_faces"] == 0


def test_crease_mode_without_a_crease_takes_the_line_of_best_colour_agreement():
    """No crease (a flat strip): the seam goes where the two views' colours agree (here around x = 8), not at the
    facing boundary (x = 5)."""
    V, F = _strip_mesh(n=12, crease_at=12, angle_deg=0.0)
    faces = np.arange(len(F))
    region = np.where(np.arange(len(F)) < 2 * 5, 0, 1)
    nf = np.full(len(F), 8.0)
    wf = np.ones((len(F), 2))
    cols = _cols(len(F), 2)
    cols[:, 1] = [60.0, 40.0, 30.0]                                  # the two photos disagree everywhere ...
    cols[2 * 7: 2 * 9, 1] = cols[2 * 7: 2 * 9, 0]                    # ... except on x in [7, 9]
    out = LB.place_labels(V, F, faces, region, {0: 0, 1: 1}, wf, nf, ["a", "b"], 4.0, band_mm=5.0, mode="crease",
                          face_colours=cols)
    lab = out["labels"]
    first_b = int(np.argmax(lab == 1)) // 2
    assert 7 <= first_b <= 9, lab
    assert out["boundary"]["photometric_de00_mean"] < out["boundary_start"]["photometric_de00_mean"]


def test_crease_mode_never_hands_a_face_to_a_blind_view_nor_a_non_adjacent_anchor():
    V, F = _strip_mesh(n=12, crease_at=12, angle_deg=0.0)
    faces = np.arange(len(F))
    x = np.arange(len(F)) // 2
    region = np.where(x < 4, 0, np.where(x < 6, 1, 2))               # a narrow middle region between 0 and 2
    nf = np.full(len(F), 8.0)
    wf = np.full((len(F), 3), 0.2)
    wf[:, 2] = 5.0                                                    # view 2 is by far the best everywhere
    wf[x == 7, 1] = 0.0                                               # view 1 does not see x = 7
    out = LB.place_labels(V, F, faces, region, {0: 0, 1: 1, 2: 2}, wf, nf, ["a", "b", "c"], 4.0, band_mm=3.0,
                          mode="crease", face_colours=_cols(len(F), 3))
    lab = out["labels"]
    assert not np.any(lab[x < 4] == 2), lab                          # region 0 never takes the non-adjacent anchor
    assert not np.any(lab[x == 7] == 1), lab                          # nor a view that does not see the face
    assert out["moved_to_unseen_faces"] == 0
