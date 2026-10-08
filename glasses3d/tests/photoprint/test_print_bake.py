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
    return PR.project_photo_texture(ft1119_job, IN.FT1119_PARTS, out_dir=outdir, tx_per_mm=TX, workers=None, log=None,
                                    compare_fill=("patchmatch",), **TH)


def test_outputs_written(bake, outdir):
    for name in ("print.png", "classes.png", "source.png", "tier.png", "layout.json", "registration.json",
                 "report.json", "report_stable.json"):
        assert (outdir / name).is_file(), name
    im = Image.open(outdir / "print.png")
    assert im.mode == "RGB" and max(im.size) <= 4096
    assert im.size == (bake["layout"].width, bake["layout"].height)


def test_print_manifest_written(bake, outdir):
    """Step 4 (Blender side): the bake writes print_manifest.json, what g3d.attach_print reads: the print, its atlas
    and density, and the print geometry's identities, computed from the job's own mesh (not recomputed later)."""
    import json
    from glasses3d.g3d import printuv as PU
    man = json.loads((outdir / "print_manifest.json").read_text(encoding="utf-8"))
    lay = bake["layout"]
    assert bake["files"]["manifest"] == str(outdir / "print_manifest.json") and "manifest_error" not in bake["files"]
    assert man["identity_recomputed"] is False and man["layer"] == "g3d_print" and man["parts"] == list(IN.FT1119_PARTS)
    assert (man["width"], man["height"]) == (lay.width, lay.height) and man["tx_per_mm"] == lay.tx_per_mm
    assert man["charts_sha"] == PU.glbmesh.charts_sha(lay.charts, lay.width, lay.height)
    V, F, part, names = PU.glbmesh.mesh_arrays(PU.glbmesh.read_glb(man["glb"]))
    assert man["geometry_sha"] == PU.glbmesh.geometry_sha(V, F, part, names, man["parts"])
    assert bake["report"]["print_uv"]["geometry_sha"] == man["geometry_sha"]


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
    fin = bake.get("final_radiance", bake["final_unpadded"])      # correction round: before the AR level, if any
    assert prot.sum() > 0 and np.array_equal(fin[prot], bake["P0"][prot])


def test_a3_read_back_independently(ft1119_job, bake, outdir):
    """Fix round 1: print.png read back, at every protected photo texel, against the photo colour sampled by the proof's
    own rasteriser / projection / spline times the bake's declared gains."""
    r = PP.readback_a3(ft1119_job, bake, outdir)
    assert r["graded"] > 10000 and r["uncovered_by_own_raster"] == 0
    assert r["pass"], r


A5_KNOWN_AT_8 = True


def test_a5_graded_beyond_the_reflection_band(ft1119_job, bake, outdir):
    """A5 by the instrument's own seam samples against the texture's within-chart change at the same separation.
    History: before step 4 A5 FAILED at 1 texel (8 texels/mm: p95 10.6 vs 9.5; fill on BOTH sides of chart seams). Step
    4 fix 4 (``seams.reflect_seams``: the losing side's fill copies the winner reflected across the seam, fully to 1.5
    texels, fading to 6) made the 1-2 texel reading pass, but BY CONSTRUCTION (step 4 fix round 1, review): inside
    the band the two sides are the same texels. So the verdict is now read beyond the band, at 7 and 8 texels (sample
    points kept on their face's chart), and 1, 2, 4 and 6 texels are reported, not graded. KNOWN STATE (a tripwire,
    update it deliberately): ``A5_KNOWN_AT_8``. Poisson now replaces reflection/hard harmonisation, but the frozen
    7/8 offsets remain. Same-3D-point agreement stays diagnostic and is no longer imposed by construction."""
    r = PP.seams(ft1119_job, bake, outdir)
    o = r["offsets"]
    assert bake["report"]["chart_seam_reflection"]["band_texels"] == 0.0 and r["reflection_band_texels"] == 0.0
    assert r["graded_offsets"] == [7, 8] and set(r["criteria"]) == {"p95_at_7_texels", "p95_at_8_texels"}
    assert all(not o[k]["graded"] for k in (1, 2, 4, 6)) and all(o[k]["graded"] for k in (7, 8))
    for k in (7, 8):
        assert o[k]["samples"] > 2000 and o[k]["within_samples"] > 1000, o[k]
        assert o[k]["across_max"] >= o[k]["across_p95"]                 # p95 is not the worst case; both reported
    assert r["image"] == "print.png read back"
    assert r["pass"] == A5_KNOWN_AT_8, r
    kinds = r["breakdown"][1]["kinds"]
    assert kinds.get("photo, two views", {"samples": 0})["samples"] < 0.01 * o[1]["samples"]
    assert set(r["breakdown"]) >= {7, 8}
    assert "frozen minimum offsets 7/8" in r["criterion_decision"]
    assert bake["report"]["chart_seam_reflection"]["texels_changed"] == 0
    # Poisson has no reflection band and does not impose same-point equality.
    assert bake["report"]["checks"]["seams"]["harmonise"]["method"] == "unscreened_poisson"
    assert "diagnostic_tool_pairs_final_by_construction" in r


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


def test_poisson_preserves_observed_photo_bytes_in_the_bake(bake):
    h = bake["report"]["checks"]["seams"]["harmonise"]
    assert h["method"] == "unscreened_poisson" and h["protected_changed"] == 0
    protected = bake["protected"]
    np.testing.assert_array_equal(bake["final_radiance"][protected], bake["P0"][protected])


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
    r = PR.project_photo_texture(ft1119_job, IN.FT1119_PARTS, out_dir=outdir / "strict", tx_per_mm=4.0, workers=None,
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
    # step 6: lettering shows only through LIGHT acetate; photo 03's cast lands on the dark phase of the -x temple
    # (no lettering visible in the photo), so none of its hits is masked and its print stays photo
    assert t["see_through_light_only"] is not None
    cast3 = t["see_through_cast_by_view"]["reference-03.png"]
    assert cast3 > 0 and t["see_through_by_view"]["reference-03.png"] <= 0.02 * cast3     # 0 of 1,970 at 16 tx/mm
    assert t["see_through_dark_acetate_by_view"]["reference-03.png"] >= 0.98 * cast3


def test_gain_support_and_saturation_are_reported(bake):
    g = bake["report"]["gains"]
    assert all("support" in r for r in g["per_view"].values())
    assert "clip_evidence" in g and g["clip_evidence"]["overlap_edges"] > 0
    sat = [x for x in g["across_boundaries"]["groups"] if x["saturated"]]
    assert bool(sat) == any(w.startswith("gains:") and "stage-2 groups" in w for w in bake["report"]["warnings"])


def test_level_field_clip_is_reported_like_stage2(bake):
    """Step 4 fix round 1 (review: the stage-2 saturation moved into the stage-3 field, which reached its own clip, log
    2): the report counts the texels and nodes at the field's clip and the texels beyond x1.5 / x1/1.5, recomputed here
    from the per-texel gain, and warns exactly when any texel sits at the clip."""
    from glasses3d.g3d_host.photoprint import gains as GN
    lf = bake["report"]["gains"]["level_field"]
    g = np.asarray(bake["level_gain"], float)
    lg = np.abs(np.log(g))
    assert int((lg >= GN.LEVEL_CLIP - 1e-6).any(1).sum()) == lf["texels_at_clip"]
    assert int((lg >= np.log(GN.LEVEL_LARGE) - 1e-12).any(1).sum()) >= lf["texels_gain_beyond_x1.5"] > 0
    warned = [w for w in bake["report"]["warnings"] if w.startswith("gains: the stage-3 levelling field sits at its clip")]
    assert bool(warned) == (lf["texels_at_clip"] > 0)
    assert lf["clip_log"] == GN.LEVEL_CLIP and lf["max_abs_log"] <= GN.LEVEL_CLIP + 1e-12


def test_small_enclosed_holes_are_filled_locally(bake):
    """Step 4 fix round 1 (review: quilting left staircase-edged dark fragments in honey blotches): hole components up
    to 2 mm2 whose ring is mostly photo texels are filled by PatchMatch (0.3 mm patches) in local tiles, from the part's
    photo texels; each tile call changes nothing outside its holes; the rest of the holes go to the production filler."""
    from glasses3d.g3d_host.photoprint import fill as FL
    se = bake["report"]["fill"]["small_enclosed"]
    assert se["filler"] == "patchmatch" and se["max_mm2"] == FL.SMALL_HOLE_MM2
    assert sum(v["texels"] for v in se["per_part"].values()) > 0 and se["tiles"] > 0
    calls = [c for c in bake["report"]["checks"]["A3"]["calls"] if c["class"].endswith("_small_enclosed")]
    assert len(calls) == se["tiles"] and all(c["outside_changed"] == 0 and c["hole_changed"] > 0 for c in calls)
    assert all(v["texels"] <= v["components"] * FL.SMALL_HOLE_MM2 * TX * TX for v in se["per_part"].values())


def test_gains_step4_mirror_same_part_and_levelling(ft1119_job, bake):
    """Step 4 fix 3: every view is connected to the reference (03 and 02 were not: their only edges were rejected by
    the old [0.5, 2] clip, so the -x temple's outer face from 03 kept gain 1 and stage 2 pushed it to its clip);
    stage-1 mirror edges exist; stage-2 edges never cross parts; mirrored groups of one view share one gain; the
    levelling field is reported and written."""
    g = bake["report"]["gains"]
    assert all(r["connected"] for r in g["per_view"].values()), g["per_view"]
    assert any(e["kind"] == "mirror" and "gain" in e for e in g["edges"])
    ab = g["across_boundaries"]
    tex = bake["tex"]
    regions = {r.rid: r for r in bake["regions"]}
    for e in ab["edges"]:
        assert regions[e["a"] // 64].part_name == regions[e["b"] // 64].part_name, e
    same = [m for m in ab["mirrors"] if m["kind"] == "mirror_same_view"]
    gains = {(x["region"], x["view_index"]): x["gain"] for x in ab["groups"]}
    assert same
    for m in same:
        ga, gb = gains[(m["a"] // 64, m["a"] % 64)], gains[(m["b"] // 64, m["b"] % 64)]
        assert np.allclose(ga, gb, rtol=0.05), (m, ga, gb)
    lf = g["level_field"]
    assert lf["nodes"] > 100 and lf["boundary_pairs"] > 0
    assert bake["level_gain"].shape == (len(tex), 3)


def test_metal_and_highlight_holes_are_reported(bake):
    """Step 4 fix 1: per photo the pigment arm and the reasons (pale colour, hardware margin, edge halo, enclosed) are
    in the report; photo 02 (the rear view, with the gold hinges and pad arms) loses donors to the colour model and to
    the hardware margin, and the hardware parts are q001's small metal parts."""
    m = bake["report"]["metal"]
    r02 = m["reference-02.png"]["reasons"]
    assert r02["pale"] > 0 and r02["hardware_margin"] > 0 and r02["metal_or_highlight"] >= r02["pale"]
    assert m["reference-02.png"]["arm"]["fitted"] >= 2
    assert all(v["reasons"]["metal_or_highlight"] is not None for v in m.values())


def test_refusals(ft1119_job, outdir):
    with pytest.raises(PR.RefusedError, match="thresholds missing"):
        PR.project_photo_texture(ft1119_job, IN.FT1119_PARTS, out_dir=outdir, tx_per_mm=TX, print_reg_mm=0.8)
    with pytest.raises(PR.RefusedError, match="lens"):
        PR.project_photo_texture(ft1119_job, ["Lens_Left"], out_dir=outdir, tx_per_mm=TX, **TH)
    with pytest.raises(PR.RefusedError, match="metallic"):
        PR.project_photo_texture(ft1119_job, ["Eyewire_Left"], out_dir=outdir, tx_per_mm=TX, **TH)


A4_KNOWN_AT_8 = {"Brow_Left": False, "Brow_Right": False, "Temple_Left": False, "Temple_Right": False}
# correction round (2026-10-06): quilting passed A4 on the +x temple (q001 Temple_Right) at 8 texels/mm. Fix round 1
# (2026-10-07, re-pinned deliberately): it FAILS again (fine-band log energy and seam). Measured at 8 texels/mm with
# each change alone: the pass needed BOTH the correction round's plain footprint and its flatten (plain + flatten on:
# pass; plain + flatten off: fail; 'edges' + flatten on: fail), i.e. the verdict sits on a knife edge of which photo
# texels the holes are drawn in and their colours. At 16 texels/mm (the production bake, data/wp5/ft1119_bake/bake.json)
# all four parts fail in both rounds.
# 7ac7c8f (PyPatchMatch at its knockout radii) passed Brow_Right here; step 4's quilting fails it: a REGRESSION of A4,
# taken for the visual reason in report['fill']['choice'] (see A4_FILLERS_KNOWN_AT_8 for PyPatchMatch on the same holes)
A4_AT_8_7AC7C8F = {"Brow_Left": False, "Brow_Right": True, "Temple_Left": False, "Temple_Right": False}


def test_a4_fill_knockout_known_verdicts(bake):
    """A4 graded by the instrument, fix round 2: 8 seeded holes per part drawn BY THE PROOF over every single-view area
    (never the tool's hole functions), medians with their spread, the mean-colour limit calibrated by the donor ring.
    The verdicts are PINNED so that a change cannot hide. Step 4: the filler under test is the bake's own (block
    quilting) at its own knockout radius. KNOWN STATE at 8 texels/mm (fix round 1 of the correction round): all four
    parts FAIL (fine-band log energy; seam ratios in ``test_a4_each_filler_at_its_own_radius``); at
    7ac7c8f Brow_Right passed (``A4_AT_8_7AC7C8F``): a regression. Every hole also gets a REAL-vs-real floor (step 4 fix round 1: a nearby same-shaped real region on the
    hole's own chart and facing class, same photo, magnification within x/: 1.25), reported with its spread as a
    DIAGNOSTIC: it never changes or excuses the verdict. With more holes in the photo texels (step 4's metal / line
    holes) a hole may find no room (reported). Update A4_KNOWN_AT_8 deliberately."""
    rows = {p: PP.knockout(bake, p) for p in IN.FT1119_PARTS}
    for p, r in rows.items():
        assert r["hole_source"].startswith("printproof.knockout") and r["method"] == "quilt", p
        assert r["radius_mm"] == bake["radii"][p]
        assert r["a4"]["holes"] + len(r["skipped"]) == 8 and r["a4"]["holes"] >= 6, (p, r["skipped"])
        assert all(sk["reason"] for sk in r["skipped"])
        assert all(h["outside_changed"] == 0 and h["hole_changed"] > 0 for h in r["holes"]), p
        assert all(np.isfinite(h["log_energy_0.25_1mm"]) for h in r["holes"])
        centres = {tuple(h["centre_atlas_px"]) for h in r["holes"]}
        assert len(centres) == r["a4"]["holes"]                        # distinct places, not one
        assert r["a4"]["spread"]["mean_colour_de00"]["n"] == r["a4"]["holes"]
        fl = r["a4"]["real_vs_real_floor"]
        assert fl["holes"] >= 3 and fl["role"].startswith("diagnostic") and fl["spread"]["log_energy_0.25_1mm"]["n"]
        assert "log_energy_within_floor" not in r["a4"]
        for h in r["holes"]:
            nb = (h.get("real_vs_real") or {}).get("neighbour")
            if nb is not None and nb["mag_ratio"] is not None:
                assert 1 / 1.3 <= nb["mag_ratio"] <= 1.3, nb                # restricted to similar magnification
    assert {p: r["a4"]["pass"] for p, r in rows.items()} == A4_KNOWN_AT_8, {p: r["a4"] for p, r in rows.items()}


A4_FILLERS_KNOWN_AT_8 = {"quilt": {"Brow_Left": False, "Brow_Right": False, "Temple_Left": False, "Temple_Right": False},
                         "patchmatch": {"Brow_Left": False, "Brow_Right": False, "Temple_Left": False,
                                        "Temple_Right": False}}
# brow-top repair (2026-10-07, re-pinned deliberately): keeping unsupported colour-only pigment changes the donor
# support and knockout holes. PyPatchMatch now also FAILS Brow_Left: a recorded A4 regression, not a passing proof.
# The production quilting filler remains four failures. Frozen A4 criteria and filler selection are unchanged.
# step 6 (2026-10-07, re-pinned deliberately): PyPatchMatch now passes the -x brow (q001 Brow_Left) and fails the +x
# temple (Temple_Right) at 8 texels/mm; quilting (the production filler) still fails all four. The step-6 donor rules
# changed which photo texels the proof's holes are drawn in (the -x brow front keeps its honey band; thin light lines
# and photo 03's see-through band changed the temples' photo texels): the same knife edge as the earlier re-pins
# correction round (2026-10-06): quilting passed the +x temple and PyPatchMatch also the -x brow (q001 Brow_Left) at
# 8 texels/mm. Fix round 1 (2026-10-07, re-pinned deliberately): only PyPatchMatch on the +x temple passes; the two
# correction-round passes needed both its plain footprint and its flatten (see A4_KNOWN_AT_8)


def test_a4_each_filler_at_its_own_radius(bake):
    """Step 4 fix round 1 (review: the step-4 comparison ran PyPatchMatch at quilting's 0.9 mm): each filler at the
    radius ITS OWN knockout chose in this bake (``compare_fill``; PyPatchMatch from 0.3 / 0.5 / 0.9 mm), on the same
    A4 holes. KNOWN STATE at 8 texels/mm after the brow-top mask repair: both fillers fail all four parts (history
    above). The production filler stays quilting for the VISUAL reason recorded in
    report['fill']['choice'] (the owner saw PatchMatch's wood grain along the strips): a choice that costs A4.
    Update A4_FILLERS_KNOWN_AT_8 deliberately."""
    from glasses3d.g3d_host.photoprint import fill as FL
    rb = bake["radii_by_method"]
    assert set(rb) == {"quilt", "patchmatch"} and rb["quilt"] == bake["radii"]
    assert all(rb["quilt"][p] in FL.QUILT_RADII_MM and rb["patchmatch"][p] in FL.RADII_MM for p in IN.FT1119_PARTS)
    bm = bake["report"]["fill"]["by_method"]
    assert set(bm) == {"quilt", "patchmatch"} and bm["patchmatch"]["radii_mm"] == rb["patchmatch"]
    assert "VISUAL" in bake["report"]["fill"]["choice"]
    got = {}
    for m in ("quilt", "patchmatch"):
        got[m] = {}
        for p in IN.FT1119_PARTS:
            r = PP.knockout(bake, p, method=m, radius_mm=rb[m][p])
            assert r["method"] == m and r["radius_mm"] == rb[m][p]
            assert all(h["outside_changed"] == 0 and h["hole_changed"] > 0 for h in r["holes"])
            got[m][p] = r["a4"]["pass"]
    assert got == A4_FILLERS_KNOWN_AT_8, got


@pytest.mark.parametrize("view", ["reference-01.png", "reference-02.png", "reference-03.png", "reference-04.png",
                                  "reference-05.png", "reference-06.png"])
def test_a2_round_trip_at_16_per_mm(ft1119_job, view):
    r = PP.round_trip(ft1119_job, view, 16.0, IN.FT1119_PARTS)
    assert r["pixels"] > 1000 and r["pass"], r
