"""WP6 design checklist items not covered by the other test_temple_* files (plan/WP6_DESIGN.md): the §3 warnings
W_FLAT_CREASE and W_ARM_NORMAL_SPREAD; §5 "only views ... registered <= 1 mm at the temple" for the crease verdicts
(and R2 never passing on no usable view); §5 the GLB check's chart from g3d_temple extras; §7 the fit's UNOBSERVED
list; and the synthetic photos' crease contrast
against the real Prada side views (the regression behind the 2026-10-06 relief-photo failures: a light that put
every synthetic crease below the frozen 3 L* step)."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple import fixtures as FX
from glasses3d.g3d_host import relief_check as RC
from glasses3d.g3d_host import relief_fit as RF
from glasses3d.g3d_host import relief_rules as RR
from glasses3d.g3d_host import relief_trace as RT
from glasses3d.g3d_host import relief_views as RV
from glasses3d.g3d_host.camera import Camera


# --------------------------------------------------------------------------------------------- §3 warnings
def test_flat_crease_warning_names_the_crease():
    tb = build_temple(FX.pyramid(r_c=0.3, r_h=0.05))
    flat = [w for w in tb.warnings if w["code"] == "W_FLAT_CREASE"]
    assert flat and all(w["deg"] < 2.0 and len(w["regions"]) == 2 for w in flat)
    assert not any(w["code"] == "W_FLAT_CREASE" for w in build_temple(FX.pyramid()).warnings)


def test_arm_normal_spread_warning():
    s = FX.base_spec()
    s["thickness"] = [[0.0, 5.0], [65.0, 5.0], [80.0, 5.0], [82.0, 1.6], [136.5, 1.6]]   # a 3.4 mm drop over 2 mm
    w = [w for w in build_temple(s).warnings if w["code"] == "W_ARM_NORMAL_SPREAD"]
    assert w and w[0]["deg"] > 30.0
    assert not any(w["code"] == "W_ARM_NORMAL_SPREAD" for w in build_temple(FX.base_spec()).warnings)


# --------------------------------------------------------------------------------------------- synthetic photos
@pytest.fixture(scope="module")
def synth():
    return RF.synthetic_setup(FX.prada_default())


def _guide(sj, key, side, nodes):
    fit = {"camera": sj.true_cams[key], "theta": sj.truth_theta[key]}
    q = RF.model_crease_px(sj, sj.truth.build, fit, side, *nodes)
    c = q.mean(0)
    d = np.linalg.svd(q - c)[2][0]
    t = (q - c) @ d
    return np.array([c + d * t.min(), c + d * t.max()]) + [[1.5, -2.0], [-1.0, 2.0]]


def test_synthetic_side_views_show_every_crease_like_the_real_photos(synth):
    """The real Prada side views show AB, AC and BC in both 05 and 01 at 4.1-6.0 L* (proof P2); the synthetic photos
    must too, at least at the frozen step threshold, or the crease instrument is tested on photos harder than real."""
    sj = synth
    for key, side in (("05", 1), ("01", -1)):
        L = RC.lightness(sj.photo(key))
        for cname, nodes in (("AB", ("A", "B")), ("AC", ("A", "C")), ("BC", ("B", "C"))):
            ln = RT.refine_line(L, *_guide(sj, key, side, nodes))
            assert ln is not None and ln["rms_px"] < 1.0, (key, cname, ln)
            assert abs(ln["step_L"]) >= RR.STEP_MIN_L, (key, cname, ln["step_L"])
    assert RF.SYNTH_LIGHT[0] == 0.0                      # no left / right component: 05 and 01 are mirror views


# --------------------------------------------------------------------------------------------- §5 usability
def test_temple_registration_reads_the_side_outline(synth):
    sj = synth
    reg = RC.temple_registration_mm(sj, "05", RV.render_model(sj, sj.truth, "05", sj.true_cams["05"],
                                                              sj.truth_theta["05"], points=True), sj.true_cams["05"], 1)
    assert reg["registration_mm"] == pytest.approx(0.0, abs=0.05)
    low = FX.prada_spec(**RF.prada_base_params(top=[[d, y - 1.5] for d, y in FX.TOP]))    # top edge 1.5 mm low
    m = RV.Model.from_spec(sj, low, "lowered top")
    reg = RC.temple_registration_mm(sj, "05", RV.render_model(sj, m, "05", sj.true_cams["05"], sj.truth_theta["05"],
                                                              points=True), sj.true_cams["05"], 1)
    assert reg["registration_mm"] > RR.VIEW_MAX_REG_MM and reg["bottom_mm"] < 0.05


def test_unregistered_views_are_not_usable_and_r2_never_passes_on_none(synth):
    sj = synth
    segs = {}
    for key, near in (("05", 1), ("01", -1)):
        cam, th = sj.true_cams[key], sj.truth_theta[key]
        r = RV.render_model(sj, sj.truth, key, cam, th, points=True, normals=True)
        ok = RC.photo_crease_segments(sj, key, r, cam, th, near, registration_mm=0.1)
        bad = RC.photo_crease_segments(sj, key, r, cam, th, near, registration_mm=1.5)
        assert ok["usable"] and ok["registration_mm"] == 0.1
        assert not bad["usable"] and bad["segments"] == ok["segments"]    # still listed, only not graded
        segs[key] = bad
    r2 = RC.r2_verdict(segs, [], segs["05"]["segments"])
    assert not r2["pass"] and r2["usable_views"] == 0


# --------------------------------------------------------------------------------------------- §5 what Astra gets
def test_crease_crop_colours_segments_by_verdict(synth, tmp_path):
    sj = synth
    slab = RV.Model.from_spec(sj, RF.slab_spec(**RF.prada_base_params()), "slab")
    cyan = {}
    for lab, m, want in (("truth", sj.truth, "OK"), ("slab", slab, "FLAG")):
        cam, th = sj.true_cams["05"], sj.truth_theta["05"]
        r = RV.render_model(sj, m, "05", cam, th, points=True, normals=True)
        seg = RC.photo_crease_segments(sj, "05", r, cam, th, 1, registration_mm=0.0)
        img = RC.crease_crop(sj, "05", r, seg, 1, tmp_path / f"{lab}.png", label=lab)
        assert (tmp_path / f"{lab}.png").exists() and img.ndim == 3
        has = lambda rgb: bool(np.all(img == np.array(rgb, np.uint8), axis=-1).any())
        assert has(RC.VERDICT_RGB[want]) and all(not has(RC.VERDICT_RGB[v]) for v in RC.VERDICT_RGB if v != want)
        cyan[lab] = int(np.all(img == np.array(RC.MODEL_CREASE_RGB, np.uint8), axis=-1).sum())
    assert cyan["truth"] > 1.5 * cyan["slab"] > 0          # both show the top-strip edge; only the truth its facets


def test_relief_sheet_sections_at_the_photo_nodes(tmp_path):
    spec = FX.prada_default()
    rs = RC.relief_sheet(spec, tmp_path / "sheet.png", x={"r_C": 3.0, "rise": -0.25}, sigma={"r_C": 0.1,
                         "rise": float("inf")}, front_bound={"clear": True}, unobserved=[{"parameter": "F4 tilt"}])
    st = {s["node"]: s for s in rs["stations"]}
    assert {"A", "B", "C"} <= set(st) and (tmp_path / "sheet.png").exists()
    tb = build_temple(spec)
    for n in ("A", "B", "C"):
        assert st[n]["r"] == pytest.approx(tb.spec.nodes[n][2])
        assert st[n]["x_out_max"] >= st[n]["x_base"] - 1e-9          # the section reaches at least the base surface
    assert st["C"]["x_out_max"] > st["C"]["x_base"] + 2.0             # C stands 3 mm proud: its section shows it


# --------------------------------------------------------------------------------------------- §5 GLB chart
def _write_glb(path, parts):
    """A minimal GLB: parts = [(name, W mm, triangles, node extras)], positions in metres, uint32 indices."""
    import json
    import struct
    blob, acc, views, meshes, nodes = b"", [], [], [], []
    for i, (name, W, T, ex) in enumerate(parts):
        P = (np.asarray(W, np.float64) / 1000.0).astype(np.float32)
        I = np.asarray(T, np.uint32).reshape(-1)
        for arr, kind in ((P, "VEC3"), (I, "SCALAR")):
            b = arr.tobytes()
            views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(b)})
            blob += b + b"\0" * ((-len(b)) % 4)
            a = {"bufferView": len(views) - 1, "componentType": 5126 if kind == "VEC3" else 5125,
                 "count": int(len(arr)), "type": kind}
            if kind == "VEC3":
                a["min"], a["max"] = P.min(0).tolist(), P.max(0).tolist()
            acc.append(a)
        meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": len(acc) - 2},
                                                     "indices": len(acc) - 1, "mode": 4}]})
        nodes.append({"name": name, "mesh": i, "extras": ex})
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}], "nodes": nodes,
           "meshes": meshes, "accessors": acc, "bufferViews": views, "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(doc).encode("utf-8")
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    path.write_bytes(b"glTF" + struct.pack("<II", 2, total) + struct.pack("<II", len(js), 0x4E4F534A) + js
                     + struct.pack("<II", len(blob), 0x004E4942) + blob)


def test_glb_check_charts_a_tool_temple_from_its_extras(tmp_path):
    """With g3d_temple extras the GLB check reads the temple in the tool's own chart (hinge, opening, zone end, facet
    planes), so tool receipt and check agree even for an opened temple; without them it falls back to the world chart
    at opening 0."""
    import json
    from glasses3d.g3d.temple import report
    spec = FX.prada_default()
    spec["hinge"]["opening_deg"] = 6.0
    tb = build_temple(spec)
    rec = json.dumps({"canonical_spec": tb.spec.canonical, "spec_sha256": tb.spec.sha256})
    parts = []
    for s in (1, -1):
        W, _, T = tb.world(s)
        parts.append((f"temple {s:+d}", W, T, {"partRole": "temple", "g3d_temple": rec}))
    glb = tmp_path / "opened.glb"
    _write_glb(glb, parts)
    t = RC.glb_temples(glb)
    for s in (1, -1):
        assert t[s]["chart"] == "g3d_temple extras" and t[s]["zone_end"] == spec["zone_end_d"]
        assert np.allclose(t[s]["V"], tb.V, atol=1e-4)            # float32 metres: the tool's local frame back
    rep = RC.check_relief(glb, photo_creases=4)
    geo = report.geometry_numbers(tb, photo_creases=4)
    for side in ("+x", "-x"):
        assert rep["temples"][side]["single_plane_share"] == pytest.approx(geo["single_plane_share"], abs=2e-3)
    plain = tmp_path / "plain.glb"
    _write_glb(plain, [(n, W, T, {"partRole": "temple"}) for n, W, T, _ in parts])
    assert RC.glb_temples(plain, hinge_z=-10.6)[1]["chart"].startswith("world")


# --------------------------------------------------------------------------------------------- §7 UNOBSERVED
def test_fit_lists_unobserved_parameters():
    fam = RF.prada_family(RF.prada_base_params())
    sig = {n: 0.1 for n in fam.names}
    sig["k2"] = float("inf")
    un = RF.unobserved(fam, sig)
    names = [u["parameter"] for u in un]
    assert "k2" in names and "x_in0" not in names
    f4 = next(u for u in un if u["parameter"].startswith("F4 tilt"))
    assert f4["value"] == fam.base.get("d_E", 55.0) and "default" in f4["why"]
    slab = RF.unobserved(fam.slab(), {n: 0.1 for n in fam.slab().names})
    assert slab == []                                     # no facets: no F4
    assert math.isinf(sig["k2"])


# --------------------------------------------------------------------------------------------- fix round 1 (2026-10-06)
def test_sigma_status_separates_flat_from_band_bound_and_non_minimum(monkeypatch):
    """sigma_report: a flat objective (sigma inf) is the only UNOBSERVED case; a parameter on its band bound (NaN) or
    at negative curvature (NaN, the fit is not at a minimum) keeps its fitted value and is listed in sigma_flags."""
    fam = RF.prada_family(RF.prada_base_params(), free=(("x_in0", 3.0), ("x_in_ze", 3.0), ("r_C", (0.0, 7.0)),
                                                        ("rise", (-0.6, -0.02))))
    x = fam.x0()
    x[2] = 7.0                                                   # r_C on its upper band bound
    x0 = x.copy()

    def fake_many(ctx, specs, details=False, pool=None):
        out = []
        for v in specs:
            d = np.asarray(v, float) - x0
            val = 3.0 * d[0] ** 2 + 0.0 * d[1] + 0.5 * d[2] - 2.0 * d[3] ** 2
            out.append((val, {"penalty": 0.0, "penalty_front_bound": 0.0, "penalty_dn": 0.0}) if details else val)
        return out
    monkeypatch.setattr(RF, "safe_spec", lambda family, v: np.asarray(v, float))
    monkeypatch.setattr(RF, "objective_many", fake_many)
    rep = RF.sigma_report(None, fam, x)
    assert rep["status"] == {"x_in0": "ok", "x_in_ze": "flat", "r_C": "at_band_bound", "rise": "not_at_minimum"}
    assert math.isfinite(rep["sigma"]["x_in0"]) and math.isinf(rep["sigma"]["x_in_ze"])
    assert math.isnan(rep["sigma"]["r_C"]) and math.isnan(rep["sigma"]["rise"])
    un = [u["parameter"] for u in RF.unobserved(fam, rep["sigma"], x, rep["status"])]
    assert "x_in_ze" in un and "r_C" not in un and "rise" not in un and "x_in0" not in un
    flags = {f["parameter"]: f["status"] for f in RF.sigma_flags(fam, rep, x)}
    assert flags == {"r_C": "at_band_bound", "rise": "not_at_minimum"}
    assert RF.sigma(None, fam, x)["x_in0"] == rep["sigma"]["x_in0"]


def test_s1_stop_i_follows_the_frozen_dependency_table():
    """§8 S1 / §9 STOP-I: a failed S1 recovery that R2 / R3 read stops the proof unless the OWNER waived it; the table
    is a fact of the code (who reads what), so every S1 quantity, theta and the outline included, is read by R2 / R3."""
    import json
    from glasses3d.tests.proofs import wp6_prada as P
    deps = json.loads(P.S1_DEPS.read_text(encoding="utf-8"))
    assert set(deps["depends"]) == set(RR.S1_RECOVERY)
    assert all(v.get("R2") or v.get("R3") for v in deps["depends"].values())
    assert {"R2", "R3"} <= set(deps["depends"]["theta_deg"])
    rec = {k: 0.0 for k in RR.S1_RECOVERY}
    rec.update(theta_deg=3.43, outline_mm=0.2063)
    si = P.s1_stop_i(rec, RR.S1_RECOVERY, deps, {})
    assert si["stop_i"] == ["outline_mm", "theta_deg"] and not si["waived_by_owner"]
    si = P.s1_stop_i(rec, RR.S1_RECOVERY, deps, {"theta_deg": "owner: proceed"})
    assert si["stop_i"] == ["outline_mm"] and si["waived_by_owner"] == {"theta_deg": "owner: proceed"}
    rec["r_C_mm"] = float("nan")                                  # a non-finite recovery fails too
    assert "r_C_mm" in P.s1_stop_i(rec, RR.S1_RECOVERY, deps, {})["stop_i"]
    assert P.s1_stop_i({k: 0.0 for k in RR.S1_RECOVERY}, RR.S1_RECOVERY, deps, {})["stop_i"] == []
    assert P.parse_waivers(["theta_deg=owner said go"]) == {"theta_deg": "owner said go"}
    for bad in (["theta=go"], ["theta_deg="], ["theta_deg"]):
        with pytest.raises(SystemExit):
            P.parse_waivers(bad)


def test_gates_refuse_cameras_not_refit_to_the_graded_shape():
    from glasses3d.tests.proofs import wp6_prada_stages as PS
    with pytest.raises(ValueError):
        PS._compare_cache({"fits": {}, "final_camera_block": False, "fit_version": "fit-1"}, "candidate")
    assert PS._compare_cache({"fits": {}, "final_camera_block": True}, "candidate") == {}


def test_compare_keeps_a_supplied_rear_fit():
    """compare_temple_views grades a model's supplied 06 fit as given (fit_temple's final camera block); only a model
    without one gets its family's best camera."""
    sj = RF.synthetic_setup(FX.prada_default(), shape=(280, 450))
    m = sj.truth
    cam_best = sj.true_cams["06"]
    other = Camera(cam_best.yaw + 1.0, cam_best.pitch, cam_best.roll, cam_best.perspective, cam_best.scale,
                   cam_best.center_x + 3.0, cam_best.center_y)
    fam = {"rows": [{"alpha": 0.0, "camera": cam_best.to_dict(), "loss": 0.1, "guard_mm": 0.0,
                     "pitch_at_bound": False, "refit": {"kept": True}}], "family": [0.0], "loss_min": 0.1,
           "stop_r": False}
    fits = {"supplied": {"06_family": fam, "06": {"camera": other, "theta": {1: 0.0, -1: 0.0}}},
            "from family": {"06_family": fam}}
    rep = RV.compare_temple_views(sj, {"supplied": m, "from family": m}, ["06"], fits=fits, log=lambda s: None)
    got = rep["views"]["06"]["models"]
    assert got["supplied"]["camera"] == other.to_dict()
    assert got["from family"]["camera"] == cam_best.to_dict()
    assert got["supplied"]["refit"] is None                       # a supplied fit without a refit record is fine


REF_DATA = Path(__file__).resolve().parents[1] / "data" / "reference" / "prada" / "manifest.json"


@pytest.mark.skipif(not REF_DATA.exists(), reason="Prada reference data not imported")
def test_photo_decal_mask_finds_the_temple_decals_by_geometry():
    """relief_check.decal_parts (§3 decal rule, shared with g3d.faceted_temple): on the delivered Prada GLB it finds
    three decals per temple side by geometry (two open hallmarks, the closed ribbon and shield whose box is not thin),
    every one within 0.1 mm of its temple's surface; the hinge leaves beside the temples are not decals. The part
    names are read here only as the test's oracle."""
    from glasses3d.g3d_host import fit_views as FV
    dec = RC.decal_parts(FV.reference_files("prada")[FV.MODEL])
    parts = dec["parts"]
    assert sorted(p["side"] for p in parts) == [-1, -1, -1, 1, 1, 1]
    assert all(0.0 <= p["signed_mm"][0] <= p["signed_mm"][1] <= 0.1 for p in parts)
    assert sum(not p["open"] and p["bbox_min_mm"] > 0.3 for p in parts) == 2          # the ribbons
    assert all(("hallmark" in p["node"] or "ribbon" in p["node"]) for p in parts)     # oracle only
    assert all(("right" in p["node"]) == (p["side"] > 0) for p in parts)             # +X = Astra's "right"


# --------------------------------------------------------------------------------------------- fix round 2 (2026-10-06)
def test_sigma_is_the_photo_terms_and_an_acting_penalty_is_its_own_status(monkeypatch):
    """sigma_report reads the PHOTO terms only (objective minus its penalties): a penalty acting along a parameter
    (changing across its stencil) is "penalty_active", never "not converged", and its sigma is the photo terms' own
    (NaN when their curvature is not positive); a penalty active at x but constant along a parameter (a front-bound
    overshoot that parameter does not move) leaves that parameter's photo status alone; a refused stencil point is
    "infeasible_stencil". The relief sheet shows only status-ok sigmas."""
    fam = RF.prada_family(RF.prada_base_params(), free=(("x_in0", 3.0), ("x_in_ze", 3.0), ("r_C", (0.0, 7.0)),
                                                        ("rise", (-0.6, -0.02))))
    x = fam.x0()
    x0 = x.copy()
    h_rise = 0.05 * (-0.02 + 0.6)

    def fake_spec(family, v):
        v = np.asarray(v, float)
        return None if v[3] > x0[3] + 0.5 * h_rise else v              # x + h on rise: a refusal cliff

    def fake_many(ctx, specs, details=False, pool=None):
        out = []
        for v in specs:
            if v is None:
                out.append((RF.INFEASIBLE, {"refused": "builder"}) if details else RF.INFEASIBLE)
                continue
            d = np.asarray(v, float) - x0
            photo = 3.0 * d[0] ** 2 + 2.0 * d[1] ** 2 - 1.0 * d[2] ** 2 + 1.0 * d[3] ** 2
            pen_fb = 2.0 + 50.0 * max(0.0, d[1])             # overshoot at x (constant in x_in0) + a wall in x_in_ze
            pen_dn = 50.0 * max(0.0, d[2])                    # dn wall along r_C
            tot = photo + pen_fb + pen_dn
            out.append((tot, {"penalty": pen_fb + pen_dn, "penalty_front_bound": pen_fb, "penalty_dn": pen_dn})
                       if details else tot)
        return out
    monkeypatch.setattr(RF, "safe_spec", fake_spec)
    monkeypatch.setattr(RF, "objective_many", fake_many)
    rep = RF.sigma_report(None, fam, x)
    assert rep["status"] == {"x_in0": "ok", "x_in_ze": "penalty_active", "r_C": "penalty_active",
                             "rise": "infeasible_stencil"}
    assert rep["sigma"]["x_in0"] == pytest.approx(math.sqrt(2 * RF.NOISE_LOSS / 6.0), rel=1e-6)
    assert rep["sigma"]["x_in_ze"] == pytest.approx(math.sqrt(2 * RF.NOISE_LOSS / 4.0), rel=1e-6)   # photo only
    assert math.isnan(rep["sigma"]["r_C"]) and math.isnan(rep["sigma"]["rise"])
    assert rep["penalty_at_x"]["front_bound"] == pytest.approx(2.0)
    flags = {f["parameter"]: f for f in RF.sigma_flags(fam, rep, x)}
    assert set(flags) == {"x_in_ze", "r_C", "rise"}
    assert "not converged" not in flags["x_in_ze"]["why"] and flags["x_in_ze"]["penalty"]["plus"]["front_bound"] > 2.0
    assert flags["r_C"]["penalty"]["plus"]["dn"] > 0.0 and flags["rise"]["status"] == "infeasible_stencil"
    assert [u["parameter"] for u in RF.unobserved(fam, rep["sigma"], x, rep["status"])][1:] == []    # F4 tilt only
    rs = RC.relief_sheet(FX.prada_default(), None, x=dict(zip(fam.names, map(float, x))), sigma=rep["sigma"],
                         sigma_status=rep["status"])
    assert rs["sigma_shown"] == {"x_in0": True, "x_in_ze": False, "r_C": False, "rise": False}


def test_r2_is_not_judged_on_one_usable_view(synth):
    """§5 names 05 AND 01 as the crease views: a candidate misregistered in one of them (that view unusable) must not
    pass R2 on the other alone with the confirmed-crease clause empty; R2 fails as not judged, with the reason."""
    sj = synth
    segs = {}
    for key, near, reg in (("05", 1, 0.1), ("01", -1, 1.5)):
        cam, th = sj.true_cams[key], sj.truth_theta[key]
        r = RV.render_model(sj, sj.truth, key, cam, th, points=True, normals=True)
        segs[key] = RC.photo_crease_segments(sj, key, r, cam, th, near, registration_mm=reg)
    assert segs["05"]["usable"] and not segs["01"]["usable"]
    r2 = RC.r2_verdict(segs, [], segs["05"]["segments"])
    assert r2["views"]["05"]["pass"]                     # the usable view alone would pass ...
    assert not r2["pass"] and r2["judged"] is False      # ... but R2 is not judged without 01
    assert "01" in r2["why"] and "registered" in r2["why"]
    r2b = RC.r2_verdict({"05": segs["05"]}, [], segs["05"]["segments"])             # 01 missing altogether
    assert not r2b["pass"] and not r2b["judged"]
    both = dict(segs, **{"01": dict(segs["01"], usable=True)})
    assert RC.r2_verdict(both, [], segs["05"]["segments"])["judged"]


def test_s1_dependency_table_names_the_theta_pairs_and_r_b_is_not_fitted():
    """The frozen table cites the (view, side) pairs whose theta R2 / R3 read (S1 grades theta there); a quantity S1
    cannot test (r_B: not a free parameter) is listed as not fitted with its readers, never as a recovery pass."""
    import json
    from glasses3d.tests.proofs import wp6_prada as P
    deps = json.loads(P.S1_DEPS.read_text(encoding="utf-8"))
    assert deps["theta_pairs"] == {"R2": ["05+x", "01-x"], "R3": ["05+x", "02+x"]}
    assert "r_B" not in [n for n, _ in RF.PRADA_FREE]
    rec = {k: 0.0 for k in RR.S1_RECOVERY if k != "r_B_mm"}
    si = P.s1_stop_i(rec, RR.S1_RECOVERY, deps, {}, not_fitted={"r_B_mm": "not fitted (fixed at 0)"})
    assert si["stop_i"] == [] and "r_B_mm" not in si["verdict"]
    assert si["not_fitted"]["r_B_mm"]["read_by"] == ["R2", "R3"] and si["not_graded"] == []
    assert P.s1_stop_i(rec, RR.S1_RECOVERY, deps, {})["not_graded"] == ["r_B_mm"]
    assert P._pair("01-x") == ("01", -1) and P._pair("02+x") == ("02", 1)


PRADA_SCENE = Path(__file__).resolve().parents[2] / ("automation/data/blender_agent/studio/jobs/"
                                                     "8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend")


@pytest.mark.skipif(not PRADA_SCENE.exists(), reason="Prada scene not present")
def test_resume_refuses_a_changed_s1_dependency_table(tmp_path):
    import json
    from types import SimpleNamespace
    from glasses3d.tests.proofs import wp6_prada as P
    rules = {"path": str(P.RULES), "sha256": P.sha(P.RULES), "version": RR.RULES_VERSION,
             "s1_dependencies": {"path": str(P.S1_DEPS), "sha256": "0" * 64}}
    (tmp_path / "proof.json").write_text(json.dumps({"rules": rules, "stages": {}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="dependency table"):
        P.Proof(tmp_path, SimpleNamespace(resume=True))
    rules["s1_dependencies"]["sha256"] = P.sha(P.S1_DEPS)
    (tmp_path / "proof.json").write_text(json.dumps({"rules": rules, "stages": {"P0": {"pass": True}}}),
                                         encoding="utf-8")
    pf = P.Proof(tmp_path, SimpleNamespace(resume=True))
    pf.hb.stop()
    assert pf.rec["stages"] == {"P0": {"pass": True}}


def test_fit_retraces_before_every_camera_block(monkeypatch):
    """fit_temple (FIT_VERSION fit-3): the outline is re-traced BEFORE a camera block (at the previous block's
    cameras) and before the final block, so every block is fitted to the exact model the round's front bound, the
    shape step and the gates read; round 0 without cameras keeps the caller's outline."""
    import copy
    fam = RF.prada_family(RF.prada_base_params())
    calls = []

    class FakeModel:
        def __init__(self, spec):
            self.marker = spec["outline"]["top"][0][1]

        @staticmethod
        def from_spec(job, spec, label):
            return FakeModel(spec)

    def fake_block(job, model, views, rear, log=None, pool=None):
        calls.append(("block", model.marker))
        return {k: {"camera": None, "theta": {1: 0.0, -1: 0.0}} for k in views}

    def retrace(family, x, fits):
        f2 = copy.deepcopy(family)
        f2.base["top"] = [[d, y + 1.0] for d, y in f2.base["top"]]
        calls.append(("retrace", f2.base["top"][0][1]))
        return f2
    monkeypatch.setattr(RF, "Model", FakeModel)
    monkeypatch.setattr(RF, "camera_block", fake_block)
    monkeypatch.setattr(RF, "build_rois", lambda job, mf, views: {})
    monkeypatch.setattr(RF, "objective", lambda ctx, spec, details=False, pool=None:
                        (1.0, {"front_bound": None}) if details else 1.0)
    monkeypatch.setattr(RF, "shape_step", lambda f, x, bounds, maxfev, prefetch=None: (x, f(x)))
    monkeypatch.setattr(RF, "sigma_report", lambda ctx, family, x, pool=None: {"sigma": {}, "status": {},
                                                                                "curvature": {}, "penalty": {}})
    m0 = fam.base["top"][0][1]
    res = RF.fit_temple(None, fam, views=("05", "01"), rounds=3, rear=False, retrace=retrace, log=lambda s: None)
    assert res["termination"] == "converged" and res["rounds_run"] == 2
    assert calls == [("block", m0), ("retrace", m0 + 1), ("block", m0 + 1), ("retrace", m0 + 2), ("block", m0 + 2)]
    assert res["spec"]["outline"]["top"][0][1] == m0 + 2 and res["final_outline_retraced"]
    assert [h["outline_retraced_before_block"] for h in res["history"]] == [False, True]
    assert res["fit_version"].startswith("fit-3")


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    V = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    T = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4],
         [1, 5, 7], [1, 7, 3]]
    return V, np.array(T)


def test_decal_planner_never_takes_a_hinge_part(tmp_path):
    """§3 "Hinge parts are never moved": a thin open part lying on the temple surface passes the decal rule, but when
    it sits at the hinge (a knuckle-sized, temple-role part there: measure.hinge_part_kind) it is a hinge part and
    never a decal; the same part further back is a decal. With or without the job's hinge (fallback: the temple's
    front and its knuckles' bbox centre)."""
    from glasses3d.g3d.temple import measure as MS
    parts = []
    for s in (1, -1):
        V, T = _box([62.0, -5.0, -147.0], [67.0, 5.0, -10.6])
        V[:, 0] *= s
        parts.append((f"temple {s:+d}", V, T if s > 0 else T[:, ::-1], {"partRole": "temple"}))

    def quad(z0):
        return (np.array([[67.05, 0.0, z0], [67.05, 2.0, z0], [67.05, 2.0, z0 + 2.0], [67.05, 0.0, z0 + 2.0]]),
                np.array([[0, 1, 2], [0, 2, 3]]))
    for name, z0 in (("plate at the hinge", -12.5), ("plate on the arm", -62.0)):
        V, T = quad(z0)
        parts.append((name, V, T, {"partRole": "temple"}))
    glb = tmp_path / "hinge_decal.glb"
    _write_glb(glb, parts)
    assert MS.hinge_part_kind([67.05, 0, -12.5], [67.05, 2, -10.5], "temple", 1, 64.6, -10.6, 4) == "temple_knuckle"
    assert MS.hinge_part_kind([67.05, 0, -62.0], [67.05, 2, -60.0], "temple", 1, 64.6, -10.6, 4) is None
    for hinge in ({"z_mm": -10.6, "axis_xz_mm": [64.6, -10.1]}, None):
        dec = RC.decal_parts(glb, hinge=hinge)
        assert [p["node"] for p in dec["parts"]] == ["plate on the arm"], hinge
        assert [(h["node"], h["kind"]) for h in dec["hinge_parts"]] == [("plate at the hinge", "temple_knuckle")]


@pytest.mark.skipif(not REF_DATA.exists(), reason="Prada reference data not imported")
def test_delivered_hinge_parts_are_never_decals():
    """On the delivered Prada GLB the hinge parts (knuckles, top pins, temple mounting leaves: measure.hinge_part_kind)
    are recorded and excluded before the decal rule runs; the decals stay the six of the photo mask."""
    from glasses3d.g3d_host import fit_views as FV
    dec = RC.decal_parts(FV.reference_files("prada")[FV.MODEL], hinge=RV.Job("prada").hinge)
    hp = dec["hinge_parts"]
    assert sum(h["kind"] == "leaf" for h in hp) == 2 and sum(h["kind"].endswith("knuckle") for h in hp) == 12
    names = {h["node"] for h in hp}
    assert not names & {p["node"] for p in dec["parts"]} and len(dec["parts"]) == 6
    assert all(("mounting leaf" in h["node"]) == (h["kind"] == "leaf") for h in hp)          # oracle only
