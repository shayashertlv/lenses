"""Steps 2-7 on FT1119 (single-source mode, WP5_DESIGN §6, §8 A1-A5): one bake at 8 texels/mm shared by the bake
tests, A2 at the production density 16 texels/mm; every proof number comes from the independent instrument
(tests/instruments/print_metrics.py via tests/photoprint/printproof.py), the bake's own report only for its checks.
Sides by x sign: q001's Temple_Left is the -x temple (the wearer's right)."""
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from glasses3d.g3d_host.photoprint import inputs as IN
from glasses3d.g3d_host.photoprint import project as PR
from glasses3d.g3d_host.photoprint.compose import CLASS_CODES
from glasses3d.tests.photoprint import printproof as PP

pytestmark = pytest.mark.slow
TX = 8.0
TH = dict(print_reg_mm=0.8, edge_mean_mm=1.0, edge_p90_mm=2.5)


@pytest.fixture(scope="module")
def outdir():
    d = Path(tempfile.mkdtemp(prefix="wp5_print_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def bake(ft1119_job, outdir):
    return PR.project_photo_texture(ft1119_job, IN.FT1119_PARTS, out_dir=outdir, tx_per_mm=TX, workers=4, log=None,
                                    **TH)


def test_outputs_written(bake, outdir):
    for name in ("print.png", "classes.png", "source.png", "tier.png", "layout.json", "registration.json",
                 "report.json", "report_stable.json"):
        assert (outdir / name).is_file(), name
    im = Image.open(outdir / "print.png")
    assert im.mode == "RGB" and max(im.size) <= 4096
    assert im.size == (bake["layout"].width, bake["layout"].height)


def test_print_png_declares_srgb(bake, outdir):
    """print.png carries the PNG sRGB chunk (its colours are the photos' sRGB values, as glTF expects)."""
    im = Image.open(outdir / "print.png")
    assert "srgb" in im.info


def test_area_table_partitions_the_area(bake):
    """report.json's area_table (build step 3 run): every region in exactly one of the four groups, the groups sum to
    the total, each group's painted split sums to its area, minor end faces go by their anchors, and the PatchMatch
    share per part equals the class split."""
    at = bake["report"]["area_table"]
    st = bake["report"]["statuses"]
    assert set(at["groups"]) == set(PR.AREA_GROUPS)
    listed = [n.replace(" (minor end face)", "") for g in at["groups"].values() for n in g["regions"]]
    assert sorted(listed) == sorted(st)
    assert abs(sum(g["mm2"] for g in at["groups"].values()) - at["total_mm2"]) < 0.5
    assert abs(sum(g["share"] for g in at["groups"].values()) - 1.0) < 2e-3
    for g in at["groups"].values():
        if g["mm2"] > 0:
            assert abs(sum(g["painted_mm2"].values()) - g["mm2"]) < 0.5
    for name, s in st.items():
        grp = next(k for k, g in at["groups"].items() if any(n.startswith(name) for n in g["regions"]))
        assert grp == PR.area_group(s)
        if s["anchor"] is None:
            assert grp == "unseen-filled"
    assert at["groups"]["registered"]["mm2"] == 0.0           # nothing registers on FT1119
    for p, v in at["parts"].items():
        assert at["patchmatch_share_per_part"][p] == v["painted_share"].get("patchmatch", 0.0)
    assert 0.0 <= at["mirrored_share"] <= 1.0


def test_a1_in_the_bake(bake):
    assert bake["report"]["checks"]["A1"]["pass"]


def test_a3_compose(bake):
    a3 = bake["report"]["checks"]["A3"]
    assert a3["protected_byte_identical"] and a3["patchmatch_outside_changed_total"] == 0
    assert a3["patchmatch_every_hole_changed"] and a3["calls"]
    prot = bake["protected"]
    fin = bake["final_unpadded"]
    assert prot.sum() > 0 and np.array_equal(fin[prot], bake["P0"][prot])


def test_a3_read_back_independently(ft1119_job, bake, outdir):
    """Fix round 1: print.png read back, at every protected photo texel, against the photo colour sampled by the proof's
    own rasteriser / projection / spline times the bake's declared gains."""
    r = PP.readback_a3(ft1119_job, bake, outdir)
    assert r["graded"] > 10000 and r["uncovered_by_own_raster"] == 0
    assert r["pass"], r


def test_a5_graded_off_the_seam(ft1119_job, bake, outdir):
    """Fix round 1: A5 by the instrument's own seam samples, 1 and 2 texels inside each chart, against the texture's
    within-chart change at the same separation. KNOWN STATE (a tripwire, update it deliberately): A5 FAILS on FT1119
    at 1 texel (8 texels/mm, fix round 2: p95 10.6 vs 9.5; at 2 texels 14.0 vs 16.2 passes). Fix round 2 breakdown:
    the samples over the p95 are almost all chart seams with a PatchMatch fill on BOTH sides (independent fills of two
    charts), not label seams (photos of two views on the two sides: 9 of about 10,000 samples). The same-3D-point
    agreement after harmonisation is set by construction and only reported; the criterion's status is recorded."""
    r = PP.seams(ft1119_job, bake, outdir)
    o = r["offsets"]
    assert o[1]["samples"] > 2000 and o[2]["samples"] > 1000 and o[1]["within_samples"] > 1000
    assert r["image"] == "print.png read back"
    assert not r["pass"] and o[1]["across_p95"] > o[1]["within_p95"], r
    kinds = r["breakdown"][1]["kinds"]
    assert kinds["fill both"]["share_of_over_p95"] > 0.5, kinds
    assert kinds.get("photo, two views", {"samples": 0})["samples"] < 0.01 * o[1]["samples"]
    assert "NOT yet recorded in PROGRESS" in r["criterion_decision"]
    # harmonisation writes the seam texels: before it they disagree, after it they agree by construction
    assert r["diagnostic_tool_pairs_pre_harmonise"]["de00_p95"] > 3.0
    assert r["diagnostic_tool_pairs_final_by_construction"]["de00_p50"] < 1.0


def test_anchors_by_x_sign_and_no_tier1(bake):
    st = bake["report"]["statuses"]
    expect = {"Brow_Left[-x]/front": "reference-05.png", "Brow_Right[+x]/front": "reference-05.png",
              "Brow_Left[-x]/back": "reference-02.png", "Brow_Left[-x]/top": "reference-04.png",
              "Temple_Left[-x]/outer": "reference-03.png", "Temple_Left[-x]/inner": "reference-06.png",
              "Temple_Right[+x]/outer": "reference-06.png"}
    for name, view in expect.items():
        assert st[name]["anchor"] == view, (name, st[name])
    assert st["Temple_Right[+x]/inner"]["status"] == "unseen"
    assert not any(s["status"] == "registered" for s in st.values())          # nothing registered on FT1119
    assert int((bake["classes"] == CLASS_CODES["photo_tier1"]).sum()) == 0     # never mixing unregistered views


def test_single_source_from_the_written_outputs(ft1119_job, bake, outdir):
    """Fix round 2 (not true by construction): source.png read back; every photo texel's view is its region's anchor
    or an ADJACENT region's anchor (registration.json's anchors, adjacency from the mesh), and no layout face carries
    photo texels of two views."""
    a = PP.audit_outputs(ft1119_job, bake, outdir)
    assert a["photo_texels"] > 10000
    assert a["outside_own_or_adjacent_anchor"] == 0 and a["faces_with_two_views"] == 0 and a["pass"], a
    lab = bake["report"]["checks"]["labels"]
    assert lab["label_moves"] == "crease" and lab["foreign_view_photo_texels"] == 0 and lab["moved_to_unseen_faces"] == 0
    assert bake["report"]["mode"].startswith("single-source, one view per face")


def test_harmonise_never_averages_two_views_in_the_bake(bake):
    h = bake["report"]["checks"]["seams"]["harmonise"]
    assert h["multi_view_means"] == 0


def test_label_boundaries_move_onto_creases_or_better_colour_agreement(bake):
    """Fix round 2: on the temples (which have creases) the boundary's crease share does not fall; on the brows (no
    crease near their boundaries) the seam moves to where the two photos agree better."""
    per = bake["report"]["checks"]["labels"]["boundary_per_part"]
    for p in ("Temple_Left", "Temple_Right"):
        assert per[p]["final"]["on_crease_share"] >= per[p]["start"]["on_crease_share"], per[p]
    for p in ("Brow_Left", "Brow_Right"):
        assert per[p]["start"]["on_crease_mm"] == 0.0                         # no crease to sit on
        assert per[p]["final"]["photometric_de00_mean"] < per[p]["start"]["photometric_de00_mean"], per[p]


def test_cover_rule_measures_what_the_photos_see_and_pairs_mirror_partners(bake):
    """§9 (fix round 2): the share the painting views SEE (before text / highlight masks) of the AR-visible area a worn
    frame shows (temple inner and bottom excluded), pooled over mirror partners. KNOWN STATE at 8 texels/mm: every
    part is textured (the +x temple's photographed outer face is no longer blanked; fix round 1 measured 29.5 % on the
    old measure)."""
    rep_ = bake["report"]
    ps = rep_["parts_summary"]
    assert rep_["below_min_cover"] == [] and rep_["texture_parts"] == list(IN.FT1119_PARTS)
    for a, b in (("Brow_Left", "Brow_Right"), ("Temple_Left", "Temple_Right")):
        assert ps[a]["cover_rule"] == ps[b]["cover_rule"] and ps[a]["cover_rule"]["members"] == sorted([a, b])
        assert ps[a]["cover_rule"]["pooled_source_seen_share"] >= 0.30
    tr = ps["Temple_Right"]
    assert tr["worn_hidden_share_of_ar"] > 0.3 and tr["source_seen_share"] > tr["source_seen_share_ar_all"]
    assert not bake["no_texture"].any()


def test_cover_rule_decides_pairs_together():
    seen_den = {"A": (10, 100), "B": (90, 100), "C": (5, 100)}
    cover, blank = PR.cover_rule(seen_den, {"A": "B", "B": "A"}, 0.30)
    assert blank == ["C"] and cover["A"]["pooled_source_seen_share"] == cover["B"]["pooled_source_seen_share"] == 0.5
    cover, blank = PR.cover_rule({"A": (10, 100), "B": (20, 100)}, {"A": "B", "B": "A"}, 0.30)
    assert blank == ["A", "B"]


def test_below_min_cover_parts_get_no_texture(ft1119_job, outdir, bake):
    """The no-texture path (flat grey, class no_texture, source 0, never protected, left out of texture_parts), forced
    with ``min_cover`` = 0.99 on a small bake (radii given: no knockout)."""
    r = PR.project_photo_texture(ft1119_job, IN.FT1119_PARTS, out_dir=outdir / "strict", tx_per_mm=4.0, workers=4,
                                 log=None, min_cover=0.99, radii_mm={p: 0.3 for p in IN.FT1119_PARTS}, **TH)
    rep_ = r["report"]
    assert rep_["below_min_cover"] == list(IN.FT1119_PARTS) and rep_["texture_parts"] == []
    blank = r["no_texture"]
    k = r["classes"]
    assert blank.any() and np.all(k[blank] == CLASS_CODES["no_texture"]) and np.all(r["source"][blank] == 0)
    fin = r["final_unpadded"]
    tex = r["tex"]
    assert np.all(fin[tex.ii[blank], tex.jj[blank]] == PR.NO_TEXTURE_RGB)
    assert not r["protected"][tex.ii[blank], tex.jj[blank]].any()


def test_mirror_only_where_no_view_sees_and_never_text(bake):
    """Fix round 1: mirror candidates are surfaces NO view sees at donor angles (not merely texels without a valid
    admitted donor); highlight holes and unpainted texels of a seen surface go to PatchMatch."""
    m = bake["mirrored"]
    assert m.sum() > 0 and not (m & bake["text_mask"]).any()
    assert not (m & bake["seen_geom"]).any() and not (m & bake["seen_any"]).any() and not (m & bake["photo"]).any()
    mi = bake["report"]["mirror"]
    assert mi["mirrored_on_seen_surface"] == 0 and mi["unpainted_seen_texels_to_patchmatch"] > 0
    # fix round 2: where mirror fill lands on surfaces a photo shows obliquely is reported, and warned above 25 %
    assert 0 <= mi["mirrored_on_grazing_photographed_surface"] <= mi["texels"]
    warned = any(w.startswith("mirror fill:") for w in bake["report"]["warnings"])
    assert warned == (mi["mirrored_on_grazing_share"] > PR.MIRROR_GRAZE_WARN)


def test_foreign_medallion_is_a_hole_and_reported(bake):
    """Fix round 2: photo 03 shows a gold medallion on the -x temple tip that q001 lacks; its bright interior AND its
    bezel (dilated 0.75 mm) are a hole for 03 (no photo texel), and the report tells Astra."""
    blobs = bake["report"]["foreign_blobs"]["reference-03.png"]
    gold = [b for b in blobs if b.get("part") == "Temple_Left" and b["kind"] == "gold" and b["area_mm2"] > 15]
    assert gold, blobs
    assert bake["samples"]["reference-03.png"].reasons["foreign_blob"] > 0
    assert any(w.startswith("foreign blobs") for w in bake["report"]["warnings"])


def test_text_is_filled_from_the_decal_by_default(bake):
    t = bake["report"]["text"]
    assert "Left_inner_brand_print.001" in t["decals"] and t["filled"] and not t["keep_photo_text"]
    k = bake["classes"]
    assert int((k == CLASS_CODES["text_fill"]).sum()) > 0
    # fix round 1: lettering seen THROUGH the acetate (06 shows the +x temple's inner print through its outer face;
    # q001 has no decal there, so the cast hits the mirrored decal) is masked too and reported for Astra
    assert t["see_through_by_view"]["reference-06.png"] > 0 and t["see_through_hits_on_mirrored_decal"] > 0
    assert t["see_through_px"] > 0 and bake["see_through"].any() and not (bake["see_through"] & bake["photo"]).any()
    assert any(w.startswith("see-through text") for w in bake["report"]["warnings"])


def test_gain_support_and_saturation_are_reported(bake):
    g = bake["report"]["gains"]
    assert all("support" in r for r in g["per_view"].values())
    assert "clip_evidence" in g and g["clip_evidence"]["overlap_edges"] > 0
    sat = [x for x in g["across_boundaries"]["groups"] if x["saturated"]]
    assert bool(sat) == any(w.startswith("gains:") and "stage-2 groups" in w for w in bake["report"]["warnings"])


def test_refusals(ft1119_job, outdir):
    with pytest.raises(PR.RefusedError, match="thresholds missing"):
        PR.project_photo_texture(ft1119_job, IN.FT1119_PARTS, out_dir=outdir, tx_per_mm=TX, print_reg_mm=0.8)
    with pytest.raises(PR.RefusedError, match="lens"):
        PR.project_photo_texture(ft1119_job, ["Lens_Left"], out_dir=outdir, tx_per_mm=TX, **TH)
    with pytest.raises(PR.RefusedError, match="metallic"):
        PR.project_photo_texture(ft1119_job, ["Eyewire_Left"], out_dir=outdir, tx_per_mm=TX, **TH)


A4_KNOWN_AT_8 = {"Brow_Left": False, "Brow_Right": True, "Temple_Left": False, "Temple_Right": False}


def test_a4_fill_knockout_known_verdicts(bake):
    """A4 graded by the instrument, fix round 2: 8 seeded holes per part drawn BY THE PROOF over every single-view area
    (never the tool's hole functions), medians with their spread, the mean-colour limit calibrated by the donor ring.
    The verdicts are PINNED so that a change cannot hide (8 texels/mm: the three failing parts fail on fine-scale
    energy, |log energy| 0.19-0.35 at 0.25-1 mm: PatchMatch is smoother than real print, design risk 9). Update
    A4_KNOWN_AT_8 deliberately."""
    rows = {p: PP.knockout(bake, p) for p in IN.FT1119_PARTS}
    for p, r in rows.items():
        assert r["hole_source"].startswith("printproof.knockout") and r["a4"]["holes"] == 8 and not r["skipped"], p
        assert all(h["outside_changed"] == 0 and h["hole_changed"] > 0 for h in r["holes"]), p
        assert all(np.isfinite(h["log_energy_0.25_1mm"]) for h in r["holes"])
        centres = {tuple(h["centre_atlas_px"]) for h in r["holes"]}
        assert len(centres) == 8                                       # eight distinct places, not one
        assert r["a4"]["spread"]["mean_colour_de00"]["n"] == 8
    assert {p: r["a4"]["pass"] for p, r in rows.items()} == A4_KNOWN_AT_8, {p: r["a4"] for p, r in rows.items()}


@pytest.mark.parametrize("view", ["reference-01.png", "reference-02.png", "reference-03.png", "reference-04.png",
                                  "reference-05.png", "reference-06.png"])
def test_a2_round_trip_at_16_per_mm(ft1119_job, view):
    r = PP.round_trip(ft1119_job, view, 16.0, IN.FT1119_PARTS)
    assert r["pixels"] > 1000 and r["pass"], r
